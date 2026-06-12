"""대시보드 E2E 렌더 스모크 — _build_html 파라미터/본문 이름 불일치
(NameError) 클래스 회귀 차단.

2026-06-12: render() 가 heatmap_html 을 만들고도 _build_html 에 안 넘겼고
시그니처에도 없어, 12:52 이후 5분 refresh 가 전부 NameError 크래시 →
index.html 동결 (순서/잠정/히트맵 미표시의 최종 진범). 기본값-only 호출이
본문 전체 이름 해석을 강제하므로 이 클래스가 다시는 조용히 못 들어온다."""
import unittest


class DashboardRenderSmokeTests(unittest.TestCase):
    def test_build_html_defaults_only(self):
        from trade import dashboard as d
        html = d._build_html([], [], {}, "")
        self.assertIsInstance(html, str)
        self.assertIn("<html", html.lower())

    def test_render_passes_heatmap_to_build(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "dashboard.py"
               ).read_text(encoding="utf-8")
        self.assertIn("industry_html, heatmap_html,", src)


if __name__ == "__main__":
    unittest.main()


class SearchAndLayoutTests(unittest.TestCase):
    """검색 확장(산업트렌드·히트맵) + 모멘텀 2×2 (사용자 2026-06-12)."""

    def test_search_filters_industry_and_heatmap(self):
        from trade import dashboard as d, heatmap
        rows = [{"hs_code": "8542310000", "name": "디램", "ref_ym": "2026-05",
                 "exp": 1000.0, "exp_pm": 900.0, "exp_py": 500.0,
                 "imp": 100.0, "imp_pm": 90.0, "imp_py": 50.0}]
        hm = heatmap.render_heatmap_html(rows)
        self.assertIn("window.hmFilter", hm)        # 전역 필터 노출
        self.assertIn("opacity", hm)                 # 비매칭 dim
        full = d._build_html([], [], {}, "", None, [], "", hm)
        self.assertIn("filterIndustryCards", full)   # 산업 카드 필터
        self.assertIn("산업 / HS", full)             # placeholder 동기

    def test_momentum_two_by_two(self):
        src = open(__file__.rsplit("/tests/", 1)[0] + "/dashboard.py",
                   encoding="utf-8").read()
        self.assertIn("repeat(2,minmax(0,1fr))", src)
        self.assertIn("grid-column:1/-1", src)       # 정렬바·노트 풀스팬

    def test_heatmap_js_no_broken_braces(self):
        # f-string 이중브레이스 누락 시 JS 가 '{{' 리터럴로 깨짐 — 영구 가드
        from trade import heatmap
        rows = [{"hs_code": "8542310000", "name": "디램", "ref_ym": "2026-05",
                 "exp": 1.0, "exp_pm": 1.0, "exp_py": 1.0,
                 "imp": 1.0, "imp_pm": 1.0, "imp_py": 1.0}]
        js = heatmap.render_heatmap_html(rows).split("<script>")[-1]
        # '}}' 는 정상 JS(객체리터럴+함수 인접 닫힘)에 존재 — 깨진
        # f-string 의 진짜 시그니처는 '{{' 리터럴 잔존.
        self.assertNotIn("{{", js)


class HeatmapIndustryGroupAndCSVTests(unittest.TestCase):
    """히트맵 [HS류|산업] 토글 (사용자 2026-06-13 '전기차는 자동차로' —
    HS 류는 관세 분류라 산업 관점과 어긋남 → HSK-MTI 연계표 병행 집계)
    + 산업트렌드·히트맵 CSV 내보내기."""

    _ROWS = [
        {"hs_code": "8473301000", "name": "디램 모듈", "ref_ym": "2026-05",
         "exp": 300.0, "exp_pm": 280.0, "exp_py": 200.0,
         "imp": 30.0, "imp_pm": 28.0, "imp_py": 20.0},
    ]

    def test_industries_tree_and_toggle(self):
        from trade import heatmap
        data = heatmap.build_heatmap_data(self._ROWS)
        self.assertIn("industries", data)
        self.assertTrue(data["industries"])         # 미매핑도 '기타(미매핑)' 그룹
        html = heatmap.render_heatmap_html(self._ROWS)
        self.assertIn('id="hm-grp"', html)          # [HS류|산업] 토글
        self.assertIn("window.hmCSV", html)         # CSV 익스포트
        self.assertNotIn("{{", html.split("<script>")[-1])

    def test_csv_button_tab_dispatch(self):
        from trade import dashboard as d, heatmap
        hm = heatmap.render_heatmap_html(self._ROWS)
        full = d._build_html([], [], {}, "", None, [], "", hm)
        self.assertIn("downloadIndustryCSV", full)
        self.assertIn("downloadRowsCSV", full)
        self.assertIn("tab==='heatmap'&&window.hmCSV", full)

    def test_industry_csv_payload_embedded(self):
        src = open(__file__.rsplit("/tests/", 1)[0] + "/dashboard.py",
                   encoding="utf-8").read()
        self.assertIn("ind-csv-data", src)
        self.assertIn("ind_csv + prov_zone_div", src)   # 임베드 배선
