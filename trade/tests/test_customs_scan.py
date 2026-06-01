"""Tests for the 전 chapter 급변 scanner (trade/customs_scan.py)."""
import sqlite3
import unittest

from trade import customs_scan as cs


def _rows(*specs):
    """specs: (hs, name, prev, curr) → two monthly leaf rows each."""
    out = []
    for hs, name, prev, curr in specs:
        out.append({"hs_code": hs, "name": name, "year_month": "202603",
                    "exp_dlr": prev, "imp_dlr": 0})
        out.append({"hs_code": hs, "name": name, "year_month": "202604",
                    "exp_dlr": curr, "imp_dlr": 0})
    return out


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.rows = _rows(
            ("1111111111", "big", 100_000_000, 130_000_000),   # +30%, Δ+30M
            ("2222222222", "huge", 1_000_000_000, 1_050_000_000),  # +5%, Δ+50M
            ("3333333333", "tiny", 500, 900),                  # +80%, Δ+400
            ("4444444444", "drop", 100, 50),                   # -50%
            ("5555555555", "newp", 0, 200_000_000),            # prev0, Δ+200M
        )
        # a chapter-aggregate (non-10-digit) row must be ignored
        self.rows.append({"hs_code": "11", "name": "chap",
                          "year_month": "202604", "exp_dlr": 9, "imp_dlr": 0})

    def test_build_series_drops_non_leaf(self):
        leaves = cs.build_series(self.rows)
        self.assertNotIn("11", leaves)
        self.assertEqual(len(leaves), 5)

    def test_rate_is_increases_above_threshold(self):
        # rate_min_usd=0 isolates the pct/threshold logic from the value floor.
        ranked = cs.rank(cs.build_series(self.rows), top_n=30,
                         pct_threshold=30, rate_min_usd=0)
        codes = {m["hs_code"] for m in ranked[cs.SECTION_RATE]}
        # +30 and +80 qualify; +5, -50, prev0 do not
        self.assertEqual(codes, {"1111111111", "3333333333"})

    def test_rate_sorted_by_pct_desc(self):
        ranked = cs.rank(cs.build_series(self.rows), rate_min_usd=0)
        self.assertEqual(ranked[cs.SECTION_RATE][0]["hs_code"], "3333333333")

    def test_rate_floor_excludes_small_lines(self):
        # 'tiny' (+80% but curr=$900) must be filtered by the export floor,
        # while 'big' (+30%, curr=$130M) survives. The 💵 amount section is
        # NOT floored, so 'tiny' can still appear there if its Δ ranked —
        # here it won't (Δ$400 is last), but the point is rate excludes it.
        ranked = cs.rank(cs.build_series(self.rows), rate_min_usd=50_000_000)
        rate_codes = {m["hs_code"] for m in ranked[cs.SECTION_RATE]}
        self.assertIn("1111111111", rate_codes)       # $130M survives
        self.assertNotIn("3333333333", rate_codes)    # $900 floored out

    def test_amount_sorted_by_delta_desc(self):
        ranked = cs.rank(cs.build_series(self.rows))
        codes = [m["hs_code"] for m in ranked[cs.SECTION_AMOUNT]]
        self.assertEqual(codes[:3], ["5555555555", "2222222222", "1111111111"])

    def test_topn_cap(self):
        ranked = cs.rank(cs.build_series(self.rows), top_n=1)
        self.assertEqual(len(ranked[cs.SECTION_AMOUNT]), 1)
        self.assertLessEqual(len(ranked[cs.SECTION_RATE]), 1)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cs.init_db(self.conn)
        self.ranked = cs.rank(cs.build_series(_rows(
            ("1111111111", "a", 100_000_000, 200_000_000),
            ("2222222222", "b", 1_000_000_000, 1_050_000_000),
        )))

    def test_first_run_baseline_silent(self):
        cs.store_live(self.conn, self.ranked)
        self.assertEqual(cs.eval_new_entrants(self.conn, self.ranked), [])

    def test_archive_upsert_no_dup_growth(self):
        cs.upsert_archive(self.conn, self.ranked)
        n1 = len(cs.get_archive(self.conn))
        cs.upsert_archive(self.conn, self.ranked)
        self.assertEqual(len(cs.get_archive(self.conn)), n1)
        self.assertGreater(n1, 0)

    def test_new_month_entrant_alerts(self):
        cs.store_live(self.conn, self.ranked)
        cs.eval_new_entrants(self.conn, self.ranked)  # baseline
        fresh = cs.rank(cs.build_series([
            {"hs_code": "9999999999", "name": "fresh", "year_month": "202604",
             "exp_dlr": 10_000_000, "imp_dlr": 0},
            {"hs_code": "9999999999", "name": "fresh", "year_month": "202605",
             "exp_dlr": 50_000_000, "imp_dlr": 0},
        ]))
        cs.store_live(self.conn, fresh)
        entrants = cs.eval_new_entrants(self.conn, fresh)
        self.assertTrue(any(e["hs_code"] == "9999999999" for e in entrants))

    def test_live_replaced_archive_kept(self):
        cs.store_live(self.conn, self.ranked)
        cs.upsert_archive(self.conn, self.ranked)
        # new live snapshot with different items
        new = cs.rank(cs.build_series(_rows(
            ("7777777777", "c", 1_000_000, 9_000_000),
        )))
        cs.store_live(self.conn, new)
        cs.upsert_archive(self.conn, new)
        live_codes = {r["hs_code"] for r in cs.get_live(self.conn, cs.SECTION_AMOUNT)}
        self.assertIn("7777777777", live_codes)
        self.assertNotIn("1111111111", live_codes)   # replaced
        arch_codes = {r["hs_code"] for r in cs.get_archive(self.conn)}
        self.assertIn("1111111111", arch_codes)       # but archived


class FetchChapterTests(unittest.TestCase):
    """Regression for the ~60× inflation bug: data.go.kr could ignore
    pageNo and return page 1 forever; fetch_chapter must dedup by
    (hs_code, year_month) and stop when a page adds no new keys."""

    @staticmethod
    def _item(hs, nm, ym, e):
        return (f"<item><hsCode>{hs}</hsCode><statKor>{nm}</statKor>"
                f"<year>{ym[:4]}.{ym[4:]}</year><expDlr>{e}</expDlr>"
                f"<impDlr>0</impDlr><balPayments>0</balPayments>"
                f"<expWgt>0</expWgt><impWgt>0</impWgt></item>")

    def _resp(self, items):
        return ("<response><header><resultCode>00</resultCode></header>"
                "<body><items>" + "".join(items) + "</items></body></response>")

    def test_ignores_pageno_no_inflation(self):
        # Every page returns the SAME rows (API ignores pageNo).
        page = self._resp([
            self._item("8542321010", "디램", "202604", 9_248_046_034),
            self._item("8542321010", "디램", "202605", 9_000_000_000),
        ])
        calls = {"n": 0}
        def fake(url):
            calls["n"] += 1
            return page
        rows = cs.fetch_chapter("85", "202604", "202605",
                                key="dummy", fetcher=fake, max_pages=60)
        # 2 unique (leaf, month) rows, NOT 2×60; stops after the 2nd page.
        self.assertEqual(len(rows), 2)
        self.assertLessEqual(calls["n"], 3)
        leaves = cs.build_series(rows)
        self.assertEqual(
            leaves["8542321010"]["months"]["2026-04"]["exp_dlr"], 9_248_046_034)
        self.assertEqual(leaves["8542321010"]["name"], "디램")

    def test_real_pagination_accumulates(self):
        # API truly paginates: distinct full pages until a short one.
        full = [self._item(f"85{i:08d}", f"item{i}", "202604", 1_000_000 + i)
                for i in range(1000)]
        page2 = [self._item("8599999999", "마지막", "202604", 5_000_000)]
        pages = [self._resp(full), self._resp(page2), self._resp([])]
        seq = {"i": 0}
        def fake(url):
            r = pages[min(seq["i"], len(pages) - 1)]
            seq["i"] += 1
            return r
        rows = cs.fetch_chapter("85", "202604", "202604",
                                key="dummy", fetcher=fake, max_pages=60)
        codes = {r["hs_code"] for r in rows}
        self.assertEqual(len(rows), 1001)
        self.assertIn("8599999999", codes)

    def test_name_from_stat_kor(self):
        rows = [{"hs_code": "1234567890", "stat_kor": "황산",
                 "year_month": "2026-04", "exp_dlr": 1, "imp_dlr": 0}]
        self.assertEqual(cs.build_series(rows)["1234567890"]["name"], "황산")


class LatestMoveTests(unittest.TestCase):
    """Regression for the 2026-06-01 'live wiped' bug: data.go.kr returns
    future/current months with expDlr=0 before they confirm, so the move
    must compare the latest two months that actually HAVE data, not the
    two calendar-latest."""

    def test_skips_zero_export_future_months(self):
        # 3·4월 confirmed; 5·6월 returned but still zero (not yet confirmed).
        months = {
            "2026-03": {"exp_dlr": 100_000_000, "imp_dlr": 0},
            "2026-04": {"exp_dlr": 130_000_000, "imp_dlr": 0},
            "2026-05": {"exp_dlr": 0, "imp_dlr": 0},
            "2026-06": {"exp_dlr": 0, "imp_dlr": 0},
        }
        mv = cs._latest_move(months)
        self.assertIsNotNone(mv)
        # compares 4월 vs 3월, NOT 6월(0) vs 5월(0)
        self.assertEqual(mv["year_month"], "2026-04")
        self.assertEqual(mv["curr"], 130_000_000)
        self.assertEqual(mv["prev"], 100_000_000)
        self.assertAlmostEqual(mv["pct"], 30.0)

    def test_only_trailing_zeros_returns_none(self):
        # All-zero (or only one real month then trailing zeros) → no move.
        months = {
            "2026-04": {"exp_dlr": 100_000_000, "imp_dlr": 0},
            "2026-05": {"exp_dlr": 0, "imp_dlr": 0},
            "2026-06": {"exp_dlr": 0, "imp_dlr": 0},
        }
        # trailing 5·6월 trimmed → only 4월 left → < 2 months → None
        self.assertIsNone(cs._latest_move(months))

    def test_new_export_in_latest_month_ranks(self):
        # 0 → $50M with the value in the NEWEST month (not trailing zero)
        # is a real new-export surge and must rank (prev=0 → pct None,
        # delta=+50M).
        months = {
            "2026-05": {"exp_dlr": 0, "imp_dlr": 0},
            "2026-06": {"exp_dlr": 50_000_000, "imp_dlr": 0},
        }
        mv = cs._latest_move(months)
        self.assertIsNotNone(mv)
        self.assertEqual(mv["delta"], 50_000_000)
        self.assertIsNone(mv["pct"])

    def test_month_rollover_keeps_real_surge(self):
        # The exact 2026-06-01 scenario: a real April surge must survive
        # ranking even when the scan window also pulled empty 5·6월.
        rows = []
        for ym, e in [("2026-03", 100_000_000), ("2026-04", 200_000_000),
                      ("2026-05", 0), ("2026-06", 0)]:
            rows.append({"hs_code": "1111111111", "stat_kor": "디램",
                         "year_month": ym, "exp_dlr": e, "imp_dlr": 0})
        ranked = cs.rank(cs.build_series(rows), rate_min_usd=0)
        codes = {m["hs_code"] for m in ranked[cs.SECTION_AMOUNT]}
        self.assertIn("1111111111", codes)

    def test_rank_unifies_reference_month(self):
        # Item A confirmed through 4월, item B only through 3월. The panel
        # must show ONE reference month (the global latest, 2026-04) and
        # drop B so rows don't mix 4월/3월.
        rows = []
        for ym, e in [("2026-02", 100_000_000), ("2026-03", 100_000_000),
                      ("2026-04", 200_000_000)]:
            rows.append({"hs_code": "1111111111", "stat_kor": "A",
                         "year_month": ym, "exp_dlr": e, "imp_dlr": 0})
        # B has no 4월 data → its latest move is 3월
        for ym, e in [("2026-02", 50_000_000), ("2026-03", 90_000_000)]:
            rows.append({"hs_code": "2222222222", "stat_kor": "B",
                         "year_month": ym, "exp_dlr": e, "imp_dlr": 0})
        ranked = cs.rank(cs.build_series(rows), rate_min_usd=0)
        all_rows = ranked[cs.SECTION_AMOUNT] + ranked[cs.SECTION_RATE]
        # every ranked row is 2026-04; B (3월-latest) excluded
        self.assertTrue(all(m["year_month"] == "2026-04" for m in all_rows))
        codes = {m["hs_code"] for m in ranked[cs.SECTION_AMOUNT]}
        self.assertIn("1111111111", codes)
        self.assertNotIn("2222222222", codes)


if __name__ == "__main__":
    unittest.main()
