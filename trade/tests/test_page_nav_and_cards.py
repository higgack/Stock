"""수출입 하위 페이지 back-link · 빈 펼침 카드 회귀 (사용자 2026-08-16).

두 버그 모두 **한 페이지가 드러냈지만 전 페이지 문제**였다:
  · `href='index.html'` → NOAH 프록시가 302 로 종목분석 메인으로 보냄.
    2026-06-28 에 파일별로 고쳤다가 새 모듈 3개가 옛 패턴을 복사해 재발.
  · 펼침 본문이 비어도 `<details>` 를 만들어 '눌러도 아무것도 없는' 토글.
이 파일은 **모듈을 순회**해 두 계약을 강제한다 — 새 소스를 추가해도 자동 적용.
"""

import importlib
import re
import unittest

# (모듈, DB 오프너) — badonion 하위 페이지 전량.
#
# ⚠️ 예전엔 11개를 **손으로 나열**했다. 그러면 새 소스가 목록 밖에 있어
# 두 계약(back-link·빈 토글)이 아무도 안 보는 채로 배포된다(2026-08-19
# 미국 PPI 추가 때 실제로 그럴 뻔했다 — NOAH 실수 #24 '목록형 가드는 새
# 파일을 못 잡는다'). 레지스트리에서 **파생**하고, 레지스트리 밖 페이지만
# 예외로 명시한다.
def _modules() -> list[tuple[str, str]]:
    from trade import badonion_sources as _srcs
    out = [(s.open_db.__module__.rsplit(".", 1)[-1], s.open_db.__name__)
           for s in _srcs.SOURCES if s.html_file]
    out.append(("jp_exports", "open_jp_db"))   # 비온 채널 — SOURCES 밖
    return sorted(set(out))


_MODULES = _modules()


def _render_empty(name, opener):
    """빈 DB 로 페이지를 실제 렌더 — 두 테스트 클래스가 공유."""
    m = importlib.import_module(f"trade.{name}")
    return m.render_html(getattr(m, opener)(":memory:"))


class BackLinkTests(unittest.TestCase):
    """`← 수출입 대시보드` 가 실제로 수출입 대시보드로 가야 한다."""

    def test_every_page_links_back_to_the_trade_root(self):
        for name, opener in _MODULES:
            with self.subTest(module=name):
                html = _render_empty(name, opener)
                hrefs = re.findall(r'<div class="nav"><a href="([^"]+)"', html)
                self.assertEqual(hrefs, ["./"],
                                 f"{name}: back-link 이 './' 가 아님 — {hrefs}")

    def test_no_page_points_at_index_html(self):
        """`index.html` 은 `bot/dashboard_server._OUR_ROOT_PAGES` 에 있어
        `/trade/index.html` 요청이 NOAH 종목분석으로 302 된다."""
        for name, opener in _MODULES:
            with self.subTest(module=name):
                self.assertNotIn("index.html", _render_empty(name, opener),
                                 f"{name}: 302 로 튕기는 링크")

    def test_empty_state_also_has_a_back_link(self):
        """데이터가 없는 페이지에 들어온 사람도 돌아갈 수 있어야 한다 —
        us_imports 빈상태에는 nav 자체가 없었다."""
        for name, opener in _MODULES:
            with self.subTest(module=name):
                html = _render_empty(name, opener)
                self.assertIn("← 수출입 대시보드", html, f"{name}: nav 없음")

    def test_helper_is_the_single_source(self):
        """하드코딩이 하나라도 남으면 다음 소스가 또 그걸 복사한다."""
        import pathlib
        for p in pathlib.Path("trade").glob("*.py"):
            src = p.read_text(encoding="utf-8")
            code = "\n".join(ln for ln in src.splitlines()
                             if not ln.lstrip().startswith("#"))
            if p.name == "archive_template.py":
                continue
            self.assertNotIn("← 대시보드</a>", code, f"{p.name}: 하드코딩 back-link")

    def test_depth_variants(self):
        from trade.archive_template import back_link_html, back_nav_html
        self.assertIn('href="./"', back_nav_html())
        self.assertIn('href="../"', back_nav_html(1))       # 동결 아카이브
        self.assertIn('href="../"', back_link_html(1, "대시보드"))
        # 두 헬퍼의 URL 규칙이 갈라지면 안 된다.
        self.assertIn(back_link_html(1), back_nav_html(1))


class EmptyDetailCardTests(unittest.TestCase):
    """본문이 없으면 펼치기 토글 자체를 만들지 않는다."""

    def test_card_html_flattens_when_body_is_empty(self):
        from trade.archive_template import card_html
        flat = card_html("kr", ["<b>SanDisk</b>"], ["", ""])
        self.assertNotIn("<details", flat)
        self.assertNotIn("<summary", flat)
        self.assertIn('<div class="kr-card">', flat)
        self.assertIn("<b>SanDisk</b>", flat, "요약은 그대로 보여야")

    def test_card_html_keeps_details_when_body_exists(self):
        from trade.archive_template import card_html
        full = card_html("kr", ["<b>Kioxia</b>"], ["", "<table>x</table>"])
        self.assertIn('<details class="kr-card">', full)
        self.assertIn('<summary class="kr-sum">', full)
        self.assertIn("<table>x</table>", full)

    def test_expand_arrow_is_scoped_to_details(self):
        """평면 카드에 '▸ 펼치기' 안내가 붙으면 여전히 눌러보게 된다."""
        import pathlib
        for p in pathlib.Path("trade").glob("*_exports.py"):
            src = p.read_text(encoding="utf-8")
            if "펼치기(차트·월별)" not in src:
                continue
            with self.subTest(module=p.name):
                self.assertRegex(
                    src, r'details\.[a-z0-9]+-card > \.[a-z0-9]+-sum::after',
                    f"{p.name}: 화살표가 details 로 스코프되지 않음")

    def test_single_month_no_chart_card_has_no_toggle(self):
        """실제 회귀 — 소스 첫 달에는 히스토리 표가 없고(2개월 미만),
        텍스트 캡션이면 차트도 없다(SanDisk). 그 조합이 빈 토글이었다."""
        from trade import jp_stock_exports as jps
        conn = jps.open_jp_stock_db(":memory:")
        jps.upsert_jp_stock(conn, {
            "ticker": "SNDK", "stock_name": "SanDisk", "month": "2026-06",
            "item": "NAND", "export_yoy": 95.1, "export_yoy_3m": 103.9,
            "price_yoy": 104.7, "note": "Kioxia와 동일한 공동생산 흐름"},
            chart_media=None, source_message_id=1, posted_at="", raw_text="")
        html = jps.render_html(conn)
        self.assertIn("SanDisk", html)
        self.assertNotIn("<details", html, "1개월·차트없음인데 토글이 생김")
        self.assertNotIn("펼치기", html.split("</style>")[-1],
                         "본문 없는 카드에 펼치기 안내")
        # 두 번째 달이 쌓이면 히스토리 표가 생겨 토글이 돌아와야 한다.
        jps.upsert_jp_stock(conn, {
            "ticker": "SNDK", "stock_name": "SanDisk", "month": "2026-07",
            "item": "NAND", "export_yoy": 88.0}, chart_media=None,
            source_message_id=2, posted_at="", raw_text="")
        html2 = jps.render_html(conn)
        self.assertIn("<details", html2, "2개월인데 토글이 없음")

    def test_rendered_pages_have_balanced_divs(self):
        """대량 기계치환에서 닫는 태그를 잃기 쉽다 — 실제로 동결 아카이브의
        `</div>` 하나가 사라져 본문 전체가 back-link 스타일 안에 중첩됐다
        (2026-08-16 독립 리뷰). 렌더 결과로 균형을 강제한다."""
        import re
        for name, opener in _MODULES:
            with self.subTest(module=name):
                html = _render_empty(name, opener)
                body = html.split("<body>", 1)[-1]
                opens = len(re.findall(r"<div\b", body))
                closes = body.count("</div>")
                self.assertEqual(opens, closes,
                                 f"{name}: div {opens}열림/{closes}닫힘")

    def test_frozen_archive_page_divs_balance(self):
        from trade import report_archive as ra
        html = ra.render_frozen_page("제목", "<p>본문</p>") if hasattr(
            ra, "render_frozen_page") else None
        if html is None:      # 함수명이 바뀌면 소스로라도 확인
            import inspect
            src = inspect.getsource(ra)
            seg = src[src.index("rb-wrap"):src.index("</body></html>") + 16]
            self.assertEqual(seg.count("<div"), seg.count("</div>"),
                             "동결 페이지 div 불균형")

    def test_flat_card_is_not_clickable(self):
        """`cursor:pointer` 가 평면 카드에도 걸리면 눌리는 척한다."""
        import pathlib as _p
        for f in _p.Path("trade").glob("*_exports.py"):
            src = f.read_text(encoding="utf-8")
            if "펼치기(차트·월별)" not in src:
                continue
            with self.subTest(module=f.name):
                # (?m) 필수 — assertNotRegex 는 re.search 라 `^` 가 기본으로
                # 문자열 맨 앞만 가리킨다(2026-08-16 mutation 에서 미검출).
                self.assertNotRegex(
                    src, r"(?m)^\.[a-z0-9]+-sum\{list-style:none;cursor:pointer",
                    f"{f.name}: cursor 가 details 로 스코프되지 않음")
                self.assertRegex(
                    src, r"details\.[a-z0-9]+-card > \.[a-z0-9]+-sum\{cursor:pointer\}",
                    f"{f.name}: details 안에서 클릭 가능해야")

    def test_archive_link_keeps_its_inline_style(self):
        """동결 페이지 CSS 에 bare `a` 규칙이 없어 스타일을 빼면 기본 파랑·
        밑줄로 렌더돼 다크테마에서 안 읽힌다."""
        from trade.archive_template import back_link_html
        a = back_link_html(1, "수출입 대시보드", "color:var(--accent)")
        self.assertIn('style="color:var(--accent)"', a)
        self.assertNotIn("style=", back_link_html())   # 기본은 스타일 없음
        import pathlib as _p
        src = _p.Path("trade/industry_archive.py").read_text(encoding="utf-8")
        self.assertIn("color:var(--accent);text-decoration:none", src)


    def test_sandisk_is_kept(self):
        """공동생산 각주는 '합산하지 말라'지 '중복'이 아니다 — SanDisk 고유
        수치를 지우면 정보가 사라진다(사용자 2026-08-16 확정).
        소스 문자열이 아니라 **렌더 결과**로 확인한다."""
        from trade import jp_stock_exports as jps
        conn = jps.open_jp_stock_db(":memory:")
        for tk, nm in (("SNDK", "SanDisk"), ("285A", "Kioxia")):
            jps.upsert_jp_stock(conn, {
                "ticker": tk, "stock_name": nm, "month": "2026-06",
                "item": "NAND", "export_yoy": 95.1},
                chart_media=None, source_message_id=1, posted_at="", raw_text="")
        html = jps.render_html(conn)
        self.assertIn("SanDisk", html, "SanDisk 카드가 렌더에서 제외됨")
        self.assertIn("SNDK", html)
        self.assertIn("Kioxia", html)


if __name__ == "__main__":
    unittest.main()


class CompanyVisibilityTests(unittest.TestCase):
    """관련기업(티커)은 **접힌 카드에서도** 보여야 한다.

    사용자 2026-08-19(미국 PPI): "텔레그램 카드에 관련기업이 있는데 우리
    대시보드엔 반영이 안 된 것 같다 — 카드에 있는 내용은 최대한 반영돼야
    한다." 실제로 tw·cn·jp2·th·my·ph·mx 7개는 요약에 넣고 있었고 미국
    수입/PPI 두 페이지만 펼침 본문에 묻어 있었다(복사한 쪽이 틀린 쪽).
    티커는 이 카드의 **행동 가능한 정보**라 20장 그리드에서 안 보이면 없는
    것과 같다.
    """

    def test_no_module_hides_companies_in_the_detail_pane(self):
        """디렉터리를 훑는다 — 목록형이면 다음 소스가 또 빠진다(실수 #24)."""
        import pathlib
        checked = 0
        for p in sorted(pathlib.Path("trade").glob("*.py")):
            src = p.read_text(encoding="utf-8")
            code = "\n".join(ln for ln in src.splitlines()
                             if not ln.lstrip().startswith("#"))
            if "co_html" not in code or "card_html(" not in code:
                continue                      # 카드가 아닌 페이지(레퍼런스북 등)
            checked += 1
            with self.subTest(module=p.name):
                # 요약에 넣는 방식은 둘 다 허용 — `summary.append(co_html)`
                # 또는 헤더 문자열에 인라인(jp_exports). 금지되는 건 하나,
                # **펼침 본문(card_html 의 두 번째 리스트)에 넣는 것**이다.
                self.assertNotRegex(
                    code, r"card_html\([^)]*\[[^\]]*co_html",
                    f"{p.name}: 관련기업이 펼침 본문에 들어갔다(펼쳐야 보임)")
        self.assertGreaterEqual(checked, 10, "훑기가 모듈을 못 찾았다")

    def test_us_ppi_and_us_imports_render_companies_in_summary(self):
        """소스 스캔은 배선만 본다 — 실제 렌더로 위치까지 확인."""
        from trade import us_imports as us, us_ppi as up
        ppi = up.open_us_ppi_db(":memory:")
        up.ingest(ppi, "🇺🇸 7월 미국 PPI\n▶️ 품목A\n"
                       "26년07월: PPI 73.24  (+11.7% YoY)  (+4.6% MoM)\n"
                       "관련기업: #DIS  #CMCSA",
                  source_message_id=1, posted_at="", media_paths=None)
        usi = us.open_us_db(":memory:")
        us.ingest(usi, "🇺🇸 6월 수입 미국\n▶️ 품목B\n"
                       "26년06월: $12.3M  (+7.0% YoY)  (+1.5% MoM)\n"
                       "관련기업: #AAPL  #MSFT",
                  source_message_id=1, posted_at="", media_paths=None)
        for name, html, tickers in (("us_ppi", up.render_html(ppi), "DIS, CMCSA"),
                                    ("us_imports", us.render_html(usi), "AAPL, MSFT")):
            with self.subTest(module=name):
                head = html.split("</summary>")[0]
                self.assertIn(tickers, head,
                              f"{name}: 관련기업이 접힌 카드에 안 보인다")
                # 본문에 중복 출력되면 같은 정보가 두 번 나온다.
                self.assertEqual(html.count(tickers), 1)
