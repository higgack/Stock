"""종목 카드 제목 → NOAH `/lookup/` 딥링크 회귀 (사용자 2026-09-22).

"수출입대시보드에 … 종목제목을 클릭하면 종목화면으로 … 종목이 있는 대시보드에
종목들은 모두 적용 … 회사별도 똑같이".

여기서 재는 것:
  ① 질의 규칙(`lookup_query`) — 규칙마다 **그 규칙을 지우면 깨지는** 값 하나씩(#91).
  ② 여덟 보드 E2E — 파서·DB·렌더를 **통째로 태워** href 값을 본다(#20 헬퍼만
     재면 배선을 떼는 변형을 못 잡는다).
  ③ CSS 정의 — 클래스만 쓰고 정의를 빠뜨리면 조용히 스타일이 빠진다(#201·#273).
  ④ 프록시 계약 — trade 가 쓰는 접두와 NOAH 프록시가 바꾸는 접두가 **같은 값**
     인지(둘이 갈리면 전 보드 링크가 404 다, #38).
"""

import contextlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from trade import stock_link as sl

_A = re.compile(r'<a class="sl-link" href="([^"]+)"[^>]*>(.*?)</a>')


def _links(html: str) -> list[tuple[str, str]]:
    return _A.findall(html)


@contextlib.contextmanager
def _stub_resolver(mapping: dict):
    """`price_provider.resolve_codes` 를 **리졸버 경계**에서 갈아끼운다.

    ⚠️ 한 단(`_load_krx_master`)만 스텁하면 나머지 세 단 — 운영자 오버라이드
    (`~/.trade/stock_overrides.json`) · `_DIRECT_CODES`(레포 상수, `trade/CLAUDE.md`
    가 **한 줄 추가를 정상 운영으로 규정**한다) · durable 캐시
    (`~/.trade/stock_codes.json` + `TRADE_DATA_GO_KR_KEY`) — 가 살아 있어
    **운영자의 홈 상태가 단언을 뒤집는다**(독립 리뷰 2026-09-22 실측: 세 경로로
    재현). `_DATA_DIR` 는 import 시점 상수라 `monkeypatch.setenv` 로도 못 가린다
    — 규율로 네 번 진 자리다(#30·#312·#344·#384). 경계에서 갈면 `kr_codes` 의
    자기 계약(합성키 거르기·`fetch=False`)은 그대로 탄다.
    """
    import trade.price_provider as pp
    real = pp.resolve_codes
    try:
        pp.resolve_codes = lambda names, **kw: {
            n: mapping[n] for n in names if n in mapping}
        yield
    finally:
        pp.resolve_codes = real


def _js_fn(src: str, name: str) -> str:
    """중괄호를 세어 함수 **본문만** 잘라낸다 — 고정 길이 창은 옆 함수가 대신
    만족시킨다(#60·#174)."""
    i = src.index(f"function {name}(")
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(name)


def _node_run(case, names, pre, post, arg):
    """제품 `_JS` 에서 함수를 떼어 실제로 **실행**한다 — 소스 문자열 단언은
    값이 걸린 곳을 못 잡는다(#19·#253 생성물이 JS 면 실행까지 태울 수 있는지
    먼저 물을 것)."""
    node = shutil.which("node")
    if not node:
        case.skipTest("node 없음")
    from trade import dashboard as d
    body = "".join(_js_fn(d._JS, n) + "\n" for n in names)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "h.js"
        f.write_text(pre + body + post, encoding="utf-8")
        r = subprocess.run([node, str(f), json.dumps(arg, ensure_ascii=False)],
                           capture_output=True, text=True, timeout=30)
    case.assertEqual(r.returncode, 0, r.stderr[-800:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class QueryRuleTests(unittest.TestCase):
    def test_suffixed_ticker_is_used_as_is(self):
        self.assertEqual(sl.lookup_query("2330.TW", "TSMC"), "2330.TW")
        self.assertEqual(sl.lookup_query("005930.kq", "삼성"), "005930.KQ")

    def test_alphabetic_ticker_is_used_as_is(self):
        # 관측된 보드 대부분(미국 상장 심볼).
        for t in ("TTMI", "DELL", "TXN", "ASX", "MXL", "PTON", "LITE", "COHU"):
            self.assertEqual(sl.lookup_query(t, "아무이름"), t)

    def test_bare_six_digits_only_on_a_board_that_declares_kr(self):
        r"""맨 6자리는 그대로 — KS/KQ 는 KRX 목록을 가진 NOAH 가 가른다.

        ⚠️ 선언이 없으면 만들지 않는다. 중국·대만 보드의 티커 정규식이
        `[A-Za-z0-9.\-]{2,10}` 이라 A주 코드 모양을 **받는데**(실측), 그걸 KR 로
        보면 `600519` 가 `600519.KS`(= 남의 회사 분석 화면)로 간다 — 링크가
        없으면 평문이지만 **틀린 링크는 거짓말**이다(#144·#43·#34)."""
        self.assertEqual(sl.lookup_query("403870", "HPSP", local="KR"), "403870")
        self.assertEqual(sl.lookup_query("403870", "HPSP"), "",
                         "선언 없는 보드의 맨 6자리에 시장을 추측해 붙이면 안 된다")
        self.assertEqual(sl.lookup_query("600519", "어떤회사"), "")

    def test_alias_resolvable_name_wins_over_unusable_ticker(self):
        """Advantest(6857) — 접미사도 영문자도 6자리도 아니지만 NOAH 별칭표가
        푼다. 이 규칙을 지우면 일본 보드의 도쿄 코드 카드가 통째로 평문이 된다."""
        self.assertEqual(sl.lookup_query("6857", "Advantest"), "Advantest")

    def test_alias_does_not_override_a_usable_ticker(self):
        """카드의 티커가 권위다 — TSM(미국 ADR) 카드를 TSMC 별칭(2330.TW)으로
        바꾸면 **다른 시장**을 연다(#34)."""
        self.assertEqual(sl.lookup_query("TSM", "TSMC"), "TSM")

    def test_tokyo_local_code_only_on_a_board_that_declares_jp(self):
        """Kioxia(285A) — 도쿄 신표기. 보드 국적 힌트는 숫자 티커가 실제로
        관측된 일본 보드에서만 쓴다(#165)."""
        self.assertEqual(sl.lookup_query("285A", "Kioxia", local="JP"), "285A.T")
        self.assertEqual(sl.lookup_query("285A", "Kioxia"), "",
                         "힌트 없는 보드까지 .T 를 붙이면 남의 시장을 연다")

    def test_tokyo_code_width_is_exactly_four(self):
        """도쿄 코드는 4자 — 폭을 안 재면 5자리(`12345`)가 `12345.T` 가 된다
        (독립 리뷰 실측 M40: 폭 가드를 지워도 전 슈트 통과했다, #91c)."""
        self.assertEqual(sl.lookup_query("12345", "모르는이름", local="JP"), "")
        self.assertEqual(sl.lookup_query("123", "모르는이름", local="JP"), "")
        self.assertEqual(sl.lookup_query("6857", "모르는이름", local="JP"), "6857.T")

    def test_unresolvable_gets_no_query(self):
        self.assertEqual(sl.lookup_query("", ""), "")
        self.assertEqual(sl.lookup_query("", "이름만있는회사"), "")
        self.assertEqual(sl.lookup_query("1234", "모르는이름"), "")

    def test_href_only_inside_the_noah_ticker_charset(self):
        """NOAH `/lookup/` 의 관문(`_TICKER_RE`) 밖 질의는 검색실패 페이지로
        간다 — 죽은 링크와 같으므로 아예 안 만든다(#144)."""
        self.assertEqual(sl.lookup_href(query="한글회사명"), "")
        self.assertEqual(sl.lookup_href(query="A" * 11), "")
        self.assertEqual(sl.lookup_href(query="005930"), "../lookup/005930")

    def test_linked_name_escapes_and_falls_back_to_plain(self):
        self.assertEqual(sl.linked_name("A&B", ""), "A&amp;B")
        out = sl.linked_name("A&B", "../lookup/X")
        self.assertIn("A&amp;B", out)
        self.assertIn('href="../lookup/X"', out)

    def test_linked_name_escapes_the_href_too(self):
        """`href` 이스케이프는 이 **공개 헬퍼의 시그니처 계약**이지
        `lookup_href` 출력의 성질이 아니다 — 오늘 그 출력은 퍼센트 인코딩이라
        no-op 이므로, 가드가 실제로 발화하는 입력으로 잰다(#291 도달 경로 없는
        가드는 가드가 아니다)."""
        out = sl.linked_name("회사", '../lookup/X"><img src=x onerror=alert(1)>')
        self.assertNotIn("<img", out)
        self.assertIn("&quot;&gt;&lt;img", out)

    def test_kr_codes_rejects_synthetic_keys(self):
        """합성키(`nm:회사`)가 코드 자리에 앉으면 화면이 없는 종목코드를
        있다고 말한다(#34·#43)."""
        import trade.price_provider as pp
        real = pp.resolve_codes
        try:
            pp.resolve_codes = lambda names, **kw: {"삼성전자": "005930",
                                                    "이상한회사": "nm:이상한회사"}
            got = sl.kr_codes(["삼성전자", "이상한회사"])
        finally:
            pp.resolve_codes = real
        self.assertEqual(got, {"삼성전자": "005930"})

    def test_kr_codes_answers_with_the_caller_s_own_key(self):
        """`resolve_codes` 는 `n.strip()` 한 키로 돌려준다 — 그대로 내보내면
        앞뒤 공백이 붙은 이름을 넘긴 렌더러가 `code_by_name.get(raw_name)` 에서
        **조용히** 못 찾는다(링크만 안 생기는 미스). 호출부 셋이 각자 정규화하면
        갈라지므로 여기서 되돌린다(#38, 독립 리뷰 2026-09-22 L5)."""
        import trade.price_provider as pp
        real = pp.resolve_codes
        try:
            pp.resolve_codes = lambda names, **kw: {
                n.strip(): "005930" for n in names if n.strip() == "삼성전자"}
            got = sl.kr_codes([" 삼성전자 ", "삼성전자", "모르는회사"])
        finally:
            pp.resolve_codes = real
        self.assertEqual(got, {" 삼성전자 ": "005930", "삼성전자": "005930"})

    def test_kr_codes_reads_cache_only(self):
        """렌더는 외부 호출 0 — `fetch=False` 를 빼면 페이지마다 네트워크가
        붙는다(#116)."""
        import trade.price_provider as pp
        seen = {}
        real = pp.resolve_codes
        try:
            pp.resolve_codes = lambda names, **kw: seen.update(kw) or {}
            sl.kr_codes(["삼성전자"])
        finally:
            pp.resolve_codes = real
        self.assertIs(seen.get("fetch"), False)


class ProxyContractTests(unittest.TestCase):
    def test_prefix_matches_what_the_noah_proxy_rewrites(self):
        """trade 가 쓰는 접두와 NOAH 프록시가 절대화하는 접두는 **같은 값**
        이어야 한다 — 갈리면 전 보드 링크가 토큰을 잃고 404 다(#38·#359)."""
        from bot import dashboard_server as ds
        body = f'<a href="{sl.LOOKUP_PREFIX}005930">x</a>'.encode()
        self.assertEqual(ds._rewrite_trade_html(body, "tok"),
                         b'<a href="/tok/lookup/005930">x</a>')
        self.assertEqual(ds._rewrite_trade_html(body, ""),
                         b'<a href="/lookup/005930">x</a>')


class BoardRenderTests(unittest.TestCase):
    """여덟 보드를 파서→DB→렌더로 통째로 태운다(#20)."""

    def _render(self, mod, opener, caption, *, arg=None):
        with tempfile.TemporaryDirectory() as tmp:
            conn = getattr(mod, opener)(Path(tmp) / "t.db")
            mod.ingest(conn, caption)
            return mod.render_html(conn) if arg is None else mod.render_html(conn, arg)

    def test_us_symbol_boards(self):
        from trade import (cn_stock_exports as cns, cn_stock_imports as cni,
                           my_stock_exports as mys, tw_stock_exports as tws)
        cases = [
            (cns, "open_cn_stock_db",
             "TTM Technologies (TTMI)\n중국 수출\n26년 7월 Update\n\n수출액 YoY: +55.8%",
             "../lookup/TTMI", "TTM Technologies"),
            (cni, "open_cn_stock_import_db",
             "Dell Technologies (DELL)\n중국 수입\n26년 7월 Update\n\n수입액 YoY: +9.5%",
             "../lookup/DELL", "Dell Technologies"),
            (tws, "open_tw_stock_db",
             "ASE Technology Holding (ASX)\n대만 수출\n26년 8월 Update\n\n수출액 YoY: +12.0%",
             "../lookup/ASX", "ASE Technology Holding"),
            (mys, "open_my_stock_db",
             "Texas Instruments Incorporated (TXN)\n말레이시아 수출\n26년 7월 "
             "Update\n\n수출액 YoY: +170.4%",
             "../lookup/TXN", "Texas Instruments Incorporated"),
        ]
        for mod, opener, cap, href, label in cases:
            html = self._render(mod, opener, cap)
            self.assertIn((href, label), _links(html), mod.__name__)
            self.assertIn(".sl-link{", html, f"{mod.__name__} CSS 미정의(#201)")

    def test_japan_board_covers_both_ticker_shapes(self):
        from trade import jp_stock_exports as jps
        with tempfile.TemporaryDirectory() as tmp:
            c = jps.open_jp_stock_db(Path(tmp) / "t.db")
            jps.ingest(c, "Lumentum (LITE) 일본 수출 Update\n26년 6월\n\n"
                          "수출액: YoY +446.7%")
            jps.ingest(c, "**Advantest (6857) 일본 수출 Update**\n**26년 6월**\n\n"
                          "수출액: YoY +76.4%")
            jps.ingest(c, "**Kioxia (285A) 일본 수출 Update**\n**26년 6월**\n\n"
                          "수출액: YoY +95.1%")
            html = jps.render_html(c)
        got = dict(_links(html))
        self.assertEqual(got.get("../lookup/LITE"), "Lumentum")
        self.assertEqual(got.get("../lookup/Advantest"), "Advantest")
        self.assertEqual(got.get("../lookup/285A.T"), "Kioxia")
        self.assertIn(".sl-link{", html)

    def test_bare_numeric_tickers_do_not_borrow_another_market(self):
        """선언(`local=`) 없는 보드의 맨 숫자 티커는 **평문**이다.

        ⚠️ 이 축이 무가드였다(독립 리뷰 2026-09-22 실측 M46~M49: `local=` 배선을
        떼거나 뒤집는 변형 넷이 전부 통과). 그러면 말레이시아 보드의
        `Foo Corp (5347)` 이 `../lookup/5347.T`(도쿄) 로, 중국 보드의
        `(600519)` 가 `../lookup/600519`(→ NOAH 가 `.KS` 로 해석) 로 간다 —
        둘 다 **남의 회사 분석 화면**이다(#34·#144).

        이름은 별칭표가 못 푸는 것으로 둔다 — 풀리면 규칙 4 가 먼저 걸려
        이 축을 안 태운다(#91b 재는 대상이 맞나)."""
        from trade import cn_stock_exports as cns, my_stock_exports as mys
        cases = [
            (mys, "open_my_stock_db",
             "무명전자 (5347)\n말레이시아 수출\n26년 7월 Update\n\n수출액 YoY: +1.0%"),
            (mys, "open_my_stock_db",
             "무명전자 (600519)\n말레이시아 수출\n26년 7월 Update\n\n수출액 YoY: +1.0%"),
            (cns, "open_cn_stock_db",
             "무명전자 (6857)\n중국 수출\n26년 7월 Update\n\n수출액 YoY: +1.0%"),
            (cns, "open_cn_stock_db",
             "무명전자 (000001)\n중국 수출\n26년 7월 Update\n\n수출액 YoY: +1.0%"),
        ]
        for mod, opener, cap in cases:
            html = self._render(mod, opener, cap)
            self.assertEqual(_links(html), [], f"{mod.__name__}: {cap[:22]}")
            self.assertIn("무명전자", html, "링크를 안 걸어도 이름은 보인다")

    def test_korea_export_board_links_by_code(self):
        from trade import kr_stock_exports as krs
        html = self._render(krs, "open_kr_stock_db",
                            "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n"
                            "수출액 YoY: +260.2%")
        self.assertIn(("../lookup/403870", "HPSP"), _links(html))
        self.assertIn(".sl-link{", html)

    def test_taiwan_revenue_board_links_by_alias_name(self):
        from trade import tw_monthly_revenue as twr
        html = self._render(twr, "open_tw_revenue_db",
                            "TSMC 월매출\n26년 8월\n\nREV 약 5,148억 1,000만 TWD\n"
                            "MoM +10.1% · YoY +53.3%\n\n"
                            "https://badonion.co.kr/twse-revenue")
        self.assertIn(("../lookup/TSMC", "TSMC"), _links(html))
        self.assertIn(".sl-link{", html)

    def test_company_board_links_through_the_price_resolver(self):
        """회사별 보드는 코드가 없다 — 시세 칩이 쓰는 그 리졸버가 푼 코드로
        건다(#150). 리졸버가 못 풀면 평문(빈 href 금지, #144).

        ⚠️ 스텁은 `_stub_resolver`(리졸버 **경계**)로 건다 — 한 단만 갈면
        운영자의 `~/.trade` 상태가 단언을 뒤집는다(그 docstring 참조)."""
        from trade import kr_stock_imports as kri
        cap = ("8월 수입 한국\n\n▶️ 삼성전자 — 반도체 장비\n"
               "26년08월: $158.5M (+105.3% YoY) (+11.8% MoM)")
        with _stub_resolver({"삼성전자": "005930"}):
            html = self._render(kri, "open_kr_stock_import_db", cap)
        self.assertIn(("../lookup/005930", "삼성전자"), _links(html))
        with _stub_resolver({}):
            bare = self._render(kri, "open_kr_stock_import_db", cap)
        self.assertEqual(_links(bare), [], "못 푼 회사에 링크를 만들면 죽은 링크다")
        self.assertIn("삼성전자", bare)
        self.assertIn(".sl-link{", bare, "CSS 는 링크 유무와 무관하게 정의된다")

    def test_name_resolution_is_one_call_per_page(self):
        """카드마다 부르면 캐시 파일을 행 수만큼 다시 읽는다(#113)."""
        from trade import kr_stock_imports as kri
        import trade.price_provider as pp
        calls = []
        real = pp.resolve_codes
        try:
            pp.resolve_codes = lambda names, **kw: calls.append(list(names)) or {}
            with tempfile.TemporaryDirectory() as tmp:
                c = kri.open_kr_stock_import_db(Path(tmp) / "t.db")
                for nm in ("삼성전자", "SK하이닉스", "LG전자"):
                    # 픽스처는 **제품 삽입 경로**로 심는다(#323 — 손으로 짠
                    # INSERT 는 스키마가 바뀌면 조용히 다른 것을 잰다).
                    kri.ingest(c, f"8월 수입 한국\n\n▶️ {nm} — 반도체 장비\n"
                                  "26년08월: $158.5M (+105.3% YoY)")
                kri.render_html(c)
        finally:
            pp.resolve_codes = real
        self.assertEqual(len(calls), 1, f"페이지당 1회여야 한다 — {len(calls)}회")
        self.assertEqual(sorted(calls[0]), ["LG전자", "SK하이닉스", "삼성전자"])

    def test_export_board_resolves_only_the_codeless_rows_once(self):
        """한국 수출 보드는 금액판(합성키) 행만 이름으로 푼다 — 코드가 이미
        있는 행까지 넘기면 리졸버가 헛돈다(#61). 그리고 페이지당 1회(#113)."""
        from trade import kr_stock_exports as krs
        import trade.price_provider as pp
        calls = []
        real = pp.resolve_codes
        try:
            pp.resolve_codes = lambda names, **kw: (calls.append(list(names))
                                                    or {"LS ELECTRIC": "010120"})
            with tempfile.TemporaryDirectory() as tmp:
                c = krs.open_kr_stock_db(Path(tmp) / "t.db")
                krs.ingest(c, "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n"
                              "수출액 YoY: +260.2%")
                krs.ingest(c, "8월 수출 한국\n\n▶️ LS ELECTRIC — 배전반\n"
                              "26년08월: $158.5M (+105.3% YoY)")
                html = krs.render_html(c)
        finally:
            pp.resolve_codes = real
        self.assertEqual(len(calls), 1, f"페이지당 1회여야 한다 — {len(calls)}회")
        self.assertEqual(calls[0], ["LS ELECTRIC"],
                         "코드가 이미 있는 행은 리졸버에 안 넘긴다")
        got = dict(_links(html))
        self.assertEqual(got.get("../lookup/403870"), "HPSP")
        self.assertEqual(got.get("../lookup/010120"), "LS ELECTRIC")


class DashboardCompanyViewTests(unittest.TestCase):
    """회사별 탭 — 섹션 제목이 종목분석 화면으로(사용자 4번째 캡처)."""

    # ⚠️ 헬퍼만 부르면 **배선을 떼는 변형을 못 잡는다**(#20 — 실측: 뮤테이션
    # M12 `stockHref(name)` → `''` 가 통과했다). `buildCompaniesView` 를 통째로
    # 태워 제목 href 가 그 뷰에서 나오는지 본다.
    _PRE = ("var STOCK_QUOTES=JSON.parse(process.argv[2]);\n"
            "var state={q:''};\n"
            "function renderMiniCard(a){return '<i></i>';}\n"
            "function isCompanyNew(n){return false;}\n"
            "function regionTier(a){return 0;}\n")
    _POST = ("\nvar ALERTS=[{companies:['삼성전자'],item:'디램',posted_at:'2026-08-01'},"
             "{companies:['모르는회사'],item:'라면',posted_at:'2026-08-02'}];\n"
             "console.log(JSON.stringify({view:buildCompaniesView(ALERTS),"
             "c:renderSection('디램',['3개 품목'],'<i></i>',false,'')}));")

    def _run(self, quotes: dict) -> dict:
        return _node_run(self, ("esc", "pxLookup", "stockHref", "slLink",
                                "sectionStockPx", "renderSection",
                                "buildCompaniesView"),
                         self._PRE, self._POST, quotes)

    def test_company_title_links_when_the_quote_carries_a_code(self):
        out = self._run({"삼성전자": {"p": 275000, "c": 5.4, "s": "005930"}})
        self.assertIn('href="../lookup/005930"', out["view"])
        self.assertIn(">삼성전자</a>", out["view"])
        # 코드를 못 받은 회사는 **같은 뷰 안에서도** 평문이다(#144).
        self.assertNotIn("모르는회사</a>", out["view"])
        self.assertEqual(out["view"].count("sl-link"), 1, out["view"][:400])
        # 시세 칩(기존 기능)은 그대로 — 링크가 헤더를 갈아치우면 안 된다.
        self.assertIn("275,000원", out["view"])

    def test_no_link_without_a_code_and_no_link_on_non_stock_axes(self):
        out = self._run({"삼성전자": {"p": 275000, "c": 5.4}})
        self.assertNotIn("sl-link", out["view"], "코드가 없으면 링크를 만들지 않는다")
        self.assertNotIn("sl-link", out["c"], "품목·산업·국가 축은 종목이 아니다")

    def test_dashboard_bundle_defines_the_class(self):
        from trade import dashboard as d
        self.assertIn(".sl-link{", d._CSS, "CSS 미정의(#201·#273)")

    def test_quote_payload_carries_only_six_digit_codes(self):
        from trade import dashboard as d
        from types import SimpleNamespace as NS

        class _PP:
            @staticmethod
            def provider_active():
                return True

            @staticmethod
            def split_names(raw):
                return list(raw)

            @staticmethod
            def recommended_ttl():
                return 60

            @staticmethod
            def get_quotes_by_name(names, **kw):
                return {"삼성전자": NS(price=275000.0, change_pct=5.4,
                                    symbol="005930", source="kis", as_of=""),
                        "이상한회사": NS(price=1.0, change_pct=0.0,
                                     symbol="nm:이상한회사", source="kis", as_of="")}

        # ⚠️ `sys.modules` 직접 대입 금지(#397) — 제품이 읽는 그 속성만 바꾼다.
        import trade.price_provider as pp
        saved = {k: getattr(pp, k) for k in
                 ("provider_active", "split_names", "recommended_ttl",
                  "get_quotes_by_name")}
        try:
            for k in saved:
                setattr(pp, k, getattr(_PP, k))
            got = d._stock_quotes_for([{"stocks": ["삼성전자", "이상한회사"]}])
        finally:
            for k, v in saved.items():
                setattr(pp, k, v)
        # 키 **집합**으로 잰다 — `.get("s")` 만 보면 여분 키가 새도 통과한다
        # (독립 리뷰 실측 M35, #91b 재는 대상이 맞나).
        self.assertEqual(set(got["삼성전자"]), {"p", "c", "s"})
        self.assertEqual(got["삼성전자"]["s"], "005930")
        self.assertEqual(set(got["이상한회사"]), {"p", "c"},
                         "합성키를 코드로 싣지 않는다(#34)")


class DashboardModalStocksTests(unittest.TestCase):
    """모달 관련종목 칩 — "종목이 있는 대시보드에 종목들은 모두 적용해줘".

    ⚠️ `renderModalCard` 를 통째로 태운다 — `slLink`/`stockHref` 만 재면 칩에서
    배선을 떼는 변형이 통과한다(#20, 실측으로 회사별 뷰에서 그랬다).
    """

    _PRE = ("var STOCK_QUOTES=JSON.parse(process.argv[2]);\n"
            "function whereLabel(a){return a.country||'';}\n"
            "function niceLabel(a){return a.item;}\n"
            "function slaBadge(a){return null;}\n"
            "function findCompositeLinks(a){return {asComposite:[],asPart:[]};}\n"
            "function findPeerStocks(a){return [];}\n")
    _POST = ("\nvar A={id:'x',item:'디램',dir:'export',status:'confirmed',"
             "country:'말레이시아',posted_at:'2026-08-01',media:[],"
             "stocks:['삼성전자','모르는회사','합성키회사','<b>회사</b>&'],"
             "has_etc:true};\n"
             "console.log(JSON.stringify({p:renderModalCard(A,true),"
             "s:renderModalCard(A,false)}));")

    def _run(self, quotes: dict) -> dict:
        return _node_run(self, ("esc", "pxLookup", "stockHref", "slLink",
                                "stockPx", "renderModalCard"),
                         self._PRE, self._POST, quotes)

    def test_chip_links_when_the_quote_carries_a_code(self):
        out = self._run({"삼성전자": {"p": 275000, "c": 5.4, "d": "09-19",
                                   "s": "005930"}})
        self.assertIn(("../lookup/005930", "삼성전자"), _links(out["p"]))
        # 코드를 못 받은 회사·'등' 은 **같은 칩 줄 안에서도** 평문(#144·#43).
        self.assertEqual(len(_links(out["p"])), 1, out["p"])
        self.assertIn(">모르는회사", out["p"])
        self.assertIn(">등<", out["p"])
        # 기존 기능(시세 칩)은 그대로 — 링크가 칩을 갈아치우면 안 된다.
        self.assertIn("+5.4%", out["p"])

    def test_no_link_without_a_code(self):
        out = self._run({"삼성전자": {"p": 275000, "c": 5.4}})
        self.assertNotIn("sl-link", out["p"])

    def test_synthetic_key_is_not_rendered_as_a_link(self):
        """합성키(`nm:회사`)는 종목코드가 아니다 — 서버가 이미 거르지만(#34)
        렌더 쪽 가드도 **실제로 발화하는 경로**로 잰다(#291·#139 2차 그물)."""
        out = self._run({"합성키회사": {"p": 1, "c": 0.0, "s": "nm:합성키회사"},
                         "삼성전자": {"p": 275000, "c": 5.4, "s": "005930"}})
        self.assertNotIn("lookup/nm", out["p"])
        self.assertEqual([h for h, _ in _links(out["p"])], ["../lookup/005930"])

    def test_caption_names_are_escaped_in_both_branches(self):
        """종목명은 **텔레그램 캡션**에서 온 남의 문자열이고, `slLink` 의
        `esc(text)` 가 화면에 닿기 전 유일한 관문이다.

        ⚠️ 그 축이 무가드였다(독립 리뷰 2026-09-22 실측 M19: `esc(text)` 를
        벗기면 칩에 원시 마크업이 실린다 —
        `title="종목분석 화면으로"><img src=x onerror=alert(1)></a>`). 내가
        #222 로 형제 테스트를 다시 쓰면서 이 축을 떨어뜨렸다(#378·#395 다시
        쓰는 것은 줄이는 것이 아니다). 링크가 **붙는 쪽·안 붙는 쪽 둘 다** 잰다."""
        linked = self._run({"<b>회사</b>&": {"p": 1, "c": 0.0, "s": "005930"}})["p"]
        self.assertIn(">&lt;b&gt;회사&lt;/b&gt;&amp;<", linked)
        self.assertNotIn("<b>회사</b>", linked)
        self.assertIn("../lookup/005930", linked, "이 갈래는 링크가 붙어야 한다")
        plain = self._run({})["p"]            # 코드 없음 → 평문 가지
        self.assertIn("&lt;b&gt;회사&lt;/b&gt;&amp;", plain)
        self.assertNotIn("<b>회사</b>", plain)
        self.assertNotIn("sl-link", plain)

    def test_sllink_escapes_the_href_attribute(self):
        """`slLink(text, href)` 는 **아무 href 나 받는 공개 마크업 헬퍼**다 —
        속성값 이스케이프는 그 시그니처의 계약이다.

        ⚠️ 오늘의 유일한 생산자(`stockHref`)는 `'../lookup/'+encodeURIComponent(6자리)`
        라 이 가드가 **제품 경로에서는 no-op** 이고, 그래서 `esc(href)` 를 벗기는
        변형이 전 슈트를 통과했다(독립 리뷰 실측 M18). 도달 경로 없는 가드는
        가드가 아니므로(#291) 헬퍼를 **직접** 태워 발화시킨다."""
        out = _node_run(self, ("esc", "slLink"),
                        "", "\nconsole.log(JSON.stringify({h:slLink("
                        "'회사', '../lookup/X\"><img src=x onerror=alert(1)>')}));",
                        {})
        self.assertNotIn("<img", out["h"])
        self.assertIn("&quot;&gt;&lt;img", out["h"])

    def test_sibling_card_has_no_stock_row(self):
        # 이전발표(secondary) 카드는 원래 관련종목을 안 그린다 — 링크를 붙이며
        # 그 계약을 바꾸지 않았는지 같이 본다(#222).
        out = self._run({"삼성전자": {"p": 275000, "c": 5.4, "s": "005930"}})
        self.assertNotIn("관련종목", out["s"])


class ReferenceBookTests(unittest.TestCase):
    """레퍼런스북 관련상장사 칩 — "종목이 있는 대시보드에 종목들은 모두 적용".

    ⚠️ `render_page` 를 통째로 태운다 — `kr_codes` 만 재면 칩에서 배선을 떼는
    변형이 통과한다(#20).
    """

    _ROWS = [{"mti6": "831110", "name": "디램", "industry": "반도체",
              "hs": ["8542311000"],
              "companies": ["삼성전자", "모르는회사", "하나마이크론"]}]

    def _render(self, codes: dict, rows=None) -> str:
        # ⚠️ 스텁은 **리졸버 경계**(`_stub_resolver`) — 한 단만 갈면 운영자의
        # `~/.trade` 상태·`_DIRECT_CODES` 한 줄이 단언을 뒤집는다(그 docstring).
        from trade import reference_book as R
        with _stub_resolver(codes):
            return R.render_page(rows if rows is not None else self._ROWS)

    def test_company_chip_links_when_a_code_resolves(self):
        html = self._render({"삼성전자": "005930", "하나마이크론": "067310"})
        self.assertEqual(sorted(_links(html)),
                         [("../lookup/005930", "삼성전자"),
                          ("../lookup/067310", "하나마이크론")])
        # 못 푼 이름은 평문 — 죽은 링크를 만들지 않는다(#144·#43).
        self.assertIn('<span class="x">모르는회사</span>', html)

    def test_no_resolver_means_a_plain_page_not_a_broken_one(self):
        html = self._render({})
        # ⚠️ `"sl-link" in html` 로 재면 **스타일시트**가 대신 만족시킨다(#300·#75)
        # — 앵커를 집는다.
        self.assertEqual(_links(html), [])
        self.assertIn('<span class="x">삼성전자</span>', html)
        self.assertTrue(html.startswith("<!doctype"))

    def test_resolver_failure_does_not_take_the_page_down(self):
        """곁들이 하나가 본체를 지우면 안 된다(#315)."""
        from trade import reference_book as R, stock_link as sl_mod
        saved = sl_mod.kr_codes
        try:
            def _boom(names):
                raise RuntimeError("resolver down")
            sl_mod.kr_codes = _boom
            html = R.render_page(self._ROWS)
        finally:
            sl_mod.kr_codes = saved
        self.assertEqual(_links(html), [])
        self.assertIn("삼성전자", html)
        self.assertIn("디램", html)

    def test_name_resolution_is_one_call_per_page(self):
        """행마다 부르면 페이지당 수백 번이다(#113)."""
        from trade import reference_book as R, stock_link as sl_mod
        calls = []
        saved = sl_mod.kr_codes
        try:
            sl_mod.kr_codes = lambda names: (calls.append(list(names)) or {})
            R.render_page(self._ROWS * 40)
        finally:
            sl_mod.kr_codes = saved
        self.assertEqual(len(calls), 1, calls[:2])

    def test_bundle_defines_the_class(self):
        from trade import reference_book as R
        self.assertIn(".sl-link{", R._CSS, "CSS 미정의(#201·#273)")

    def test_csv_extraction_still_sees_the_company_name(self):
        """CSV 는 `.x` 의 textContent 를 읽는다 — `<a>` 를 넣어도 같은 글자여야
        한다(#232 표기를 바꾸면 그 표기로 재던 경로가 깨진다)."""
        html = self._render({"삼성전자": "005930"})
        cell = re.search(r'<td class="co">(.*?)</td>', html, re.S).group(1)
        text = re.sub(r"<[^>]+>", "", cell)
        for name in ("삼성전자", "모르는회사", "하나마이크론"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
