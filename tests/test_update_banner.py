"""자동 업데이트 배너 회귀 (사용자 2026-06-29 '켜놔도 새 내용 자동으로 보이게').

콘텐츠 도착형 페이지(DART·블로그·레딧·DailyByte·부동산/청약·밸류체인)에 HEAD 폴링
→ Last-Modified 변경 시 '🆕 새 업데이트' 배너 주입. 메인/ASIA(이미 영역 swap)·
per-ticker 상세는 제외. 순수 주입 로직 + 스니펫 계약 고정."""
import unittest

import bot.dashboard as d


class TestInjectUpdateBanner(unittest.TestCase):
    def test_injects_before_body_close(self):
        out = d._inject_update_banner("<html><body><div>x</div></body></html>")
        self.assertIn("__updBanner", out)
        self.assertEqual(out.count("</body>"), 1)
        self.assertLess(out.index("__updBanner"), out.index("</body>"))

    def test_idempotent(self):
        once = d._inject_update_banner("<body>x</body>")
        self.assertEqual(d._inject_update_banner(once), once)   # 두 번 안 넣음

    def test_passthrough_without_body(self):
        self.assertEqual(d._inject_update_banner("<div>no body</div>"),
                         "<div>no body</div>")
        self.assertEqual(d._inject_update_banner(""), "")

    def test_snippet_uses_head_and_banner(self):
        # 계약: 본문 0(HEAD)·Last-Modified 비교·배너(자동 swap 아님 — 상태 보존).
        js = d._UPDATE_BANNER_JS
        self.assertIn("method:'HEAD'", js)
        self.assertIn("Last-Modified", js)
        self.assertIn("upd-banner", js)
        self.assertIn("document.hidden", js)          # 숨김 탭 skip(부하 가드)
        self.assertNotIn("innerHTML", js)             # 무단 swap 금지(상태 보존)

    def test_content_pages_wrap_render(self):
        # 6개 콘텐츠 페이지 regen 이 _inject_update_banner 로 감싸는지(소스 계약).
        src = open("bot/dashboard.py", encoding="utf-8").read()
        for fn in ("_render_daily_byte_page", "_render_realestate_page",
                   "_render_valuechain_page"):
            self.assertIn(f"_inject_update_banner(" + fn, src,
                          f"{fn} 가 배너 주입으로 감싸이지 않음")
        # 레딧·블로그는 lazy 분리로 튜플 언팩 후 주입(2026-07-03).
        self.assertIn("_inject_update_banner(_ri_html)", src)
        self.assertIn("_inject_update_banner(_bl_html)", src)
        self.assertIn("_inject_update_banner(_idx_html)", src)   # index 도 주입
        self.assertIn("_inject_update_banner(_df_html)", src)    # dart(lazy 분리)


if __name__ == "__main__":
    unittest.main()
