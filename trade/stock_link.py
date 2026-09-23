"""종목 카드 제목 → NOAH `/lookup/<질의>` 종목분석 화면 링크(단일 출처).

사용자 2026-09-22: "수출입대시보드에 종목별로 각 나라의 수출입데이터에서
종목제목을 클릭하면 종목화면으로 가게해줘. 종목이 있는 대시보드에 종목들은
모두 적용해줘."

⚠️ **왜 모듈 하나인가.** 종목 카드를 그리는 `_card_html` 은 오늘 여섯 벌이다
(`cn_stock_flow`(cns·cni·tws) · `kr_stock_exports` · `jp_stock_exports` ·
`my_stock_exports` · `tw_monthly_revenue` · `kr_company_flow`). 질의 규칙을
카드마다 적으면 같은 종목이 보드마다 다른 곳으로 가고, 한 곳을 고쳐도 나머지가
옛 규칙에 남는다(#38·#147). 규칙은 여기 한 벌이고 카드는 부르기만 한다.

⚠️ **URL 은 상대경로**다(`../lookup/…`). 수출입 페이지는 NOAH 프록시의
`/trade/…`(토큰이 있으면 `/<token>/trade/…`) 아래에서 열리므로, 절대경로로
적으면 토큰이 떨어져 404 다(#359). NOAH 프록시(`bot.dashboard_server.
_rewrite_trade_html`)가 `../media/` 와 **같은 규칙**으로 이 접두를 토큰 포함
절대경로로 바꾼다 — 그래야 동결 아카이브처럼 한 단계 아래 페이지에서도 깊이와
무관하게 맞는다.
⚠️ **못 보는 축**(#274): trade 백엔드(8765)에 직접 붙으면 NOAH `/lookup` 이
없으므로 이 링크는 그 경로에서만 죽는다. 이 레포의 운영 규약상 전 대시보드는
NOAH 서버 한 자격으로 서빙된다(§Stance) — 그 경로를 정본으로 본다.

질의를 **만들 수 없으면 링크를 만들지 않는다**(평문). 빈 href·틀린 질의는
그 페이지를 다시 열거나 남의 종목을 여는 죽은 링크다(#144·#43 — 지어내지
않는다, #165).
"""
from __future__ import annotations

import html as _html
import logging
import re
import urllib.parse as _up

log = logging.getLogger(__name__)
_WARNED: set = set()


def _warn_once(what: str, exc: BaseException) -> None:
    """신원 목록을 못 불러 전 링크가 평문이 될 때 — **조용히** 평문이 되면
    '없는 링크' 와 '못 읽은 목록' 이 같은 화면이다(#12·#82). 렌더가 5분마다
    돌므로 같은 사유는 프로세스당 한 번만 적는다."""
    key = f"{what}:{type(exc).__name__}"
    if key not in _WARNED:
        _WARNED.add(key)
        log.warning("%s 를 못 불러 링크가 평문이 된다: %s: %s",
                    what, type(exc).__name__, exc)

# 카드 제목 링크 CSS — 쓰는 페이지의 번들에 **반드시 같이** 넣는다(#201·#273:
# 클래스만 쓰고 정의를 빠뜨리면 조용히 스타일이 빠진다). 색은 본문 그대로 두고
# 밑줄만 hover 에 둔다 — 제목이 파랗게 변하면 카드 헤더 위계가 깨진다.
# ⚠️ 색 토큰을 참조하지 않는다 — `trade/dashboard.py` 번들엔 `--muted` 가 없어
# 미정의 토큰은 늘 리터럴 폴백으로 떨어진다(#355·#273). 점선 밑줄은 글자색을
# 그대로 따라가므로(currentColor 기본) 대비 가드도 그대로다.
LINK_CSS = (".sl-link{color:inherit;cursor:pointer;"
            "text-decoration:underline dotted;text-underline-offset:3px;"
            "text-decoration-thickness:1px}"
            ".sl-link:hover{text-decoration:underline solid}")

# 프록시가 토큰 포함 절대경로로 바꾸는 접두(= `_rewrite_trade_html` 의 짝).
LOOKUP_PREFIX = "../lookup/"

# NOAH `/lookup/<q>` 는 해석 실패 시 질의를 그대로 티커로 보는데, 그 관문이
# `bot.dashboard_server._TICKER_RE` 다. 여기서 만드는 질의는 그 문자셋 안에
# 있어야 한다 — 밖이면 검색실패 페이지로 간다(죽은 링크와 같다).
_TICKER_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,9}$")
_ALPHA_ONLY = re.compile(r"^[A-Za-z]{1,10}$")          # 미국 등 영문 심볼
_KR_CODE = re.compile(r"^\d{6}$")                       # KRX 6자리(무접미)
# 도쿄 신·구 표기: 4자리 숫자(6857) 또는 3자리+영문자(285A, Kioxia 실측).
_JP_CODE = re.compile(r"^\d{3}[0-9A-Za-z]$")
_SUFFIXED = (".KS", ".KQ", ".T", ".TW", ".TWO", ".SS", ".SZ", ".BJ", ".HK")


def lookup_query(ticker: str = "", name: str = "", *,
                 local: str = "", kr_master: dict | None = None,
                 jp_master: dict | None = None) -> str:
    r"""`/lookup/<q>` 에 넣을 질의. 만들 수 없으면 ""(= 링크 없음).

    순서에 근거가 있다(전부 실측 픽스처 기준 — 지어낸 규칙이 아니다):

    1. **접미사가 이미 있으면 그대로.** 원천이 시장을 밝힌 것이라 추측이 없다.
    2. **영문자만인 티커는 그대로.** 관측된 보드 대부분이 미국 상장 심볼이다
       (`TTMI`·`DELL`·`TXN`·`ASX`·`MXL`·`PTON`·`LITE`·`COHU`·`PENG`) —
       NOAH 해석기가 티커 모양이면 그대로 통과시킨다.
    3. **맨 6자리 숫자는 KR 로 확인됐을 때만.** 확인 수단이 둘이다.
       (a) 보드가 `local="KR"` 로 **선언**했거나,
       (b) `kr_master`(KRX 상장 이름→코드 목록)가 이 **이름을 이 코드로** 푼다.
       (b)는 추측이 아니라 **거래소 목록과의 대조**다 — 이름과 코드가 **양쪽
       다** 맞을 때만 인정하므로, 이름만 우연히 겹치거나 코드만 겹치는 경우는
       통과하지 못한다(#25 '있다'만 묻지 말고 반대 증거도 볼 것).
       ⚠️ **왜 (b)가 필요한가.** 보드의 나라는 **교역 상대국**이지 상장 시장이
       아니다 — 말레이시아 수출 보드에 `삼성SDI (006400)`, 중국 수출 보드에
       `Taiyo Yuden (6976)` 이 실린다(2026-09-22 실측). 한 보드가 한 시장을
       선언할 수 없으므로 (a)만으로는 그 카드들이 영원히 평문이다(#171 가드가
       '못 만든다'로 끝나면 그 자리가 영원히 비는지 먼저 물을 것).
       ⚠️ **그래도 추측은 안 한다.** 맨 6자리를 무조건 KR 로 보면 CN A주 코드
       (`600519`·`000001` — `.SS`/`.SZ` 가 맞다)가 `600519.KS`(**남의 회사
       분석 화면**)로 간다. 중국·대만 보드의 티커 정규식이
       `[A-Za-z0-9.\-]{2,10}` 이라 그 모양을 **받는다**(실측). 확인이 없으면
       링크가 없다 — 평문이 틀린 링크보다 낫다(#144·#43).
       KS/KQ 판별은 여기서 하지 않는다 — KRX 목록을 가진 NOAH 쪽
       (`resolve_name_to_ticker`)이 한다(렌더가 그 목록을 받아오면 페이지마다
       네트워크가 붙는다, #116).
    4. **영문 별칭이 이름을 풀면 이름을 질의로.** `Advantest → 6857.T` 처럼
       NOAH 가 **같은 표**(`bot.market.resolve_english_alias`)로 푸는 것만
       인정한다 — 우리가 시장을 추측하는 게 아니라 이미 측정된 매핑이다.
    5. **도쿄 코드 모양**(6857·285A 실측)이면 `.T` — 단 도쿄로 **확인됐을 때만**.
       (a) 보드가 `local="JP"` 로 선언했거나,
       (b) `jp_master`(JPX 상장 목록 {코드: 영문명}, `build_jpx_codes`)가 이
       **코드를 이 이름의 회사로** 적고 있을 때(`jp_confirms`). 3(b)의 일본판이다
       (2026-09-23 — `jp_master_probe` VM 실측으로 원천을 고른 뒤).
       ⚠️ 4자리 숫자는 **대만(TWSE) 코드와 모양이 같다**(`TSMC (2330)`). 확인
       없이 `.T` 를 붙이면 대만 보드의 카드가 도쿄의 **남의 회사**로 간다 — 그래서
       코드만이 아니라 이름까지 맞아야 한다(#25·#34).
    6. 그 밖 → "" (평문).

    `local` 은 **보드가 선언하는 축**이고 `kr_master`·`jp_master` 는 **거래소
    목록이 답하는 축**이다 — 둘 다 없으면 맨 숫자는 질의를 못 만든다(#34 한
    규칙이 두 시장을 대표하면 한쪽은 반드시 거짓말).
    """
    mkt = (local or "").strip().upper()
    t = (ticker or "").strip()
    nm = (name or "").strip()
    if t:
        up = t.upper()
        if up.endswith(_SUFFIXED) and _TICKER_OK.match(t):
            return up
        if _ALPHA_ONLY.match(t):
            return up
        if _KR_CODE.match(t) and (mkt == "KR" or kr_confirms(nm, t, kr_master)):
            return t
    if nm and _alias_hit(nm):
        return nm
    if t and _JP_CODE.match(t) and (mkt == "JP"
                                    or jp_confirms(nm, t, jp_master)):
        return t.upper() + ".T"
    return ""


def kr_confirms(name: str, code: str, master: dict | None) -> bool:
    """KRX 상장 목록이 **이 이름을 이 코드로** 푸는가 — 양쪽 다 맞을 때만 True.

    ⚠️ 한쪽만 보면 안 된다. 코드만 보면 CN A주 6자리가 통과하고, 이름만 보면
    동명 회사가 남의 코드를 얻는다. 둘의 **일치**가 곧 확인이다(#25).
    ⚠️ 못 보는 축(#274): 다른 시장 회사가 KRX 상장사와 이름이 **정확히** 같고
    캡션 코드까지 그 KRX 코드와 같으면 통과한다. 6자리 완전일치 + 이름 완전일치가
    동시에 나야 하므로 관측된 적은 없지만, 재지 않았으므로 없다고 하지 않는다(#165).
    """
    if not (name and code and master):
        return False
    c = master.get(name) or master.get(name.strip())
    return bool(c) and str(c) == code


# 회사명 끝의 법인 형태 — JPX 영문명은 `KYOKUYO CO.,LTD.` · `Hokuryo Co.,Ltd.`
# 처럼 표기가 섞여 온다(⑥ 실측). 캡션은 보통 이걸 뗀다(`Taiyo Yuden`).
# ⚠️ `holdings`·`group` 은 **넣지 않는다** — 법인 형태가 아니라 이름이다
# (SoftBank Group ≠ SoftBank Corp.). 떼면 다른 회사가 같은 이름이 된다.
_LEGAL_TAIL = frozenset({"co", "ltd", "limited", "corp", "corporation", "inc",
                         "incorporated", "company", "kk", "plc"})
# 캡션이 **생략해도 되는** 꼬리 — 실측한 `holdings` 하나뿐이다(`Kioxia` ↔
# `Kioxia Holdings Corporation`, ④). 그 밖의 낱말이 남으면 다른 회사일 수 있다:
# `Tokyo` 는 `Tokyo Electron` 이 아니다(독립 리뷰 2026-09-23 — 한 낱말 캡션이 같은
# 낱말로 시작하는 아무 회사에나 맞던 규칙을 좁혔다). ⚠️ `group` 은 **넣지 않는다**
# — 바로 위 이유 그대로 `SoftBank Corp.`(9434) 캡션이 `SoftBank Group Corp.`
# (9984)에 맞아 버린다(2차 리뷰). 재지 않은 약어(`hd` 등)도 넣지 않는다(#165).
_OMITTABLE_TAIL = frozenset({"holdings"})


def _name_tokens(name: str) -> list[str]:
    """비교용 토큰 — NFKC(전각→반각)·소문자·영숫자 외 전부 구분자, 앞 `the` 와
    끝의 법인 형태를 뗀다. `Shin-Etsu` 와 `SHIN-ETSU` 가 같은 토큰이 된다."""
    import unicodedata
    toks = re.sub(r"[^0-9a-z]+", " ",
                  unicodedata.normalize("NFKC", name or "").casefold()).split()
    if toks and toks[0] == "the":
        toks = toks[1:]
    while toks and toks[-1] in _LEGAL_TAIL:
        toks.pop()
    return toks


def jp_confirms(name: str, code: str, master: dict | None) -> bool:
    """JPX 상장 목록이 **이 코드를 이 이름의 회사로** 적고 있나(`kr_confirms` 의
    일본판). 코드로 목록의 영문명을 찾고, 법인형태를 뗀 토큰이 **같거나** 목록
    쪽에 지주사 꼬리(`_OMITTABLE_TAIL`)만 더 붙어 있을 때 True.

    ⚠️ 캡션은 이름을 줄여 쓴다(`Kioxia` ↔ `Kioxia Holdings Corporation`, ④ 실측)
    — 그래서 지주사 꼬리는 생략을 허용한다. 그 밖의 낱말이 남으면 거부한다:
    `Tokyo` 는 `Tokyo Electron` 이 아니고, 캡션이 **더 긴** `Tokyo Electron
    Device` 도 8035 가 아니다.
    ⚠️ 코드만 보면 안 된다. 4자리는 대만 코드와 모양이 같아 `TSMC (2330)` 이
    도쿄 2330 의 남의 회사로 간다 — 이름이 그걸 가른다(#25·#34).
    ⚠️ 못 보는 축(#274): 다른 거래소 회사가 **같은 숫자**를 쓰고 법인형태를 뗀
    영문명까지 도쿄 회사와 같으면 통과한다. 관측된 적은 없지만 재지 않았으므로
    없다고 하지 않는다(#165).
    """
    if not (name and code and master):
        return False
    listed = master.get(code.strip().upper()) or master.get(code)
    if not listed:
        return False
    cap, lst = _name_tokens(name), _name_tokens(listed)
    if not cap or lst[:len(cap)] != cap:
        return False
    return set(lst[len(cap):]) <= _OMITTABLE_TAIL


def _alias_hit(name: str) -> bool:
    """NOAH 영문 별칭표가 이 이름을 푸는가(순수 dict 조회 — 네트워크 0).

    ⚠️ 우리가 매핑을 **복제하지 않는다** — 복제하면 두 화면이 갈라진다(#38).
    표가 없거나 import 가 막힌 환경(트레이드 단독 체크아웃)에서는 False 로
    떨어져 링크만 안 생긴다(없는 링크 > 틀린 링크)."""
    try:
        from bot.market import resolve_english_alias
    except Exception:                                     # noqa: BLE001
        return False
    try:
        return bool(resolve_english_alias(name))
    except Exception:                                     # noqa: BLE001
        return False


def lookup_href(ticker: str = "", name: str = "", *,
                local: str = "", query: str = "",
                kr_master: dict | None = None,
                jp_master: dict | None = None) -> str:
    """`../lookup/<질의>` (만들 수 없으면 ""). `query` 를 주면 그걸 그대로 쓴다
    (이름→코드 리졸버를 이미 가진 호출부용). `local` = 그 보드의 맨 숫자 코드가
    어느 시장 것인지, `kr_master` = KRX 상장 이름→코드 목록, `jp_master` = JPX
    상장 코드→영문명 목록(`lookup_query` 참조)."""
    q = (query or "").strip() or lookup_query(ticker, name, local=local,
                                              kr_master=kr_master,
                                              jp_master=jp_master)
    if not q or not _TICKER_OK.match(q):
        return ""
    return LOOKUP_PREFIX + _up.quote(q, safe="")


def linked_name(name: str, href: str) -> str:
    """이스케이프된 종목명 — href 가 있으면 `<a>` 로 감싼다(없으면 평문).

    카드 헤더의 `<span class="…-item">` **안쪽**만 만든다 — 바깥 마크업·CSS 를
    안 건드려야 여섯 보드의 레이아웃이 그대로다(§미니멀 코드).

    ⚠️ 두 인자 **모두** 이스케이프한다. `name` 은 텔레그램 캡션에서 온 남의
    문자열이고, `href` 는 이 모듈의 `lookup_href` 가 주면 퍼센트 인코딩이라
    오늘은 no-op 이지만 이 함수는 **아무 href 나 받는 공개 헬퍼**다 — 속성값
    이스케이프는 그 시그니처의 계약이지 `lookup_href` 출력의 성질이 아니다."""
    label = _html.escape(str(name or ""))
    if not href:
        return label
    return (f'<a class="sl-link" href="{_html.escape(href)}" '
            f'title="종목분석 화면으로">{label}</a>')


def kr_codes(names) -> dict[str, str]:
    """{회사명: KRX 상장 6자리} — **신원 확인 전용**(딥링크가 쓴다).
    **한 페이지에 한 번** 부른다(#113 루프 안에서 부르면 카드 수만큼 다시 읽는다).

    ⚠️ **시세 사다리(`price_provider.resolve_codes`)를 쓰면 안 된다.** 그쪽은
    값을 *보여주기* 위한 경로라 `_DIRECT_CODES` 가 상장사를 **모회사로 바꿔**
    놓는다 — `코오롱플라스틱 → 120110`(코오롱인더스트리; 진짜 코드 138490 의
    KIS 시세가 없어 운영자가 고른 대용이라고 소스 주석이 밝힌다) ·
    `HD현대미포조선 → 329180` 등 25건. 시세엔 옳지만 신원엔 양방향으로 틀린다
    (2026-09-22 실측): 캡션이 **진짜 코드**를 들고 오면 대조가 어긋나 멀쩡한
    상장사가 평문이 되고, 캡션이 **대용 코드**면 확인을 통과해 **남의 회사
    분석 화면**이 열린다(#34 한 표가 두 뜻을 대표하면 한쪽은 반드시 거짓말).
    운영자 `/map` 오버라이드도 같은 슬롯이라 의도(신원 ↔ 시세대용)를 가를 수
    없어 보지 않는다 — 재지 않은 것을 신원 근거로 쓰지 않는다(#165).

    신원은 **거래소 목록**만 답한다(#86): `_load_krx_master()`(build_krx_codes
    가 만든 전 상장종목 이름→코드, 공백제거 키) + `_NAME_ALIASES`(표기 변형 —
    `Sk하이닉스 → SK하이닉스` 류라 신원을 안 바꾼다). 둘 다 로컬·메모이즈라
    **외부 호출 0** 이고, 마스터가 없으면 {} 라 전 칩이 평문으로 렌더된다.

    ⚠️ 돌려주는 키는 **호출부가 준 그 문자열**이다 — 정규화한 키로 돌려주면
    렌더러가 `code_by_name.get(raw_name)` 로 찾을 때 조용히 못 찾는다.
    """
    orig = [str(n) for n in names if n]
    if not orig:
        return {}
    try:
        from trade.price_provider import _NAME_ALIASES, _load_krx_master
        master = _load_krx_master()
    except Exception as exc:                              # noqa: BLE001
        _warn_once("KRX 상장 목록", exc)
        return {}
    if not master:
        return {}
    out: dict[str, str] = {}
    for n in orig:
        if n in out:
            continue
        k = n.strip()
        for cand in (k, _NAME_ALIASES.get(k, k)):
            c = master.get(cand.replace(" ", "")) or master.get(_ws(cand))
            if c and _KR_CODE.match(str(c)):
                out[n] = str(c)
                break
    return out


def jp_names(codes) -> dict[str, str]:
    """{코드: JPX 영문명} — **신원 확인 전용**(`jp_confirms` 가 쓴다).
    **한 페이지에 한 번** 부른다(#113). 도쿄 코드 모양만 묻는다(US 심볼·6자리
    행까지 물으면 헛돈다, #61).

    원천은 `build_jpx_codes` 가 만든 로컬 마스터다 — 렌더는 **외부 호출 0**
    이고(#116), 마스터가 없으면 {} 라 전 칩이 종전처럼 평문이다.
    ⚠️ 키는 **정규화한 코드**(앞뒤 공백 제거·대문자)다 — `kr_codes`(이름 키)와
    달리 `jp_confirms` 가 정규화해 찾으므로, 원문 키로 돌려주면 `'6976 '` 같은
    행이 조용히 못 찾는다(독립 리뷰 2026-09-23)."""
    want = {str(c).strip().upper() for c in codes
            if c and _JP_CODE.fullmatch(str(c).strip())}
    if not want:
        return {}
    try:
        from trade.jpx_master import load
        master = load()
    except Exception as exc:                              # noqa: BLE001
        _warn_once("JPX 상장 목록", exc)
        return {}
    return {c: master[c] for c in want if master.get(c)}


def _ws(s: str) -> str:
    """공백 **전부** 제거 — 마스터 키는 `.replace(" ", "")` 라 캡션에 `\xa0`
    같은 비-ASCII 공백이 끼면 그 키와 안 맞는다(독립 리뷰 2026-09-22 실측)."""
    return re.sub(r"\s+", "", s)
