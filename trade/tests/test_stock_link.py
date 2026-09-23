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

from trade import stock_link as _sl
from pathlib import Path

from trade import stock_link as sl

_A = re.compile(r'<a class="sl-link" href="([^"]+)"[^>]*>(.*?)</a>')


def _links(html: str) -> list[tuple[str, str]]:
    return _A.findall(html)


class _spy_kr_codes:
    """`stock_link.kr_codes` 를 감싸 **인자와 호출 수**를 본다(#113·#61).

    보드가 부르는 이름(`_sl.kr_codes`)을 갈아끼워야 배선까지 잰다 — 오라클만
    스텁하면 '페이지당 몇 번 조립했나'를 못 본다(#20)."""

    def __init__(self, mapping: dict):
        self.mapping, self.seen = mapping, []

    def __enter__(self):
        import trade.stock_link as m
        self._real = m.kr_codes
        def spy(names):
            got = [str(n) for n in names if n]
            self.seen.append(got)
            return {n: self.mapping[n] for n in got if n in self.mapping}
        m.kr_codes = spy
        return self

    def __exit__(self, *exc):
        import trade.stock_link as m
        m.kr_codes = self._real
        return False


@contextlib.contextmanager
def _stub_master(mapping: dict):
    """**신원 오라클**(`price_provider._load_krx_master`)을 경계에서 갈아끼운다.

    2026-09-22 에 `kr_codes` 가 시세 사다리(`resolve_codes`)에서 거래소 목록으로
    옮겨 왔다 — 사다리는 `_DIRECT_CODES` 로 상장사를 모회사로 바꾸므로 신원에
    쓰면 양방향으로 틀린다(독립 리뷰 실측 · #34·#55).
    ⚠️ 사다리도 **같이 막는다** — 오라클을 되돌리는 변형이 운영자 홈 상태로
    우연히 통과하는 길을 없앤다(#139 2차 그물 · #30·#312·#344·#384)."""
    import trade.price_provider as pp
    realm, realr = pp._load_krx_master, pp.resolve_codes
    try:
        pp._load_krx_master = lambda: dict(mapping)
        pp.resolve_codes = lambda names, **kw: {}
        yield
    finally:
        pp._load_krx_master, pp.resolve_codes = realm, realr


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

    def test_tokyo_code_off_the_japan_board_needs_the_jpx_list(self):
        """혼합 보드의 도쿄 코드 — JPX 목록이 **이 코드를 이 이름으로** 적을 때만
        `.T`(2026-09-23). 목록 값의 모양은 JPX 실측 행(`KYOKUYO CO.,LTD.`)을 따른
        재구성이다(코드↔회사 신원은 ④ yfinance 실측, #393)."""
        m = {"6976": "TAIYO YUDEN CO.,LTD."}
        self.assertEqual(sl.lookup_query("6976", "Taiyo Yuden", jp_master=m),
                         "6976.T")
        self.assertEqual(sl.lookup_query("6976", "Taiyo Yuden"), "",
                         "목록 없이는 종전처럼 평문")
        self.assertEqual(sl.lookup_query("6976", "Formosa Widget", jp_master=m), "",
                         "4자리는 대만 코드와 모양이 같다 — 이름이 안 맞으면 평문(#34)")
        self.assertEqual(sl.lookup_query("403870", "HPSP", jp_master=m), "",
                         "JPX 목록은 6자리를 확인하지 않는다")

    def test_jp_confirms_prefix_rule(self):
        """캡션은 이름을 줄여 쓴다 — 캡션 토큰이 JPX 영문명 토큰의 **앞부분**이면
        같은 회사. 반대 방향(캡션이 더 김)은 다른 회사다."""
        c = sl.jp_confirms
        m = {"285A": "Kioxia Holdings Corporation",       # ④ yfinance 실측 longName
             "8035": "Tokyo Electron Limited",              # ④ 실측
             "1301": "KYOKUYO CO.,LTD."}                    # ⑥ JPX 실측 행
        self.assertTrue(c("Kioxia", "285A", m))
        self.assertTrue(c("Kioxia Holdings Corporation", "285A", m))
        self.assertTrue(c("Kyokuyo", "1301", m), "대소문자·법인형태 무시")
        self.assertTrue(c("Kyokuyo Co., Ltd.", "1301", m), "양쪽 법인형태를 뗀다")
        self.assertTrue(c("ＫＹＯＫＵＹＯ", "1301", m), "전각도 같은 이름(NFKC)")
        self.assertTrue(c("Kioxia", "285a", m), "코드 대소문자")
        self.assertFalse(c("Tokyo Electron Device", "8035", m),
                         "캡션이 더 길면 다른 회사다")
        self.assertFalse(c("Tokyo", "8035", m),
                         "한 낱말이 앞부분과 같다고 같은 회사가 아니다 — 생략은 "
                         "지주사 꼬리만(독립 리뷰 2026-09-23)")
        self.assertFalse(c("Asia", "0002", {"0002": "Asia Pile Holdings Corporation"}),
                         "남는 꼬리에 지주사 표기 밖의 낱말(pile)이 있다")
        self.assertTrue(c("SoftBank", "0003", {"0003": "SoftBank Group Corp."}),
                        "지주사 꼬리(group)만 남으면 생략을 허용한다")
        self.assertTrue(c("Kyokuyo", " 1301 ", m), "코드의 앞뒤 공백")
        self.assertFalse(c("Kiox", "285A", m), "글자가 아니라 **토큰** 단위다")
        self.assertFalse(c("Kioxia", "285B", m), "목록에 없는 코드")
        self.assertFalse(c("", "285A", m))
        self.assertFalse(c("Kioxia", "285A", None))
        self.assertFalse(c("Co., Ltd.", "1301", m), "법인형태만 남으면 빈 이름이다")

    def test_jp_names_keys_are_normalized_codes(self):
        """키를 호출부 원문으로 돌려주면 `'6976 '` 행이 `jp_confirms` 의
        정규화 조회와 어긋나 조용히 평문이 된다(독립 리뷰 2026-09-23)."""
        import trade.jpx_master as jm
        real = jm.load
        try:
            jm.load = lambda: {"6976": "TAIYO YUDEN CO.,LTD.", "285A": "Kioxia"}
            got = sl.jp_names(["6976 ", "285a", "PENG", "006400", ""])
            self.assertEqual(got, {"6976": "TAIYO YUDEN CO.,LTD.",
                                   "285A": "Kioxia"})
            self.assertEqual(sl.lookup_query("6976 ", "Taiyo Yuden",
                                             jp_master=got), "6976.T")
        finally:
            jm.load = real

    def test_a_failing_identity_list_is_logged_not_silent(self):
        """목록을 못 불러 전 링크가 평문이 되면 **왜** 인지 남긴다(#12·#82) —
        렌더가 5분마다 돌므로 같은 사유는 한 번만."""
        import trade.jpx_master as jm
        real = jm.load
        sl._WARNED.clear()
        try:
            def boom():
                raise RuntimeError("broken import")
            jm.load = boom
            with self.assertLogs(sl.log, level="WARNING") as cm:
                self.assertEqual(sl.jp_names(["6976"]), {})
                self.assertEqual(sl.jp_names(["6723"]), {})
            self.assertEqual(len(cm.output), 1, cm.output)
            self.assertIn("broken import", cm.output[0])
        finally:
            jm.load = real
            sl._WARNED.clear()

    def test_a_failing_krx_list_is_logged_too(self):
        """형제 `kr_codes` 도 같은 자리에서 조용히 {} 였다 — 같은 헬퍼로 말한다
        (#38 한 곳을 고치면 형제도). 뮤테이션 N15 가 이 축이 무가드임을 보였다."""
        import trade.price_provider as pp
        real = pp._load_krx_master
        sl._WARNED.clear()
        try:
            def boom():
                raise OSError("krx master unreadable")
            pp._load_krx_master = boom
            with self.assertLogs(sl.log, level="WARNING") as cm:
                self.assertEqual(sl.kr_codes(["삼성SDI"]), {})
            self.assertIn("KRX 상장 목록", cm.output[0])
        finally:
            pp._load_krx_master = real
            sl._WARNED.clear()

    def test_name_tokens_keep_group_and_holdings(self):
        """`holdings`·`group` 은 법인형태가 아니라 이름이다 — 떼면 SoftBank
        Group 과 SoftBank Corp. 가 같은 이름이 된다(합성 목록)."""
        self.assertEqual(sl._name_tokens("SoftBank Group Corp."),
                         ["softbank", "group"])
        self.assertFalse(sl.jp_confirms("SoftBank Group", "0001",
                                        {"0001": "SoftBank Corp."}))
        self.assertEqual(sl._name_tokens("The Synthetic Works, Ltd."),
                         ["synthetic", "works"], "앞 `the` 도 뗀다")
        self.assertEqual(sl._name_tokens("Shin-Etsu Chemical Co., Ltd."),
                         sl._name_tokens("SHIN-ETSU CHEMICAL CO.,LTD."))

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
        있다고 말한다(#34·#43).

        ⚠️ 2026-09-22 오라클 교체로 다시 썼다(#222) — 스텁을 `resolve_codes`
        에서 **거래소 목록**(`_load_krx_master`)으로 옮겼다. 남는 보장은
        '6자리가 아닌 값은 코드로 인정하지 않는다' 그대로다."""
        with _stub_master({"삼성전자": "005930", "이상한회사": "nm:이상한회사"}):
            got = sl.kr_codes(["삼성전자", "이상한회사"])
        self.assertEqual(got, {"삼성전자": "005930"})

    def test_kr_codes_answers_with_the_caller_s_own_key(self):
        """정규화한 키로 돌려주면 앞뒤 공백이 붙은 이름을 넘긴 렌더러가
        `code_by_name.get(raw_name)` 에서 **조용히** 못 찾는다(링크만 안 생기는
        미스). 호출부 셋이 각자 정규화하면 갈라지므로 여기서 되돌린다(#38).

        ⚠️ 마스터 키는 `.replace(" ", "")` 라 캡션의 내부 공백·`\xa0` 도 여기서
        맞춘다(독립 리뷰 2026-09-22 L2 실측)."""
        with _stub_master({"삼성전자": "005930"}):
            got = sl.kr_codes([" 삼성전자 ", "삼성전자", "삼성 전자",
                               "삼성\xa0전자", "모르는회사"])
        # ⚠️ 앞뒤 `\xa0` 는 `.strip()` 이 이미 먹으므로 **내부**에 둬야 공백
        # 정규화 축이 실제로 발화한다(#91c — 첫 픽스처는 앞쪽에 둬서 눈이 멀었다).
        self.assertEqual(got, {" 삼성전자 ": "005930", "삼성전자": "005930",
                               "삼성 전자": "005930", "삼성\xa0전자": "005930"})

    def test_kr_codes_makes_no_outbound_call(self):
        """렌더는 외부 호출 0 — 신원 오라클은 로컬 파일 + 메모이즈다(#116).

        ⚠️ 옛 판은 `resolve_codes(fetch=False)` 를 단언했다(#222 다시 쓰기).
        지금은 **그 사다리를 아예 안 탄다**는 것이 더 강한 계약이다 — 타면
        `_DIRECT_CODES` 대용이 신원으로 새기 때문이다(독립 리뷰 2026-09-22 B1)."""
        import trade.price_provider as pp
        called = []
        real = pp.resolve_codes
        try:
            pp.resolve_codes = lambda names, **kw: called.append(1) or {}
            with _stub_master({"삼성전자": "005930"}):
                self.assertEqual(sl.kr_codes(["삼성전자"]), {"삼성전자": "005930"})
        finally:
            pp.resolve_codes = real
        self.assertEqual(called, [], "신원 확인이 시세 사다리를 탔다")


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

    def setUp(self):
        """리졸버를 **클래스 전체**에서 경계 격리한다.

        ⚠️ 2026-09-22 에 보드 넷(cn·my·tw·jp)이 KRX 마스터 대조를 쓰게 되면서
        이 클래스의 **모든** 테스트가 `price_provider.resolve_codes` 를 타게
        됐다 — 격리가 없으면 운영자의 `~/.trade` 상태(오버라이드·durable
        캐시·`_DIRECT_CODES`)가 단언을 뒤집는다. 독립 리뷰가 바로 그것을
        Blocking 으로 잡았고(#399), 규율로는 네 번 졌다(#30·#312·#344·#384)
        — 그래서 개별 테스트가 어떻게 렌더하든(헬퍼든 직접이든) 걸리도록
        `setUp` 에 둔다(#119 규율로 기억할 일을 구조로).
        확인 목록이 필요한 테스트는 `self.codes` 를 채운다."""
        import trade.price_provider as pp
        self.codes: dict = {}
        realm, realr = pp._load_krx_master, pp.resolve_codes
        pp._load_krx_master = lambda: dict(self.codes)
        pp.resolve_codes = lambda names, **kw: {}   # 사다리도 막는다(2차 그물)
        self.addCleanup(lambda: setattr(pp, "resolve_codes", realr))
        self.addCleanup(lambda: setattr(pp, "_load_krx_master", realm))
        # JPX 목록(2026-09-23)도 같은 이유로 경계에서 막는다 — 운영자가 빌더를
        # 한 번 돌리면 `~/.trade/jpx_codes.json` 이 도쿄 코드 단언을 뒤집는다.
        # 루트 conftest 리다이렉트가 2차 그물이다. 필요하면 `self.jp` 를 채운다.
        import trade.jpx_master as jm
        self.jp: dict = {}
        self.jp_loads = 0
        realj = jm.load

        def _jp_load():
            self.jp_loads += 1
            return dict(self.jp)
        jm.load = _jp_load
        self.addCleanup(lambda: setattr(jm, "load", realj))

    def test_the_resolver_is_boundary_isolated_in_this_class(self):
        """위 `setUp` 이 실제로 걸렸나 — 이게 없으면 이 클래스의 보드
        테스트가 운영자 홈 상태를 읽는다(#399 Blocking 재발). 모양이 아니라
        **결과**로 잰다(#313)."""
        import trade.price_provider as pp
        self.codes = {"__probe__": "123456"}
        self.assertEqual(pp._load_krx_master(), {"__probe__": "123456"})
        self.assertEqual(pp.resolve_codes(["__probe__"]), {},
                         "시세 사다리도 막혀 있어야 한다 — 신원으로 새면 안 된다")
        import trade.jpx_master as jm
        self.jp = {"9999": "__probe__"}
        self.assertEqual(jm.load(), {"9999": "__probe__"},
                         "JPX 목록도 경계에서 막혀 있어야 한다")

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

    def test_mixed_market_board_links_a_korean_listing_when_the_exchange_confirms(self):
        """혼합시장 보드의 한국 상장사 — **거래소 목록이 확인할 때만** 링크.

        보드의 나라는 **교역 상대국**이지 상장 시장이 아니다: 2026-09-22 실측으로
        `삼성SDI (006400)` 이 **말레이시아 수출** 보드에 실린다. 한 보드가 한
        시장을 선언할 수 없으니 `local=` 만으로는 그 카드가 영원히 평문이었다
        (#171 가드가 '못 만든다'로 끝나면 그 자리가 영원히 비는지 먼저 물을 것).

        확인은 추측이 아니라 대조이고 **이름·코드가 둘 다** 맞아야 한다(#25) —
        한쪽만 보면 CN A주 6자리가 통과하거나 동명 회사가 남의 코드를 얻는다."""
        from trade import (my_stock_exports as mys, cn_stock_exports as cns,
                           jp_stock_exports as jps, tw_monthly_revenue as twr)
        # 전시장 동일 — 한 보드에서만 되는 게 아니다(§UNIVERSAL). 네 보드를 전수로
        # 도는 이유: 배선을 한 곳만 떼는 변형이 나머지 테스트를 전부 통과한다
        # (2026-09-22 실측 M6·M7 생존 — 발화 경로 없는 배선은 없는 것이다, #291).
        boards = (
            (mys, "open_my_stock_db",
             "삼성SDI (006400)\n말레이시아 수출\n26년 8월 Update\n\n수출액 YoY: +97.0%"),
            (cns, "open_cn_stock_db",
             "삼성SDI (006400)\n중국 수출\n26년 8월 Update\n\n수출액 YoY: +1.0%"),
            (jps, "open_jp_stock_db",
             "삼성SDI (006400) 일본 수출 Update\n26년 8월\n\n수출액: YoY +1.0%"),
            (twr, "open_tw_revenue_db",
             "삼성SDI (006400) 월매출\n26년 8월\n\nREV 약 1,000억 TWD\n"
             "MoM +1.0% · YoY +1.0%\n\nhttps://badonion.co.kr/twse-revenue"),
        )
        for mod, opener, cap in boards:
            self.codes = {"삼성SDI": "006400"}
            self.assertIn(("../lookup/006400", "삼성SDI"),
                          _links(self._render(mod, opener, cap)), mod.__name__)
            # 코드가 다르면 확인이 아니다
            self.codes = {"삼성SDI": "999999"}
            self.assertEqual(_links(self._render(mod, opener, cap)), [],
                             f"{mod.__name__}: 코드 불일치인데 링크가 붙었다")
            # 목록에 **이 이름이** 없으면 평문 — 목록이 비어 있어서가 아니라
            # 이름이 안 맞아서다(= '코드만 보는' 변형이 여기서 잡힌다)
            self.codes = {"다른회사": "006400"}
            self.assertEqual(_links(self._render(mod, opener, cap)), [],
                             f"{mod.__name__}: 이름이 목록에 없는데 링크가 붙었다")
            # 마스터 자체가 없는 환경(미빌드·읽기 실패)도 종전처럼 평문
            self.codes = {}
            html = self._render(mod, opener, cap)
            self.assertEqual(_links(html), [], f"{mod.__name__}: 확인 없이 링크")
            self.assertIn("삼성SDI", html, "링크를 안 걸어도 이름은 보인다")

    def test_a_tokyo_code_outside_the_japan_board_stays_plain_without_the_jpx_list(self):
        """JPX 목록이 확인하지 않으면 평문 — KRX 목록이 우겨도 마찬가지.

        ⚠️ 2026-09-23 에 다시 썼다(#222). 옛 판은 "일본판 대조 수단이 이 레포에
        없다" 를 계약으로 못박았는데 `build_jpx_codes` 가 그 수단이 됐다. 남는
        보장 둘: (a) KRX 축(6자리 전용)은 4자리에 닿지 않는다 (b) JPX 확인이
        없으면(마스터 부재) 종전처럼 평문이다.

        `Taiyo Yuden (6976)` 은 **중국 수출** 보드에 온다(2026-09-22 실측)."""
        from trade import cn_stock_exports as cns
        cap = ("Taiyo Yuden (6976)\n중국 수출\n26년 8월 Update\n\n"
               "수출액 YoY: +40.5%")
        self.codes = {"Taiyo Yuden": "6976"}
        self.jp = {}
        html = self._render(cns, "open_cn_stock_db", cap)
        self.assertEqual(_links(html), [])
        self.assertIn("Taiyo Yuden", html)

    def test_mixed_market_board_links_a_tokyo_listing_when_the_jpx_list_confirms(self):
        """혼합 보드의 도쿄 상장사 — JPX 목록이 **코드와 이름을 둘 다** 확인할
        때만 `.T` 링크(2026-09-23, `jp_master_probe` ③ 실측: 중국 보드의
        `Taiyo Yuden (6976)` · 말레이시아 보드의 `Renesas Electronics
        Corporation (6723)` 이 JP 보드에 없었다).

        ⚠️ **네 혼합 보드 전수**다 — 배선을 한 곳만 떼는 변형이 나머지를 통과한다
        (#291). TWSE 월매출 보드는 **일부러 뺐다**(아래 테스트). 목록 값의 대소문자·법인형태 표기는 JPX 실측 행(`KYOKUYO
        CO.,LTD.`)의 모양을 따른 재구성이다(#393)."""
        from trade import (cn_stock_exports as cns, cn_stock_imports as cni,
                           tw_stock_exports as tws, my_stock_exports as mys)
        boards = (
            (cns, "open_cn_stock_db", "Taiyo Yuden",
             "Taiyo Yuden (6976)\n중국 수출\n26년 8월 Update\n\n수출액 YoY: +40.5%"),
            (cni, "open_cn_stock_import_db", "Taiyo Yuden",
             "Taiyo Yuden (6976)\n중국 수입\n26년 8월 Update\n\n수입액 YoY: +9.5%"),
            (tws, "open_tw_stock_db", "Taiyo Yuden",
             "Taiyo Yuden (6976)\n대만 수출\n26년 8월 Update\n\n수출액 YoY: +1.0%"),
            (mys, "open_my_stock_db", "Taiyo Yuden",
             "Taiyo Yuden (6976)\n말레이시아 수출\n26년 8월 Update\n\n"
             "수출액 YoY: +2.0%"),
        )
        for mod, opener, label, cap in boards:
            self.jp = {"6976": "TAIYO YUDEN CO.,LTD."}
            self.assertIn(("../lookup/6976.T", label),
                          _links(self._render(mod, opener, cap)), mod.__name__)
            # 코드는 맞는데 이름이 다르다 = 4자리 충돌(대만 코드 등) — 평문
            self.jp = {"6976": "SOMEONE ELSE CO.,LTD."}
            self.assertEqual(_links(self._render(mod, opener, cap)), [],
                             f"{mod.__name__}: 이름 불일치인데 링크가 붙었다")
            # 이름은 목록에 있는데 **다른 코드** 아래다 — 평문
            self.jp = {"6977": "TAIYO YUDEN CO.,LTD."}
            self.assertEqual(_links(self._render(mod, opener, cap)), [],
                             f"{mod.__name__}: 코드 불일치인데 링크가 붙었다")
            self.jp = {}
            html = self._render(mod, opener, cap)
            self.assertEqual(_links(html), [], f"{mod.__name__}: 목록 없이 링크")
            self.assertIn(label, html)

    def test_the_twse_revenue_board_never_asks_the_jpx_list(self):
        """TWSE 월매출 = 발행사가 전부 대만 상장(데이터 소스 사유) — 도쿄 확인을
        걸면 맞는 링크는 나올 수 없고 틀린 링크만 나올 수 있다(독립 리뷰
        2026-09-23 · #34). 목록이 **확인해 주는** 상태에서도 평문이고, 목록을
        열지조차 않는다."""
        from trade import tw_monthly_revenue as twr
        self.jp = {"6976": "TAIYO YUDEN CO.,LTD."}
        html = self._render(twr, "open_tw_revenue_db",
                            "Taiyo Yuden (6976) 월매출\n26년 8월\n\nREV 약 1,000억 "
                            "TWD\nMoM +1.0% · YoY +1.0%\n\n"
                            "https://badonion.co.kr/twse-revenue")
        self.assertNotIn("../lookup/6976.T", dict(_links(html)))
        self.assertEqual(self.jp_loads, 0)

    def test_the_jpx_list_is_read_once_per_page(self):
        """카드마다 읽으면 행 수만큼 다시 연다(#113) — 그리고 도쿄 코드 모양이
        없는 페이지는 목록을 아예 안 연다(#61)."""
        from trade import my_stock_exports as mys
        self.jp = {"6723": "Renesas Electronics Corporation"}
        with tempfile.TemporaryDirectory() as tmp:
            c = mys.open_my_stock_db(Path(tmp) / "t.db")
            for cap in ("Renesas Electronics Corporation (6723)\n말레이시아 수출\n"
                        "26년 8월 Update\n\n수출액 YoY: +2.0%",
                        "Taiyo Yuden (6976)\n말레이시아 수출\n26년 8월 Update\n\n"
                        "수출액 YoY: +1.0%",
                        "Penguin Solutions (PENG)\n말레이시아 수출\n26년 8월 "
                        "Update\n\n수출액 YoY: +1.0%"):
                mys.ingest(c, cap)
            html = mys.render_html(c)
        self.assertEqual(self.jp_loads, 1, f"페이지당 1회 — {self.jp_loads}회")
        got = dict(_links(html))
        self.assertEqual(got.get("../lookup/6723.T"),
                         "Renesas Electronics Corporation")
        self.assertNotIn("../lookup/6976.T", got, "목록에 없는 코드는 평문")
        self.jp_loads = 0
        html = self._render(mys, "open_my_stock_db",
                            "Penguin Solutions (PENG)\n말레이시아 수출\n26년 8월 "
                            "Update\n\n수출액 YoY: +1.0%")
        self.assertEqual(self.jp_loads, 0, "도쿄 코드 모양이 없으면 목록을 안 연다")

    def test_mixed_board_asks_the_exchange_only_about_bare_six_digit_rows(self):
        """US 심볼 행까지 거래소에 물으면 헛돈다(#61) — 그리고 페이지당 1회(#113).

        ⚠️ **인자**를 본다. 링크 결과만 보면 필터를 지워도 값이 같아 통과한다
        (2026-09-22 실측 N4~N7 생존 — 목록에 없는 이름은 어차피 {} 를 돌려준다).
        ⚠️ **네 보드 전수**다. 한 보드만 재면 나머지 셋에서 지우는 변형이
        통과하고, 그때 `docs/tests.md` 는 그 축을 덮었다고 적는다(#286·#291)."""
        from trade import (my_stock_exports as mys, cn_stock_exports as cns,
                           jp_stock_exports as jps, tw_monthly_revenue as twr)
        boards = (
            (mys, "open_my_stock_db",
             ["삼성SDI (006400)\n말레이시아 수출\n26년 8월 Update\n\n수출액 YoY: +97.0%",
              "Penguin Solutions (PENG)\n말레이시아 수출\n26년 8월 Update\n\n"
              "수출액 YoY: +1.0%"], "../lookup/PENG", "Penguin Solutions"),
            (cns, "open_cn_stock_db",
             ["삼성SDI (006400)\n중국 수출\n26년 8월 Update\n\n수출액 YoY: +1.0%",
              "TTM Technologies (TTMI)\n중국 수출\n26년 8월 Update\n\n수출액 YoY: +1.0%"],
             "../lookup/TTMI", "TTM Technologies"),
            (jps, "open_jp_stock_db",
             ["삼성SDI (006400) 일본 수출 Update\n26년 8월\n\n수출액: YoY +1.0%",
              "Lumentum (LITE) 일본 수출 Update\n26년 8월\n\n수출액: YoY +1.0%"],
             "../lookup/LITE", "Lumentum"),
            (twr, "open_tw_revenue_db",
             ["삼성SDI (006400) 월매출\n26년 8월\n\nREV 약 1,000억 TWD\n"
              "MoM +1.0% · YoY +1.0%\n\nhttps://badonion.co.kr/twse-revenue",
              "TSMC 월매출\n26년 8월\n\nREV 약 2,000억 TWD\nMoM +1.0% · YoY +1.0%\n\n"
              "https://badonion.co.kr/twse-revenue"], "../lookup/TSMC", "TSMC"),
        )
        for mod, opener, caps, other_href, other_label in boards:
            calls = _spy_kr_codes({"삼성SDI": "006400"})
            with calls:
                with tempfile.TemporaryDirectory() as tmp:
                    c = getattr(mod, opener)(Path(tmp) / "t.db")
                    for cap in caps:
                        mod.ingest(c, cap)
                    html = mod.render_html(c)
            self.assertEqual(len(calls.seen), 1,
                             f"{mod.__name__}: 페이지당 1회 — {len(calls.seen)}회")
            self.assertEqual(calls.seen[0], ["삼성SDI"],
                             f"{mod.__name__}: 맨 6자리가 아닌 행까지 물었다")
            got = dict(_links(html))
            self.assertEqual(got.get("../lookup/006400"), "삼성SDI", mod.__name__)
            self.assertEqual(got.get(other_href), other_label, mod.__name__)

    def test_the_identity_oracle_is_the_exchange_list_not_the_price_ladder(self):
        """신원은 **거래소 목록**이 답한다 — 시세 사다리가 아니다.

        `price_provider.resolve_codes` 는 값을 *보여주기* 위한 경로라
        `_DIRECT_CODES` 가 상장사를 모회사로 바꾼다(`코오롱플라스틱 → 120110`
        코오롱인더스트리 — 진짜 코드 138490 의 KIS 시세가 없어 운영자가 고른
        대용이라고 소스 주석이 밝힌다). 그걸 신원에 쓰면 **양방향으로** 틀린다
        (독립 리뷰 2026-09-22 실측): 진짜 코드는 대조가 어긋나 평문이 되고,
        대용 코드는 확인을 통과해 **남의 회사 화면**이 열린다(#34·#55).

        그래서 이 테스트는 사다리를 **일부러 살려 두고**(`resolve_codes` 가
        대용을 돌려주게) 결과가 마스터 값이어야 함을 잰다 — 오라클을 되돌리는
        변형이 여기서 잡힌다(#91b 재는 대상이 맞나)."""
        import trade.price_provider as pp
        real, realm = pp.resolve_codes, pp._load_krx_master
        try:
            pp.resolve_codes = lambda names, **kw: {"코오롱플라스틱": "120110"}
            pp._load_krx_master = lambda: {"코오롱플라스틱": "138490",
                                           "SK하이닉스": "000660"}
            got = _sl.kr_codes(["코오롱플라스틱"])
            self.assertEqual(got, {"코오롱플라스틱": "138490"},
                             "시세 대용(120110)이 신원으로 새어 나왔다")
            # 표기 변형(`_NAME_ALIASES`)은 신원을 안 바꾸므로 푼다
            self.assertEqual(_sl.kr_codes(["Sk하이닉스"]), {"Sk하이닉스": "000660"})
            # 비-ASCII 공백이 끼어도 마스터 키와 맞춘다(키는 호출부 원문 그대로)
            self.assertEqual(_sl.kr_codes(["\xa0SK 하이닉스"]),
                             {"\xa0SK 하이닉스": "000660"})
            # 마스터가 없으면 아무것도 확인하지 않는다(= 전 칩 평문)
            pp._load_krx_master = lambda: {}
            self.assertEqual(_sl.kr_codes(["코오롱플라스틱"]), {})
        finally:
            pp.resolve_codes, pp._load_krx_master = real, realm

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

        ⚠️ 스텁은 `_stub_master`(리졸버 **경계**)로 건다 — 한 단만 갈면
        운영자의 `~/.trade` 상태가 단언을 뒤집는다(그 docstring 참조)."""
        from trade import kr_stock_imports as kri
        cap = ("8월 수입 한국\n\n▶️ 삼성전자 — 반도체 장비\n"
               "26년08월: $158.5M (+105.3% YoY) (+11.8% MoM)")
        with _stub_master({"삼성전자": "005930"}):
            html = self._render(kri, "open_kr_stock_import_db", cap)
        self.assertIn(("../lookup/005930", "삼성전자"), _links(html))
        with _stub_master({}):
            bare = self._render(kri, "open_kr_stock_import_db", cap)
        self.assertEqual(_links(bare), [], "못 푼 회사에 링크를 만들면 죽은 링크다")
        self.assertIn("삼성전자", bare)
        self.assertIn(".sl-link{", bare, "CSS 는 링크 유무와 무관하게 정의된다")

    def test_name_resolution_is_one_call_per_page(self):
        """카드마다 부르면 이름표를 행 수만큼 다시 조립한다(#113).

        ⚠️ 2026-09-22 오라클 교체로 다시 썼다(#222) — 옛 판은 `resolve_codes`
        호출 수를 셌는데 이제 그 사다리를 안 탄다. 계약("페이지당 1회 ·
        이 이름들만")은 그대로이므로 **`kr_codes` 자체**를 스파이한다."""
        from trade import kr_stock_imports as kri
        calls = _spy_kr_codes({"삼성전자": "005930"})
        with calls:
            with tempfile.TemporaryDirectory() as tmp:
                c = kri.open_kr_stock_import_db(Path(tmp) / "t.db")
                for nm in ("삼성전자", "SK하이닉스", "LG전자"):
                    # 픽스처는 **제품 삽입 경로**로 심는다(#323).
                    kri.ingest(c, f"8월 수입 한국\n\n▶️ {nm} — 반도체 장비\n"
                                  "26년08월: $158.5M (+105.3% YoY)")
                kri.render_html(c)
        self.assertEqual(len(calls.seen), 1,
                         f"페이지당 1회여야 한다 — {len(calls.seen)}회")
        self.assertEqual(sorted(calls.seen[0]), ["LG전자", "SK하이닉스", "삼성전자"])

    def test_export_board_resolves_only_the_codeless_rows_once(self):
        """한국 수출 보드는 금액판(합성키) 행만 이름으로 푼다 — 코드가 이미
        있는 행까지 넘기면 조회가 헛돈다(#61). 그리고 페이지당 1회(#113)."""
        from trade import kr_stock_exports as krs
        calls = _spy_kr_codes({"LS ELECTRIC": "010120"})
        with calls:
            with tempfile.TemporaryDirectory() as tmp:
                c = krs.open_kr_stock_db(Path(tmp) / "t.db")
                krs.ingest(c, "HPSP (403870)\n한국 수출\n26년 7월 Update\n\n"
                              "수출액 YoY: +260.2%")
                krs.ingest(c, "8월 수출 한국\n\n▶️ LS ELECTRIC — 배전반\n"
                              "26년08월: $158.5M (+105.3% YoY)")
                html = krs.render_html(c)
        self.assertEqual(len(calls.seen), 1,
                         f"페이지당 1회여야 한다 — {len(calls.seen)}회")
        self.assertEqual(calls.seen[0], ["LS ELECTRIC"],
                         "코드가 이미 있는 행은 조회에 안 넘긴다")
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
        # ⚠️ 스텁은 **리졸버 경계**(`_stub_master`) — 한 단만 갈면 운영자의
        # `~/.trade` 상태·`_DIRECT_CODES` 한 줄이 단언을 뒤집는다(그 docstring).
        from trade import reference_book as R
        with _stub_master(codes):
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
