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
