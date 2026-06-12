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

    def test_stat_row_provisional_label(self):
        # 상세 패널 최신월 옆 잠정/확정 () 라벨 (사용자 2026-06-12)
        by = {"반도체": self._series_25mo(11_000_000_000, 31_000_000_000)}
        html = industry.render_industry_html(by)
        self.assertIn("ind-mstatus", html)
        # 날짜 의존(오늘) 없이 둘 중 하나는 반드시 노출
        self.assertTrue("(잠정)" in html or "(확정)" in html)

    def test_stat_row_provisional_word_matches_status(self):
        from datetime import date
        # 2026-05 최신월: 6/15 전이면 잠정, 이후면 확정 (라벨 단어 = 상태)
        for today, word in ((date(2026, 6, 12), "잠정"),
                            (date(2026, 6, 20), "확정")):
            st = industry._month_status_label("2026-05", today=today)
            self.assertTrue(st.startswith(word), (today, st))

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
        self.assertIn("ind-sbox-imp", html)        # 수입 모멘텀 박스
        self.assertIn("수입 모멘텀", html)
        # backward compat: no imports → no box
        self.assertNotIn("ind-sbox-imp", industry.render_industry_html(exp))

    def test_import_box_divergence_ranks_lead_first(self):
        # 디스플레이(수입↑·수출↓)=⚡선행후보가, 반도체(둘다↑)=동행보다 위.
        exp = {"반도체": self._series_25mo(11_000_000_000, 31_000_000_000),       # 수출 강
               "디스플레이": self._series_25mo(5_000_000_000, 4_800_000_000)}      # 수출 약(−4%)
        imp = {"반도체": self._series_25mo(1_000_000_000, 3_000_000_000),          # 수입 +200%
               "디스플레이": self._series_25mo(1_000_000_000, 1_300_000_000)}       # 수입 +30%
        html = industry.render_industry_html(exp, imp)
        self.assertIn("⚡선행후보", html)
        self.assertIn("동행", html)
        # 괴리 큰 순 → 선행후보가 동행보다 먼저(태그는 수입박스에만 등장)
        self.assertLess(html.index("⚡선행후보"), html.index("동행"))

    def test_import_box_floor_excludes_small_import(self):
        # 산업 월 수입이 하한(1억$) 미만이면 수입 모멘텀 박스에서 제외 → 박스 없음
        exp = {"반도체": self._series_25mo(11_000_000_000, 31_000_000_000)}
        imp = {"반도체": self._series_25mo(20_000_000, 50_000_000)}   # 0.5억$ < 1억$
        html = industry.render_industry_html(exp, imp)
        self.assertNotIn("ind-sbox-imp", html)

    def test_intra_views_engine_and_rotation(self):
        # 디스플레이 산업 부진(−4%)인데 OLED ↑(+150%)·LCD ↓(−50%):
        # → 견인(OLED 반등엔진) + 교체·잠식(OLED↑ vs LCD↓)
        by_ind = {"디스플레이": self._series_25mo(5_000_000_000, 4_800_000_000)}
        by_mti = {
            "OLED": {"name": "OLED", "industry": "디스플레이",
                     "months": self._series_25mo(1_000_000_000, 2_500_000_000)},
            "LCD": {"name": "LCD", "industry": "디스플레이",
                    "months": self._series_25mo(3_000_000_000, 1_500_000_000)},
        }
        html = industry.render_industry_html(by_ind, None, by_mti)
        self.assertIn("산업 내부 동학", html)
        self.assertIn("산업 내 견인 품목", html)
        self.assertIn("산업 내 교체·잠식", html)
        self.assertIn("OLED", html)
        self.assertIn("LCD", html)
        self.assertIn("반등엔진", html)        # 산업 부진인데 OLED 초고성장

    def test_import_box_capital_good_badge(self):
        # 드라이버가 자본재(설비·장비)면 [자본재] 배지, 아니면 없음
        exp = {"반도체": self._series_25mo(11_000_000_000, 12_000_000_000)}
        imp = {"반도체": self._series_25mo(1_000_000_000, 3_000_000_000)}
        cap = {"732100": {"name": "반도체제조용장비", "industry": "반도체",
                          "months": self._series_25mo(500_000_000, 2_000_000_000)}}
        self.assertIn("ind-imp-cap",
                      industry.render_industry_html(exp, imp, None, cap))
        noncap = {"831190": {"name": "기타 메모리반도체", "industry": "반도체",
                             "months": self._series_25mo(500_000_000, 2_000_000_000)}}
        self.assertNotIn("ind-imp-cap",
                         industry.render_industry_html(exp, imp, None, noncap))

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

    def test_motie_banner_factored_and_optional(self):
        # 대시보드 순서 변경(2026-06-12): motie 배너 분리 — motie=False 면
        # 본문 제외(대시보드가 구분선 밑에 별도 삽입), 기본 True(아카이브).
        b = industry.motie_banner()
        self.assertIn("더 빠른 잠정치", b)
        by = {"반도체": self._series_25mo(11_000_000_000, 31_000_000_000)}
        self.assertNotIn("ind-motie", industry.render_industry_html(by, motie=False))
        self.assertIn("ind-motie", industry.render_industry_html(by))

    def test_dashboard_zone_order_and_emphasis(self):
        # 구분선 → motie → 월별 아카이브 순서 + 강조 pill + auto-update
        # 트리거 trade/*.py 확대 (stale dashboard-server 클래스 차단)
        # 절대경로 — 타 테스트의 chdir 누수에 면역 (2026-06-12)
        from pathlib import Path as _P
        _root = _P(__file__).resolve().parents[2]
        src = (_root / "trade" / "dashboard.py").read_text(encoding="utf-8")
        i = src.index("zone_div + industry.motie_banner()")
        self.assertIn("archive_link", src[i:i + 120])
        self.assertIn("motie=False", src)
        self.assertIn("1.5px solid var(--accent)", src)
        sh = (_root / "deploy" / "trade-auto-update.sh").read_text(encoding="utf-8")
        self.assertIn("^trade/[^/]+\\.py$", sh.replace("\\", "\\"))
        self.assertIn("trade/[^/]+", sh)

    def test_motie_provisional_link_banner(self):
        # 산업부 잠정 보도자료 '링크 배너' — 데이터는 안 긁고 원문 링크만, 산업 집계와 분리
        by = {"반도체": self._series_13mo(11_000_000_000, 31_000_000_000)}
        html = industry.render_industry_html(by)
        self.assertIn("ind-motie", html)
        self.assertIn("motie.go.kr", html)
        self.assertIn("더 빠른 잠정치", html)
        # 토픽바는 관세청 기준 + 동적 잠정/확정 라벨(_month_status_label,
        # 2026-06-12 두 단계 표기). 날짜 의존 없이 둘 중 하나가 노출.
        self.assertIn("관세청", html)
        self.assertTrue("확정(" in html or "잠정(" in html)

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


class ImportDirectionCardsTests(unittest.TestCase):
    """수입 카드 전체셋 (사용자 2026-06-13 '수입도 똑같은 식으로 하위품목
    까지') — [수출|수입] 토글, 수입 블록 = 동일 구조(보드·카드·MTI 하위
    품목), 기본 숨김, 라벨 수입액/수입."""

    @staticmethod
    def _data():
        me = {f"2024-{m:02d}": 1_000_000_000 + m * 10_000_000 for m in range(1, 13)}
        me.update({f"2025-{m:02d}": 1_200_000_000 + m * 10_000_000 for m in range(1, 13)})
        mi = {k: int(v * 0.7) for k, v in me.items()}
        mti_e = {"741000": {"months": dict(me), "industry": "자동차부품", "name": "전장부품"}}
        mti_i = {"741000": {"months": dict(mi), "industry": "자동차부품", "name": "전장부품"}}
        return {"자동차부품": me}, {"자동차부품": mi}, mti_e, mti_i

    def test_dual_blocks_and_labels(self):
        # 2026-06-13 2차: 기본 = 전체(수출→구분선→수입 모두 표시),
        # [전체|수출|수입] 3버튼. 수입모멘텀 박스는 수출 블록 전용
        # (수입 블록 자기비교 버그 fix).
        e, i, me, mi = self._data()
        html = industry.render_industry_html(e, i, me, mi, motie=False)
        for d in ("all", "exp", "imp"):
            self.assertIn(f"data-ind-dir='{d}'", html)   # 3버튼
        self.assertIn("ind-dir-divider", html)            # 사이 구분선
        imp = html[html.index("data-dir='imp'"):]
        self.assertNotIn("display:none", imp[:80])        # 기본 = 전체(표시)
        self.assertIn("수입액", imp)                      # 카드 라벨
        self.assertIn("<th>수입</th>", imp)               # MTI 하위품목 th
        self.assertIn("수입 ≥", imp)                      # 캡션 방향
        self.assertNotIn("수입 모멘텀", imp)              # cross-dir 박스 제외
        self.assertIn("ind-mti-row", imp)                 # 랭킹 행 확장
        exp = html[html.index("data-dir='exp'"):html.index("ind-dir-divider")]
        self.assertNotIn("수입액", exp)                   # 라벨 오염 0

    def test_no_import_data_no_toggle(self):
        e, _, me, _ = self._data()
        html = industry.render_industry_html(e, None, me, None, motie=False)
        self.assertNotIn("ind-dir-btn", html)            # 수입 없으면 토글 숨김
        self.assertIn("data-dir='exp'", html)

    def test_js_toggle_wired(self):
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[2] / "trade" / "dashboard.py"
               ).read_text(encoding="utf-8")
        self.assertIn(".ind-dir-btn", src)
        self.assertIn("dataset.indDir", src)


class MtiUnitPriceCompanyTests(unittest.TestCase):
    """품목 카드 단가($/kg) + 관련기업 (사용자 2026-06-13 텔레그램 채널
    미러 2단계). 중량은 이미 fetch 되던 필드 — API 호출 0, additive."""

    def test_build_series_keeps_weights(self):
        from trade import customs_scan
        leaves = customs_scan.build_series([
            {"hs_code": "8542321010", "stat_kor": "디램", "year_month": "2026-04",
             "exp_dlr": 100, "imp_dlr": 5, "exp_wgt": 20, "imp_wgt": 1}])
        fig = leaves["8542321010"]["months"]["2026-04"]
        self.assertEqual(fig["exp_wgt"], 20)
        self.assertEqual(fig["imp_wgt"], 1)

    def test_aggregate_pairs_weight_field(self):
        from trade import customs_scan
        rows = [{"hs_code": "8542321010", "stat_kor": "디램",
                 "year_month": "2026-04", "exp_dlr": 1000, "imp_dlr": 400,
                 "exp_wgt": 10, "imp_wgt": 4}]
        leaves = customs_scan.build_series(rows)
        exp = industry.aggregate_by_mti(leaves)
        imp = industry.aggregate_by_mti(leaves, field="imp_dlr")
        self.assertEqual(exp["831110"]["wgts"]["2026-04"], 10)  # exp↔exp_wgt
        self.assertEqual(imp["831110"]["wgts"]["2026-04"], 4)   # imp↔imp_wgt

    def test_attach_units_and_format(self):
        pts = [{"ym": "2025-04", "exp": 380}, {"ym": "2026-04", "exp": 413}]
        industry._attach_units(
            pts, {"202504": 100, "2026-04": 100, "2026-05": 0})
        self.assertAlmostEqual(pts[0]["unit"], 3.8)   # 'YYYYMM' 키 정규화
        self.assertAlmostEqual(pts[1]["unit"], 4.13)
        self.assertEqual(industry._unit_str(4.13), "$4.13/kg")
        self.assertEqual(industry._unit_str(1250.0), "$1,250/kg")
        self.assertEqual(industry._unit_str(0.4567), "$0.457/kg")
        # wgts 없음/0 → no-op (옛 스냅샷 graceful)
        pts2 = [{"ym": "2026-04", "exp": 10}]
        industry._attach_units(pts2, None)
        self.assertNotIn("unit", pts2[0])

    def test_mti_extras_unit_yoy_and_companies(self):
        node = {"name": "디램", "industry": "반도체"}
        pts = [{"ym": "2025-04", "exp": 380, "unit": 3.8},
               {"ym": "2026-04", "exp": 413, "unit": 4.13}]
        html = industry._mti_extras(node, pts, "수출")
        self.assertIn("수출 단가", html)
        self.assertIn("$4.13/kg", html)
        self.assertIn("+8.7%", html)        # (4.13−3.8)/3.8 = +8.7%
        self.assertIn("전년동월 $3.80/kg", html)
        self.assertIn("삼성전자", html)
        self.assertIn("큐레이션", html)     # 수동 큐레이션 표기 의무

    def test_mti_extras_graceful_absent(self):
        # 미수록 품목 + 중량 없음 → 빈 문자열 (카드 무변화)
        node = {"name": "정체불명품목xyz", "industry": "기타"}
        pts = [{"ym": "2026-04", "exp": 1}]
        self.assertEqual(industry._mti_extras(node, pts, "수출"), "")

    def test_subitem_card_shows_unit_row_and_companies(self):
        from trade import customs_scan
        rows = []
        for i in range(25):
            y, m = 2024 + (i // 12), (i % 12) + 1
            rows.append({"hs_code": "8542321010", "stat_kor": "디램",
                         "year_month": f"{y}-{m:02d}",
                         "exp_dlr": 1_000_000_000, "imp_dlr": 0,
                         "exp_wgt": 100_000, "imp_wgt": 0})
        by = industry.aggregate_by_mti(customs_scan.build_series(rows))
        html = industry.render_subitem_html(by)
        self.assertIn("단가($/kg)", html)    # 원자료표 단가 행
        self.assertIn("수출 단가", html)      # meta 단가 라인
        self.assertIn("삼성전자", html)       # 관련기업 칩 (D램)

    def test_store_roundtrip_preserves_wgts(self):
        import sqlite3
        from trade import customs_scan
        rows = [{"hs_code": "8542321010", "stat_kor": "디램",
                 "year_month": "2026-04", "exp_dlr": 100, "imp_dlr": 0,
                 "exp_wgt": 7, "imp_wgt": 0}]
        by = industry.aggregate_by_mti(customs_scan.build_series(rows))
        conn = sqlite3.connect(":memory:")
        industry.store_mti(conn, by)
        back = industry.load_mti_stored(conn)
        self.assertEqual(back["831110"]["wgts"]["2026-04"], 7)
        conn.close()


class MtiCompaniesTests(unittest.TestCase):
    """관련기업 큐레이션 — 우선순위(구체 키워드 먼저)·비매칭 가드."""

    def test_specific_before_generic(self):
        from trade.mti_companies import companies_for
        self.assertIn("한미반도체", companies_for("반도체제조용장비"))  # 장비 ≠ 칩
        self.assertNotIn("삼성전자", companies_for("반도체제조용장비"))
        self.assertIn("세방전지", companies_for("납축전지"))            # ≠ 이차전지
        self.assertIn("POSCO홀딩스", companies_for("아연도강판"))       # 강판 ≠ 아연금속
        self.assertNotIn("고려아연", companies_for("아연도강판"))
        self.assertIn("HD현대중공업", companies_for("선박용엔진"))      # 엔진 ≠ 조선3사
        self.assertNotIn("한화오션", companies_for("선박용엔진"))

    def test_known_and_unknown(self):
        from trade.mti_companies import companies_for
        self.assertEqual(companies_for("디램"), ["삼성전자", "SK하이닉스"])
        self.assertIn("현대제철", companies_for("H형강"))
        self.assertIn("심팩", companies_for("페로망간"))
        self.assertEqual(companies_for("정체불명품목"), [])
        self.assertEqual(companies_for(""), [])
