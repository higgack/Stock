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
