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

    def _series_25mo(self, base, latest):
        # 24 months 2024 + jump in 2026-04 → YoY defined
        d = {}
        for i in range(24):
            y = 2024 + (i // 12); m = (i % 12) + 1
            d[f"{y}-{m:02d}"] = base
        d["2026-04"] = latest
        return d

    def test_summary_board(self):
        # 반도체 huge YoY → 초고성장 box; board chips present
        by = {"반도체": self._series_25mo(11_000_000_000, 31_000_000_000)}
        html = industry.render_industry_html(by)
        self.assertIn("ind-summary-grid", html)      # 분류 보드
        self.assertIn("ind-sbox-hot", html)          # 초고성장 박스
        self.assertIn("ind-mini-chip", html)         # %칩
        self.assertIn("분류 기준", html)              # explainer
        self.assertIn("ind-deriv-grid", html)        # 가속/둔화 미분 보드

    def test_import_signal_box(self):
        # 수입 YoY 급증 → 생산 선행신호 박스
        exp = {"반도체": self._series_25mo(11_000_000_000, 31_000_000_000)}
        imp = {"반도체": self._series_25mo(1_000_000_000, 3_000_000_000)}
        # store/load imports roundtrip
        industry.store(self.conn, exp, imp)
        self.assertEqual(
            industry.load_stored_imports(self.conn)["반도체"]["2026-04"],
            3_000_000_000)
        html = industry.render_industry_html(exp, imp)
        self.assertIn("ind-sbox-imp", html)        # 수입 급증 박스
        self.assertIn("수입 급증", html)
        # backward compat: no imports → no box
        self.assertNotIn("ind-sbox-imp", industry.render_industry_html(exp))

    def test_import_box_shows_mti_driver(self):
        # 수입 급증 산업(반도체) 옆에 그 수입을 끌어올린 세부품목(MTI) 드라이버
        # '← 기타 메모리반도체'가 붙어야 함. 배포 경로 그대로 store→load→render
        # 라운드트립으로 검증 — '드라이버 사라진 듯' 혼선(실은 stale HTML) 재발 방지.
        exp = {"반도체": self._series_25mo(11_000_000_000, 31_000_000_000)}
        imp = {"반도체": self._series_25mo(1_000_000_000, 3_000_000_000)}
        # store_mti는 by_mti(수출) 키 행에만 import_json을 붙이므로 둘 다 필요
        mti = {"831190": {"name": "기타 메모리반도체", "industry": "반도체",
                          "months": self._series_25mo(800_000_000, 1_000_000_000)}}
        mti_imp = {"831190": {"name": "기타 메모리반도체", "industry": "반도체",
                              "months": self._series_25mo(700_000_000, 2_200_000_000)}}
        industry.store(self.conn, exp, imp)
        industry.store_mti(self.conn, mti, mti_imp)
        html = industry.render_industry_html(
            industry.load_stored(self.conn),
            industry.load_stored_imports(self.conn),
            industry.load_mti_stored(self.conn),
            industry.load_mti_imports(self.conn))
        self.assertIn("ind-imp-drv", html)        # 드라이버 주석 wrapper
        self.assertIn("기타 메모리반도체", html)    # 산업 수입을 끌어올린 세부품목

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

    def test_render_has_toggle_and_panels(self):
        # 14+ months so YoY exists for ≥2 months → ΔYoY (and thus a signal)
        months = {}
        for i in range(15):
            y = 2024 + (i // 12); m = (i % 12) + 1
            months[f"{y}-{m:02d}"] = 11_000_000_000
        # latest two months grow → YoY rises both → ΔYoY defined
        months["2025-03"] = 20_000_000_000
        months["2025-04"] = 31_000_000_000
        html = industry.render_industry_html({"반도체": months})
        self.assertIn("data-ind-view='monthly'", html)
        self.assertIn("data-ind-view='ttm'", html)
        self.assertIn("ind-monthly", html)
        self.assertIn("ind-ttm", html)
        self.assertIn("ind-summary", html)       # 요약문
        self.assertIn("ind-signal", html)        # ΔYoY 신호
        # reference-style full layout: 2-up charts + YoY bars + raw table
        self.assertIn("ind-row", html)           # meta | chart1 | chart2 (3-col)
        self.assertIn("ind-bar-pos", html)       # YoY 성장률 막대
        self.assertIn("YoY 성장률", html)
        self.assertIn("월별 원자료", html)         # raw-data table
        self.assertIn("ind-table", html)


class IndicatorExtraTests(unittest.TestCase):
    def _mk(self, vals):
        # vals: list of (ym, exp) → dict
        return {ym: v for ym, v in vals}

    def test_ttm_and_ttm_yoy_need_24_months(self):
        # 25 months so the latest has TTM and TTM-YoY (needs 24 prior).
        months = {}
        for i in range(25):
            y = 2024 + (i // 12)
            mth = (i % 12) + 1
            months[f"{y}-{mth:02d}"] = 100
        s = industry.industry_series({"x": months})["x"]
        latest = s[-1]
        self.assertIsNotNone(latest["ttm"])       # 12-mo sum exists
        self.assertEqual(latest["ttm"], 1200)     # 100×12
        self.assertIsNotNone(latest["ttm_yoy"])   # 24+ months → defined
        self.assertAlmostEqual(latest["ttm_yoy"], 0.0)  # flat → 0%

    def test_momentum_3month_avg(self):
        by = {"x": {f"2025-{m:02d}": 100 for m in range(1, 13)}}
        by["x"]["2026-01"] = 130   # YoY +30
        s = industry.industry_series(by)["x"]
        mo = industry.momentum(s)
        self.assertIsNotNone(mo["yoy3"])

    def test_interpret_high_growth_decel(self):
        # latest YoY high but ΔYoY negative → 고성장 둔화 signal
        pts = [{"yoy": 60, "dyoy": 5}, {"yoy": 55, "dyoy": -5}]
        r = industry.interpret(pts)
        self.assertEqual(r["signal_label"], "고성장 둔화")

    def test_interpret_no_yoy(self):
        r = industry.interpret([{"yoy": None, "dyoy": None}])
        self.assertIn("부족", r["summary"])
        self.assertEqual(r["signal_label"], "")


class SubitemTests(unittest.TestCase):
    """B: MTI6 하위품목(D램·낸드 등) 집계 + 하위품목 TOP 섹션."""

    def _leaves(self, hs, base, latest):
        from trade import customs_scan
        rows = []
        for i in range(24):
            y = 2024 + (i // 12); m = (i % 12) + 1
            rows.append({"hs_code": hs, "stat_kor": "x",
                         "year_month": f"{y}-{m:02d}", "exp_dlr": base, "imp_dlr": 0})
        # consecutive 2026-03 so MoM(전월대비) for 2026-04 is defined
        rows.append({"hs_code": hs, "stat_kor": "x",
                     "year_month": "2026-03", "exp_dlr": base, "imp_dlr": 0})
        rows.append({"hs_code": hs, "stat_kor": "x",
                     "year_month": "2026-04", "exp_dlr": latest, "imp_dlr": 0})
        return customs_scan.build_series(rows)

    def test_aggregate_by_mti(self):
        # 디램 8542321010 → MTI 831110 'D램' under 반도체
        by = industry.aggregate_by_mti(self._leaves("8542321010", 1, 1))
        self.assertIn("831110", by)
        self.assertEqual(by["831110"]["name"], "D램")
        self.assertEqual(by["831110"]["industry"], "반도체")

    def test_subitem_section_ranks(self):
        by = industry.aggregate_by_mti(
            self._leaves("8542321010", 11_000_000_000, 31_000_000_000))
        html = industry.render_subitem_html(by)
        self.assertIn("하위품목 (MTI 세분)", html)
        self.assertIn("급등률", html)        # 랭킹표
        self.assertIn("급증액", html)
        self.assertIn("ind-card", html)      # TOP10 풀 카드
        self.assertIn("D램", html)
        self.assertIn("반도체", html)        # 산업 컬럼

    def test_subitem_mom_ranking_and_floor(self):
        # 급등률은 MoM(전월대비) 상위 · 수출 하한 $200M(2.0억), 두 표 공통 하한
        by = industry.aggregate_by_mti(
            self._leaves("8542321010", 1_000_000_000, 3_000_000_000))
        html = industry.render_subitem_html(by)
        self.assertIn("MoM↑ 상위", html)         # 급등률 헤더가 MoM 상위(고정컷 없음)
        self.assertIn("수출 ≥2.0억", html)         # 새 수출 하한 $200M (양 표 공통)
        self.assertIn("전월대비", html)           # 급증액 제목/컬럼 MoM
        self.assertIn("+200.0%", html)           # 3B vs 전월 1B = +200% MoM
        # 랭킹표 컬럼 헤더는 '전월대비'(MoM)여야 하고 'YoY' 컬럼은 없어야 함
        self.assertNotIn("<th>전월비YoY</th>", html)

    def test_subitem_no_fixed_rate_cut(self):
        # 고정 +30%컷 제거 — MoM +10%여도 수출 ≥2억이면 급등률에 노출
        by = industry.aggregate_by_mti(
            self._leaves("8542321010", 10_000_000_000, 11_000_000_000))
        html = industry.render_subitem_html(by)
        self.assertIn("📈 급등률", html)
        self.assertIn("+10.0%", html)            # (11B-10B)/10B = +10% MoM도 등장

    def test_subitem_floor_excludes_small_base(self):
        # 전월대비 +200%지만 수출이 하한($200M) 미만 → 급등률·급증액 둘 다 제외
        by = industry.aggregate_by_mti(
            self._leaves("8542321010", 10_000_000, 30_000_000))  # 0.3억 < 2.0억
        html = industry.render_subitem_html(by)
        self.assertNotIn("📈 급등률", html)        # 하한 미달로 급등률표 없음
        self.assertNotIn("💵 급증액", html)        # 급증액도 이제 수출 하한 적용

    def test_mti_store_load(self):
        import sqlite3
        by = industry.aggregate_by_mti(self._leaves("8542321010", 1, 1))
        conn = sqlite3.connect(":memory:")
        industry.store_mti(conn, by)
        back = industry.load_mti_stored(conn)
        self.assertEqual(back["831110"]["name"], "D램")
        conn.close()

    def test_empty_subitem_blank(self):
        self.assertEqual(industry.render_subitem_html({}), "")


if __name__ == "__main__":
    unittest.main()
