"""Industry aggregation + HSK-MTI bridge tests (phase 1)."""
import unittest

from trade import industry, mti_map


class MtiMapTests(unittest.TestCase):
    def test_real_linkage_loads(self):
        # The shipped TSV must load and contain the expected industries.
        m = mti_map.load()
        self.assertGreater(len(m), 10000)            # ~11,327 HS rows
        inds = mti_map.industries()
        for expected in ["반도체", "자동차", "이차전지", "화장품"]:
            self.assertIn(expected, inds)
        self.assertNotIn(mti_map.CATCH_ALL, inds)    # 기타 excluded

    def test_known_hs_maps_to_industry(self):
        # 디램 8542321010 → 반도체 (verified against the 2026 table)
        self.assertEqual(mti_map.industry_of("8542321010"), "반도체")

    def test_unmapped_returns_none(self):
        self.assertIsNone(mti_map.industry_of("0000000000"))
        self.assertIsNone(mti_map.industry_of(""))


class AggregationTests(unittest.TestCase):
    def setUp(self):
        # Two leaves in 반도체, one in 자동차, one unmapped — leaves shape
        # matches customs_scan.build_series output.
        self.leaves = {
            "8542321010": {"name": "디램", "months": {
                "2026-03": {"exp_dlr": 100, "imp_dlr": 0},
                "2026-04": {"exp_dlr": 200, "imp_dlr": 0}}},
            "8542311000": {"name": "MCO", "months": {
                "2026-03": {"exp_dlr": 50, "imp_dlr": 0},
                "2026-04": {"exp_dlr": 70, "imp_dlr": 0}}},
            "8703231010": {"name": "신차", "months": {
                "2026-04": {"exp_dlr": 1000, "imp_dlr": 0}}},
            "0000000000": {"name": "없음", "months": {
                "2026-04": {"exp_dlr": 9, "imp_dlr": 0}}},
        }

    def test_sums_member_leaves(self):
        by = industry.aggregate_by_industry(self.leaves)
        # 8542* both map to 반도체 → summed
        self.assertEqual(by["반도체"]["2026-04"], 270)   # 200+70
        self.assertEqual(by["반도체"]["2026-03"], 150)   # 100+50

    def test_unmapped_excluded(self):
        by = industry.aggregate_by_industry(self.leaves)
        # 0000000000 has no industry → not in any bucket
        self.assertEqual(sum(b.get("2026-04", 0) for b in by.values()),
                         270 + 1000)  # 반도체 + 자동차, NOT +9


class SeriesIndicatorTests(unittest.TestCase):
    def test_yoy_dyoy_ma(self):
        # 13 months so YoY and a ΔYoY exist; flat then a jump.
        by = {"테스트": {}}
        # 2025-01..2025-12 = 100 each; 2026-01 = 130 (YoY +30%)
        for mth in range(1, 13):
            by["테스트"][f"2025-{mth:02d}"] = 100
        by["테스트"]["2026-01"] = 130
        s = industry.industry_series(by)["테스트"]
        latest = s[-1]
        self.assertEqual(latest["ym"], "2026-01")
        self.assertAlmostEqual(latest["yoy"], 30.0)     # 130 vs 100
        # ma12 over 2025-02..2026-01 = (100*11 + 130)/12
        self.assertAlmostEqual(latest["ma12"], (100 * 11 + 130) / 12)

    def test_yoy_none_without_year_ago(self):
        by = {"x": {"2026-03": 100, "2026-04": 200}}
        s = industry.industry_series(by)["x"]
        self.assertIsNone(s[-1]["yoy"])     # no 2025-04
        self.assertIsNone(s[-1]["ma12"])    # < 12 months


class ClassifyTests(unittest.TestCase):
    def test_high_growth(self):
        self.assertEqual(
            industry.classify([{"yoy": 25.0, "dyoy": -2.0}]), "초고성장/강세")

    def test_turnaround(self):
        # below +20% YoY but accelerating (ΔYoY > 0)
        self.assertEqual(
            industry.classify([{"yoy": 5.0, "dyoy": 3.0}]), "턴어라운드 후보")

    def test_decline(self):
        self.assertEqual(
            industry.classify([{"yoy": 5.0, "dyoy": -3.0}]), "부진/재하락")
        self.assertEqual(
            industry.classify([{"yoy": -10.0, "dyoy": -1.0}]), "부진/재하락")

    def test_insufficient(self):
        self.assertEqual(industry.classify([]), "데이터부족")
        self.assertEqual(industry.classify([{"yoy": None, "dyoy": None}]),
                         "데이터부족")


class StoreRenderTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def _series_13mo(self, base, latest):
        d = {}
        for mth in range(4, 13):
            d[f"2025-{mth:02d}"] = base
        d["2026-01"] = base
        d["2026-02"] = base
        d["2026-03"] = base
        d["2026-04"] = latest
        return d

    def test_store_load_roundtrip(self):
        by = {"반도체": self._series_13mo(11_000_000_000, 31_000_000_000),
              "자동차": self._series_13mo(6_000_000_000, 5_700_000_000)}
        n = industry.store(self.conn, by)
        self.assertEqual(n, 2)
        back = industry.load_stored(self.conn)
        self.assertEqual(back["반도체"]["2026-04"], 31_000_000_000)

    def test_store_empty_is_noop(self):
        industry.store(self.conn, {"x": {"2026-04": 5}})
        n = industry.store(self.conn, {})       # empty must not wipe
        self.assertEqual(n, 0)
        self.assertEqual(len(industry.load_stored(self.conn)), 1)

    def test_render_has_chart_and_groups(self):
        by = {"반도체": self._series_13mo(11_000_000_000, 31_000_000_000)}
        html = industry.render_industry_html(by)
        self.assertIn("반도체", html)
        self.assertIn("ind-value-line", html)       # SVG export line
        self.assertIn("초고성장", html)               # classified hot (YoY huge)
        self.assertIn("<svg", html)

    def test_render_empty_returns_blank(self):
        self.assertEqual(industry.render_industry_html({}), "")


if __name__ == "__main__":
    unittest.main()
