"""TradingView 스캐너 — 5개국(KR·US·JP·TW·CN) 전 종목의 하루치(등락·거래대금·시총·섹터·업종)를
**한 원천·한 분류**로 받는다(사용자 2026-10-12 「국가별 섹터혼합」·「국가별 심리지도」).

왜 이 원천인가 — 나라 사이에 업종을 비교하려면 **같은 업종 분류가 5개국에 다** 있어야 한다.
나라마다 원천(KRX 업종·TWSE 산업코드·finviz)을 쓰면 업종을 손으로 짝지어야 하고 그 표엔 틀린
짝이 조용히 섞인다(#34 같은 이름 다른 시리즈). TradingView 는 FactSet 분류(섹터 ~20 · 업종 ~130)를
전 시장에 **같은 영문 키**로 주고, `lang=ko` 면 한국어 이름(`sector.tr`·`industry.tr`)도 준다 —
사용자가 준 블로그 캡처의 이름(세미컨덕터 · 패키지 소프트웨어 · 생산자 제조 …)이 바로 그 한국어다.

⚠️ 공식 공개 API 가 아니다 — tradingview.com 웹 스크리너가 부르는 그 엔드포인트다. 요청 형식·
필드 이름은 PyPI `tradingview-screener` 3.2.2 가 싣는 웹 요청 본문(`STOCKS_QUERY`)에서 옮겼다
(2026-10-12 패키지 원문 확인). 키 없이 15분 지연 값이라 **장 마감 뒤 한 번** 받는 이 보드엔 충분하다.
이 저장소의 샌드박스에서는 닿지 못해 **실측하지 못했다**(#25·#151) — 그래서 실패는 갈래를 이름으로
말하고(#82) `python -m bot.world_sectors --probe` 가 나라마다 응답 모양을 재 준다.
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("bot.tv_scanner")

URL = "https://scanner.tradingview.com/{market}/scan"

# 우리 시장 코드 → (TradingView market, 화면 이름, 국기). 순서 = 화면 열 순서(사용자 요청 순).
MARKETS: dict[str, tuple[str, str, str]] = {
    "KR": ("korea", "한국", "🇰🇷"),
    "US": ("america", "미국", "🇺🇸"),
    "JP": ("japan", "일본", "🇯🇵"),
    "TW": ("taiwan", "대만", "🇹🇼"),
    "CN_A": ("china", "중국", "🇨🇳"),
}
# 미국 market 은 OTC 까지 담는다 — 장외 종목 수천 개의 값(거래 0·시총 공란)이 섹터 집계를 흐린다.
# 그 밖의 시장은 TradingView market 자체가 정규 거래소만 담는다(거래소 필터 불요).
EXCHANGES: dict[str, list[str]] = {"US": ["NYSE", "NASDAQ", "AMEX"]}

# 판정에 꼭 필요한 열 — 하나라도 원천이 모르면 그 시장 수집은 실패로 말한다.
CORE = ("name", "description", "sector", "industry", "close", "change",
        "Value.Traded", "market_cap_basic")
# 있으면 쓰는 열 — 원천이 모르면 빼고 다시 묻고, 뺐다고 기록한다(#43).
OPTIONAL = ("sector.tr", "industry.tr", "average_volume_30d_calc", "currency",
            "fundamental_currency_code")

PAGE = 2000          # 한 요청 행 수 — 라이브러리 기본 50 은 5천 종목에 100콜이다
MAX_PAGES = 8        # 시장당 상한(미국 ~5천 종목 = 3쪽) — 원천이 끝없이 주는 사고를 막는다
PARTIAL_MIN = 0.98  # totalCount 대비 이만큼 못 받으면 일부만 받은 것으로 본다(겹침·버린 행 여유)
_TIMEOUT = (10, 30)
_HEADERS = {
    "accept": "text/plain, */*; q=0.01",
    "origin": "https://www.tradingview.com",
    "referer": "https://www.tradingview.com/",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
}


class ScanError(Exception):
    """수집 실패 — `kind` 로 갈래를 가른다(network · http · format · empty · partial)."""

    def __init__(self, reason: str, kind: str):
        super().__init__(reason)
        self.reason, self.kind = reason, kind


def build_query(market: str, columns: list[str], start: int, size: int) -> dict:
    """한 쪽의 요청 본문(순수). 주 상장(`is_primary`)의 보통주·DR — 우선주는 같은 회사를 두 번
    세고, ETF·펀드는 업종이 아니라서 뺀다."""
    tv = MARKETS[market][0]
    filt = [{"left": "is_primary", "operation": "equal", "right": True}]
    if market in EXCHANGES:
        filt.append({"left": "exchange", "operation": "in_range",
                     "right": EXCHANGES[market]})

    def _and(*exprs):
        return {"operation": {"operator": "and",
                              "operands": [{"expression": e} for e in exprs]}}
    # 보통주 또는 DR — 웹 스크리너 기본 요청(`STOCKS_QUERY.filter2`)에서 우선주·펀드만 뺐다.
    # DR 을 넣는 건 사용자가 준 블로그 캡처가 미국 업종에 ADR(SK hynix·ASML·ARM)을
    # 넣기 때문이다 — 그 나라에서 거래되는 대로 센다. 단 시총 통화가 거래 통화와 다르면
    # (`world_sectors.normalize` 의 `fx`) 종목 수·거래대금에만 들고 **등락 가중에선 빠진다** —
    # 몇 개인지는 화면(심리지도 주 머리)·`--probe` 가 적는다. 원천이 ADR 시총을 어느 통화로
    # 주는지·`is_primary` 가 ADR 을 이미 빼는지는 재지 못했다(#165).
    filt2 = {"operator": "and", "operands": [{"operation": {"operator": "or", "operands": [
        _and({"left": "type", "operation": "equal", "right": "stock"},
             {"left": "typespecs", "operation": "has", "right": ["common"]}),
        _and({"left": "type", "operation": "equal", "right": "dr"}),
    ]}}]}
    return {
        "markets": [tv],
        "symbols": {"query": {"types": []}, "tickers": []},
        "options": {"lang": "ko"},
        "columns": list(columns),
        "filter": filt,
        "filter2": filt2,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [start, start + size],
        "ignore_unknown_fields": False,
    }


def unknown_fields(body: str, columns) -> list[str]:
    """400 본문이 지목한 열(순수) — 원천이 오류 문구에 모르는 열 이름을 적는다고 보고, **우리가
    보낸 열 이름이 낱말로** 나오는지로 고른다. JSON 본문이면 문자열 값을 풀어서 본다(이스케이프된
    따옴표에 안 걸리게). 낱말 경계는 점까지 포함한다 — `sector` 가 `sector.tr` 안에서 걸리지
    않게(#46). 오류 문구의 실제 모양은 재지 않았다(샌드박스 미도달) — 못 찾으면 빈 목록이고
    호출부는 그 400 을 그대로 실패로 말한다."""
    import re as _re
    texts = [body or ""]
    try:
        js = json.loads(body)
    except (TypeError, ValueError):
        js = None

    def _walk(o):
        if isinstance(o, str):
            texts.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(js)
    hay = "\n".join(texts)
    return [c for c in columns
            if _re.search(r"(?<![\w.])" + _re.escape(c) + r"(?![\w.])", hay)]


def _post(market: str, body: dict, session=None):
    import requests
    s = session or requests
    return s.post(URL.format(market=MARKETS[market][0]), json=body,
                  headers=_HEADERS, timeout=_TIMEOUT)


def scan(market: str, *, session=None, sleep=time.sleep) -> dict:
    """한 시장 전 종목 — {"rows": [dict], "total": int, "dropped": [열], "pages": int,
    "bad_rows": int, "dup_rows": int}.

    실패는 `ScanError`(갈래·사유). 400 이 우리가 보낸 **선택 열**을 지목하면 그 열만 빼고 한 번
    더 묻는다 — 필수 열을 지목하면 실패다(그 열 없이는 판정을 못 한다).

    ⚠️ 원천이 `totalCount` 를 말하면 **그만큼 받았는지 대조한다** — 쪽 크기를 줄여 주거나(요청
    2000 · 응답 1000) 행을 버려야 하면 '마지막 쪽' 으로 읽고 멈춘 일부를 완전본으로 굽게 된다
    (#280 — 첫날은 비교할 저장분이 없고, 그 뒤로는 날마다 같은 일부라 어떤 검사도 안 걸린다).
    그래서 다음 쪽은 **받은 만큼 뒤에서** 묻고(원천이 쪽을 줄여 줘도 건너뛰지 않게), 다 받기 전에
    끝나면 `partial` 로 실패한다. 같은 기호가 쪽 경계에서 두 번 오면 한 번만 센다(시총 정렬의
    동률). 원천이 `totalCount` 를 안 주면 대조할 수 없다 — `total` 이 None 으로 남는다."""
    from bot.naver_diag import http_reason
    columns = [*CORE, *OPTIONAL]
    dropped: list[str] = []
    rows: list[dict] = []
    seen: set[str] = set()
    bad = dup = 0
    total = None
    page = 0
    offset = 0           # 원천이 지금까지 준 항목 수(버린 행 포함) — 다음 쪽의 시작
    retried = False
    while page < MAX_PAGES:
        body = build_query(market, columns, offset, PAGE)
        try:
            r = _post(market, body, session)
        except Exception as exc:                               # noqa: BLE001
            raise ScanError(http_reason(None, exc=exc), "network") from exc
        status = getattr(r, "status_code", None)
        if status == 400 and not retried:
            text = (getattr(r, "text", "") or "")[:4000]
            badf = unknown_fields(text, columns)
            core_bad = [c for c in badf if c in CORE]
            if core_bad:
                raise ScanError(f"원천이 필수 열을 모른다고 답함({', '.join(core_bad)}) — "
                                + http_reason(400, body=text), "format")
            if badf:
                dropped += badf
                columns = [c for c in columns if c not in badf]
                retried = True
                log.warning("tv_scanner: %s 원천이 열 %s 를 몰라 빼고 다시 묻는다", market, badf)
                continue
        if status != 200:
            raise ScanError(http_reason(status, body=getattr(r, "content", b"")), "http")
        try:
            js = r.json()
        except Exception as exc:                               # noqa: BLE001
            raise ScanError(f"원천이 JSON 이 아닌 본문을 줌({type(exc).__name__})",
                            "format") from exc
        if not isinstance(js, dict) or not isinstance(js.get("data"), list):
            keys = ", ".join(sorted(js)[:8]) if isinstance(js, dict) else type(js).__name__
            raise ScanError(f"원천 응답에 data 목록이 없음(키: {keys})", "format")
        if total is None:
            try:
                total = int(js.get("totalCount"))
            except (TypeError, ValueError):
                total = None
        data = js["data"]
        for it in data:
            d = it.get("d") if isinstance(it, dict) else None
            if not isinstance(d, list) or len(d) != len(columns):
                bad += 1
                continue
            row = dict(zip(columns, d))
            row["symbol"] = it.get("s") or ""
            if row["symbol"] and row["symbol"] in seen:
                dup += 1
                continue
            seen.add(row["symbol"])
            rows.append(row)
        page += 1
        offset += len(data)
        if not data:
            break
        if total is not None:
            if offset >= total:
                break
        elif len(data) < PAGE:
            break
        sleep(0.6)
    if not rows:
        raise ScanError(f"원천이 종목을 하나도 주지 않음(totalCount={total})", "empty")
    if total is not None and len(rows) < PARTIAL_MIN * total:
        raise ScanError(
            f"원천이 totalCount {total:,} 중 {len(rows):,}행만 줌({page}쪽"
            + (f" · 열 수가 안 맞아 버린 행 {bad}" if bad else "")
            + (f" · 겹친 행 {dup}" if dup else "")
            + (f" · 쪽 상한 {MAX_PAGES}에 닿음" if page >= MAX_PAGES else "")
            + ") — 일부만 받은 응답은 굽지 않는다", "partial")
    return {"rows": rows, "total": total, "dropped": dropped, "pages": page,
            "bad_rows": bad, "dup_rows": dup}
