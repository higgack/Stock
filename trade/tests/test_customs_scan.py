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
        ranked = cs.rank(cs.build_series(self.rows), top_n=30, pct_threshold=30)
        codes = {m["hs_code"] for m in ranked[cs.SECTION_RATE]}
        # +30 and +80 qualify; +5, -50, prev0 do not
        self.assertEqual(codes, {"1111111111", "3333333333"})

    def test_rate_sorted_by_pct_desc(self):
        ranked = cs.rank(cs.build_series(self.rows))
        self.assertEqual(ranked[cs.SECTION_RATE][0]["hs_code"], "3333333333")

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


if __name__ == "__main__":
    unittest.main()
