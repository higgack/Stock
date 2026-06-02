"""trade.customs — 관세청 품목별 수출입실적 fetch/parse/cache tests.

The XML fixtures here are faithful to a LIVE 2026.01 response from
…/1220000/Itemtrade/getItemtradeList (dataset 15101609): the same tag
names (hsCode/statKor/year/expDlr/impDlr/balPayments/expWgt/impWgt) and
the same shape (one <item> per HS-leaf × month, year as 'YYYY.MM').

We pin the contract the signal layer depends on:
  - parse extracts every field, normalises year, coerces blanks to 0
  - a non-00 resultCode raises (so an error page is never cached as data)
  - aggregate_by_month sums leaves under a pinned prefix, recomputes 무역수지
  - month_over_month gives the change the ±30% alert will key on
  - the SQLite cache round-trips and reports first-seen months
"""

import tempfile
import unittest
from pathlib import Path

from trade import customs


# Real-shape response: HS 1902 family, two months so series logic is
# exercised. The 라면 leaf 1902301010 uses the exact live 2026.01 values
# (expDlr 129,673,809 / balPayments 128,437,194) so the parser is
# validated against real magnitudes, plus a fabricated 2026.02 month.
_XML_OK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header>
<body><items>
<item><balPayments>128437194</balPayments><expDlr>129673809</expDlr><expWgt>31427100</expWgt><hsCode>1902301010</hsCode><impDlr>1236615</impDlr><impWgt>292484</impWgt><statKor>라면</statKor><year>2026.01</year></item>
<item><balPayments>4144808</balPayments><expDlr>6355568</expDlr><expWgt>2087261</expWgt><hsCode>1902301090</hsCode><impDlr>2210760</impDlr><impWgt>640098</impWgt><statKor>기타</statKor><year>2026.01</year></item>
<item><balPayments>140000000</balPayments><expDlr>141000000</expDlr><expWgt>33000000</expWgt><hsCode>1902301010</hsCode><impDlr>1000000</impDlr><impWgt>300000</impWgt><statKor>라면</statKor><year>2026.02</year></item>
</items></body></response>"""

_XML_ERROR = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response><header><resultCode>30</resultCode><resultMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</resultMsg></header><body></body></response>"""

_XML_BLANKS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<response><header><resultCode>00</resultCode><resultMsg>정상서비스.</resultMsg></header>
<body><items>
<item><hsCode>1902301010</hsCode><statKor>라면</statKor><year>2026.01</year><expDlr></expDlr><impDlr>5</impDlr></item>
</items></body></response>"""


class TestParse(unittest.TestCase):
    def test_parses_real_shape(self):
        rows = customs.parse_response(_XML_OK)
        self.assertEqual(len(rows), 3)
        ramyeon = next(
            r for r in rows
            if r["hs_code"] == "1902301010" and r["year_month"] == "2026-01"
        )
        self.assertEqual(ramyeon["exp_dlr"], 129673809)
        self.assertEqual(ramyeon["imp_dlr"], 1236615)
        self.assertEqual(ramyeon["bal_payments"], 128437194)
        self.assertEqual(ramyeon["stat_kor"], "라면")

    def test_year_normalised(self):
        rows = customs.parse_response(_XML_OK)
        self.assertTrue(all("-" in r["year_month"] for r in rows))
        self.assertIn("2026-01", {r["year_month"] for r in rows})

    def test_error_code_raises(self):
        with self.assertRaises(customs.CustomsAPIError):
            customs.parse_response(_XML_ERROR)

    def test_blank_numeric_coerced_to_zero(self):
        rows = customs.parse_response(_XML_BLANKS)
        self.assertEqual(rows[0]["exp_dlr"], 0)
        self.assertEqual(rows[0]["imp_dlr"], 5)


class TestAggregate(unittest.TestCase):
    def setUp(self):
        self.rows = customs.parse_response(_XML_OK)

    def test_exact_leaf_isolates_one_line(self):
        agg = customs.aggregate_by_month(self.rows, "1902301010")
        self.assertEqual(agg["2026-01"]["exp_dlr"], 129673809)
        # the 1902301090 기타 leaf must NOT be folded in
        self.assertNotEqual(agg["2026-01"]["exp_dlr"], 129673809 + 6355568)

    def test_prefix_sums_leaves(self):
        # 190230 prefix rolls 라면 + 기타 together for 2026-01
        agg = customs.aggregate_by_month(self.rows, "190230")
        self.assertEqual(agg["2026-01"]["exp_dlr"], 129673809 + 6355568)
        # 무역수지 recomputed as exp - imp after the sum
        self.assertEqual(
            agg["2026-01"]["bal_payments"],
            (129673809 + 6355568) - (1236615 + 2210760),
        )

    def test_groups_by_month(self):
        agg = customs.aggregate_by_month(self.rows, "1902301010")
        self.assertEqual(set(agg), {"2026-01", "2026-02"})


class TestMoM(unittest.TestCase):
    def test_month_over_month_pct(self):
        series = [
            {"year_month": "2026-01", "exp_dlr": 100},
            {"year_month": "2026-02", "exp_dlr": 130},
        ]
        self.assertAlmostEqual(customs.month_over_month(series), 30.0)

    def test_none_when_single_point(self):
        self.assertIsNone(customs.month_over_month([{"exp_dlr": 100}]))

    def test_none_when_prev_zero(self):
        series = [{"exp_dlr": 0}, {"exp_dlr": 100}]
        self.assertIsNone(customs.month_over_month(series))


class TestFetchInjectsFetcher(unittest.TestCase):
    def test_fetch_uses_injected_fetcher(self):
        captured = {}

        def fake(url):
            captured["url"] = url
            return _XML_OK

        rows = customs.fetch(
            "1902301010", "202601", "202602", key="DUMMY", fetcher=fake
        )
        self.assertEqual(len(rows), 3)
        self.assertIn("hsSgn=1902301010", captured["url"])
        self.assertIn("strtYymm=202601", captured["url"])
        self.assertIn("serviceKey=DUMMY", captured["url"])

    def test_missing_key_raises(self):
        import os
        old = os.environ.pop("TRADE_DATA_GO_KR_KEY", None)
        try:
            with self.assertRaises(customs.CustomsAPIError):
                customs.fetch("1902", "202601", "202601", key="",
                              fetcher=lambda u: _XML_OK)
        finally:
            if old is not None:
                os.environ["TRADE_DATA_GO_KR_KEY"] = old


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "customs.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_upsert_reports_first_seen_then_refresh(self):
        agg = customs.aggregate_by_month(
            customs.parse_response(_XML_OK), "1902301010"
        )
        with customs.session(self.db) as conn:
            # first insert of 2026-01 → new
            self.assertTrue(
                customs.upsert_month(conn, "1902301010", "2026-01", agg["2026-01"])
            )
            # re-insert same month → not new (refresh, no alert)
            self.assertFalse(
                customs.upsert_month(conn, "1902301010", "2026-01", agg["2026-01"])
            )
            # a different month → new again
            self.assertTrue(
                customs.upsert_month(conn, "1902301010", "2026-02", agg["2026-02"])
            )

    def test_get_series_ascending(self):
        agg = customs.aggregate_by_month(
            customs.parse_response(_XML_OK), "1902301010"
        )
        with customs.session(self.db) as conn:
            customs.upsert_month(conn, "1902301010", "2026-02", agg["2026-02"])
            customs.upsert_month(conn, "1902301010", "2026-01", agg["2026-01"])
            series = customs.get_series(conn, "1902301010")
        self.assertEqual([r["year_month"] for r in series], ["2026-01", "2026-02"])
        # end-to-end: MoM over the cached series
        self.assertGreater(customs.month_over_month(series), 0)


class TestFormat(unittest.TestCase):
    def test_fmt_usd(self):
        # 억 달러($) 한국어 통일 — 1억$ = $100M
        self.assertEqual(customs.fmt_usd(129673809), "1.3억$")
        self.assertEqual(customs.fmt_usd(3417443000), "34.2억$")
        self.assertEqual(customs.fmt_usd(-5364818), "-0.05억$")
        self.assertEqual(customs.fmt_usd(304), "0.00억$")
        self.assertEqual(customs.fmt_usd(None), "0.00억$")

    def test_fmt_pct(self):
        self.assertEqual(customs.fmt_pct(30.25), "+30.2%")
        self.assertEqual(customs.fmt_pct(-12.0), "-12.0%")
        self.assertEqual(customs.fmt_pct(None), "—")


class TestSummaryRows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "customs.db"
        agg = customs.aggregate_by_month(
            customs.parse_response(_XML_OK), "1902301010"
        )
        with customs.session(self.db) as conn:
            for ym, a in sorted(agg.items()):
                customs.upsert_month(conn, "1902301010", ym, a)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pinned_with_data_has_latest_and_mom(self):
        with customs.session(self.db) as conn:
            rows = customs.summary_rows(conn, [("라면", "1902301010")])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertTrue(r["has_data"])
        self.assertEqual(r["year_month"], "2026-02")  # latest
        self.assertEqual(r["exp_dlr"], 141000000)
        self.assertIsNotNone(r["exp_mom"])             # 2 months → computable

    def test_pin_without_data_marked(self):
        with customs.session(self.db) as conn:
            rows = customs.summary_rows(conn, [("미수집품목", "8501")])
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["has_data"])

    def test_sort_data_first_then_value_desc(self):
        # Add a smaller-export pin and a no-data pin; order must be
        # big-export, small-export, then no-data last.
        small = customs.aggregate_by_month(
            customs.parse_response(_XML_OK), "1902301090"
        )
        with customs.session(self.db) as conn:
            for ym, a in sorted(small.items()):
                customs.upsert_month(conn, "1902301090", ym, a)
            rows = customs.summary_rows(conn, [
                ("기타", "1902301090"),
                ("라면", "1902301010"),
                ("없음", "9999"),
            ])
        self.assertEqual([r["item"] for r in rows], ["라면", "기타", "없음"])


if __name__ == "__main__":
    unittest.main()
