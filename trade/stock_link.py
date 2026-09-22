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
import re
import urllib.parse as _up

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
                 local: str = "") -> str:
    r"""`/lookup/<q>` 에 넣을 질의. 만들 수 없으면 ""(= 링크 없음).

    순서에 근거가 있다(전부 실측 픽스처 기준 — 지어낸 규칙이 아니다):

    1. **접미사가 이미 있으면 그대로.** 원천이 시장을 밝힌 것이라 추측이 없다.
    2. **영문자만인 티커는 그대로.** 관측된 보드 대부분이 미국 상장 심볼이다
       (`TTMI`·`DELL`·`TXN`·`ASX`·`MXL`·`PTON`·`LITE`·`COHU`·`PENG`) —
       NOAH 해석기가 티커 모양이면 그대로 통과시킨다.
    3. **`local="KR"` 보드의 맨 6자리 숫자는 그대로.** KS/KQ 판별은 KRX 목록을
       가진 NOAH 쪽(`resolve_name_to_ticker`)이 한다 — 렌더가 그 목록을
       받아오면 페이지마다 네트워크가 붙는다(#116).
       ⚠️ **보드가 선언해야 한다.** 맨 6자리를 무조건 KR 로 보면 CN A주 코드
       (`600519`·`000001` — `.SS`/`.SZ` 가 맞다)가 `600519.KS` 로 간다. 중국·
       대만 보드의 티커 정규식이 `[A-Za-z0-9.\-]{2,10}` 이라 그 모양을 **받는다**
       (실측 — 실제로 A주 회사가 올라오는지는 안 쟀다, #165). 링크가 없으면
       평문이지만 틀린 링크는 **남의 회사 분석 화면**을 연다(#144·#43).
    4. **영문 별칭이 이름을 풀면 이름을 질의로.** `Advantest → 6857.T` 처럼
       NOAH 가 **같은 표**(`bot.market.resolve_english_alias`)로 푸는 것만
       인정한다 — 우리가 시장을 추측하는 게 아니라 이미 측정된 매핑이다.
    5. **`local="JP"` 보드의 도쿄 코드 모양**이면 `.T`(6857·285A 실측).
    6. 그 밖 → "" (평문). 이름이 한글이어도 여기선 안 쓴다 — 한글 이름의
       코드 해석은 `kr_company_flow` 처럼 **이미 이름→코드 리졸버를 가진**
       호출부가 코드를 넘겨 주는 쪽이 정확하다(#150 이미 부르는 호출이
       답을 갖고 있나).

    `local` 은 **보드가 선언하는 축**이다 — 맨 숫자 코드가 어느 시장 것인지는
    캡션이 아니라 그 보드가 안다(#34 한 규칙이 두 시장을 대표하면 한쪽은 반드시
    거짓말). 선언하지 않은 보드에서 맨 숫자는 질의를 못 만든다 = 평문.
    """
    mkt = (local or "").strip().upper()
    t = (ticker or "").strip()
    if t:
        up = t.upper()
        if up.endswith(_SUFFIXED) and _TICKER_OK.match(t):
            return up
        if _ALPHA_ONLY.match(t):
            return up
        if mkt == "KR" and _KR_CODE.match(t):
            return t
    nm = (name or "").strip()
    if nm and _alias_hit(nm):
        return nm
    if mkt == "JP" and t and _JP_CODE.match(t):
        return t.upper() + ".T"
    return ""


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
                local: str = "", query: str = "") -> str:
    """`../lookup/<질의>` (만들 수 없으면 ""). `query` 를 주면 그걸 그대로 쓴다
    (이름→코드 리졸버를 이미 가진 호출부용). `local` = 그 보드의 맨 숫자 코드가
    어느 시장 것인지(`lookup_query` 참조)."""
    q = (query or "").strip() or lookup_query(ticker, name, local=local)
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
    """{회사명: KRX 6자리 코드} — **한 페이지에 한 번** 부른다(#113 루프 안에서
    부르면 카드 수만큼 캐시 파일을 다시 읽는다).

    이름→코드 해석은 이 레포가 **이미 갖고 있다** — 수출입 대시보드의 시세 칩이
    쓰는 `price_provider.resolve_codes`(운영자 /map 오버라이드 → 직접코드 →
    KRX 로컬 마스터 → durable 캐시)가 그것이다(#150 이미 부르는 호출이 답을
    갖고 있나). 여기서 이름 매칭을 새로 짜면 시세 칩이 붙는 회사와 링크가 붙는
    회사가 갈린다(#38).

    ⚠️ `fetch=False` — 렌더는 **캐시만** 읽는다. 외부 호출 0이고, 못 푼 이름은
    링크가 안 생길 뿐이다(워머가 채우면 다음 렌더에 붙는다, #116).
    ⚠️ 6자리 숫자만 인정한다 — 합성키(`nm:회사`)가 코드 자리에 앉으면 화면이
    없는 종목코드를 있다고 말한다(#34·#43).
    ⚠️ 돌려주는 키는 **호출부가 준 그 문자열**이다. `resolve_codes` 는 내부에서
    `n.strip()` 한 키로 돌려주므로, 앞뒤 공백이 있는 이름을 넘긴 렌더러가
    `code_by_name.get(raw_name)` 로 찾으면 조용히 못 찾는다(= 링크만 안 생기는
    조용한 미스). 호출부 셋이 각자 정규화하면 갈라지므로 여기서 되돌린다(#38).
    """
    orig = [str(n) for n in names if n]
    try:
        from trade import price_provider
        raw = price_provider.resolve_codes(orig, fetch=False)
    except Exception:                                     # noqa: BLE001
        return {}
    codes = {k: str(v) for k, v in (raw or {}).items()
             if _KR_CODE.match(str(v or ""))}
    out: dict[str, str] = {}
    for n in orig:
        c = codes.get(n) or codes.get(n.strip())
        if c and n not in out:
            out[n] = c
    return out
