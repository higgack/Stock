"""네이버 시장지표(marketindex) 시세 — 원자재(energy/metals/agricultural).

yfinance fast_info(quote API, 데이터센터 IP rate-limit) 대체 (사용자 2026-06-14
'다 네이버로'). VM probe 2026-06-14 구조 확정: GET
stock.naver.com/api/securityService/marketindex/{energy|metals|agricultural}
→ [{symbolCode, name, closePrice(콤마), fluctuations(절대변동), fluctuationsRatio,
fluctuationsType:{code:'2'상승|'5'하락}}]. 두바이유(DCB)·니켈(NI) 등 yfinance
무티커 항목 포함. 무료·무키·1분 캐시·graceful.
"""
from __future__ import annotations

import logging

log = logging.getLogger("bot.naver_marketindex")

_BASE = "https://stock.naver.com/api/securityService/marketindex"
_INDEX_URL = "https://stock.naver.com/api/polling/worldstock/index"
# transport = 운임지수(CCFI/SCFI/BADI 등, 사용자 2026-06-14). energy/metals/agri 와
# 동일 구조라 fetch_commodities 로 함께 수집(nv:CCFI·nv:BADI 등으로 CARD 참조).
_CATEGORIES = ("energy", "metals", "agricultural", "transport")
_CACHE = "naver_marketindex.json"
_TTL = 60       # 1분 (스냅샷 주기와 일치, 사용자 2026-06-14)
_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json", "Referer": "https://stock.naver.com/",
}


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None


def _parse_item(it: dict) -> dict | None:
    """marketindex item → {name, close, prev, change, pct}. 순수."""
    close = _num(it.get("closePrice"))
    if close is None:
        return None
    chg = _num(it.get("fluctuations"))
    pct = _num(it.get("fluctuationsRatio"))
    typ = (it.get("fluctuationsType") or {}).get("code")
    sign = -1.0 if typ == "5" else 1.0    # 5=하락, 2=상승
    prev = (close - sign * chg) if chg is not None else close
    return {"name": str(it.get("name") or ""), "close": close,
            "prev": prev, "change": (sign * abs(chg)) if chg is not None else 0.0,
            "pct": (sign * abs(pct)) if pct is not None else 0.0}


def fetch_commodities() -> dict:
    """{symbolCode: {name, close, prev, change, pct}} — energy+metals+agri 합산.
    1분 디스크 캐시. NAVER_PAUSE·실패 시 직전 캐시(블랭크 방지) 또는 {}."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_CACHE, ttl=86400) or {}
    import requests
    out: dict = {}
    for cat in _CATEGORIES:
        try:
            r = requests.get(f"{_BASE}/{cat}", headers=_HDRS, timeout=12)
            rows = r.json() if r.status_code == 200 else []
        except Exception as exc:
            log.warning("naver marketindex %s fetch error: %s", cat, exc)
            continue
        for it in rows if isinstance(rows, list) else []:
            sc = str(it.get("symbolCode") or "").strip()
            rec = _parse_item(it) if sc else None
            if sc and rec:
                out.setdefault(sc, rec)    # 중복 symbolCode 첫 값
    if out:
        _cache_write(_CACHE, out)
    else:                                  # 전 카테고리 실패 → 스테일 캐시
        return _cached(_CACHE, ttl=86400) or {}
    return out


_IDX_CACHE = "naver_worldindex.json"


def _codeset_plan(fresh: dict, codes: tuple) -> bool:
    """신선 캐시가 요청 코드를 **모두** 커버하면 True(재fetch 불요). 부분만 있으면
    False → 호출부가 union refetch. 공유 코드셋 캐시(아래 fetch_world_indices)용."""
    if not codes:
        return bool(fresh)
    return bool(fresh) and all(x in fresh for x in codes)


def _codeset_finalize(known: dict, fetched: dict, codes: tuple) -> tuple:
    """(write, ret) — known=기존(임의 age) 캐시, fetched=이번 fetch 결과. 병합으로
    **캐시 축소 방지**(부분집합 호출이 전체 카드를 블랭크로 만들던 회귀 차단) +
    요청 코드 부분집합 반환."""
    known = known if isinstance(known, dict) else {}
    fetched = fetched if isinstance(fetched, dict) else {}
    merged = {**known, **fetched}
    ret = {c: merged[c] for c in codes if c in merged} if codes else merged
    return merged, ret


def fetch_world_indices(codes: tuple) -> dict:
    """{reutersCode: {close, prev, change, pct}} — polling/worldstock/index
    (사용자 2026-06-14 '다 네이버로', VM probe 구조 확정: datas[].reutersCode·
    indexName·closePrice·compareToPreviousClosePrice·compareToPreviousPrice.code
    2상승/5하락). 니케이(.N225)·대만(.TWII)·VIX(.VIX)·필반(.SOX) 등. 1분 캐시·
    naver_paused·graceful.

    ⚠️ 공유 캐시(naver_worldindex.json) **코드셋 병합** 정책 — 여러 호출부가 같은
    파일을 쓴다(market_overview 24종 카드 / macro_snapshot 3종 값). 부분집합 fetch
    가 캐시를 통째로 덮어쓰면 다른 호출부가 자기 코드를 못 찾아 블랭크가 되는 회귀
    (2026-06-14: macro 가 .INX/.IXIC/.VIX 3종만 써 세계지수 카드 전부 — 표시). 방지:
    캐시가 요청 코드를 모두 커버하면 그 부분집합 반환, 아니면 **요청 ∪ 기존코드**를
    한 번에 refetch 후 병합(축소 0)."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    codes = tuple(c for c in codes if c)
    fresh = _cached(_IDX_CACHE, ttl=_TTL)
    fresh = fresh if isinstance(fresh, dict) else {}
    if _codeset_plan(fresh, codes):
        return {c: fresh[c] for c in codes} if codes else fresh
    known = _cached(_IDX_CACHE, ttl=86400) or {}
    known = known if isinstance(known, dict) else {}
    if naver_paused():
        return {c: known[c] for c in codes if c in known} if codes else known
    want = tuple(dict.fromkeys([*codes, *known.keys()])) or codes
    import requests
    out: dict = {}
    try:
        r = requests.get(_INDEX_URL, headers=_HDRS, timeout=12,
                         params={"reutersCodes": ",".join(want)})
        datas = (r.json() or {}).get("datas") or [] if r.status_code == 200 else []
    except Exception as exc:
        log.warning("naver worldindex fetch error: %s", exc)
        return {c: known[c] for c in codes if c in known} if codes else known
    for it in datas if isinstance(datas, list) else []:
        rc = str(it.get("reutersCode") or "").strip()
        close = _num(it.get("closePrice"))
        if not rc or close is None:
            continue
        chg = _num(it.get("compareToPreviousClosePrice"))
        code = (it.get("compareToPreviousPrice") or {}).get("code")
        sign = -1.0 if code == "5" else 1.0
        prev = (close - sign * chg) if chg is not None else close
        pct = (sign * abs(chg) / prev * 100.0) if (chg is not None and prev) else 0.0
        out[rc] = {"close": close, "prev": prev,
                   "change": (sign * abs(chg)) if chg is not None else 0.0,
                   "pct": pct}
    if out:
        merged, ret = _codeset_finalize(known, out, codes)
        _cache_write(_IDX_CACHE, merged)
        return ret
    return {c: known[c] for c in codes if c in known} if codes else known


_DOM_URL = "https://stock.naver.com/api/polling/domestic/index"
_DOM_CACHE = "naver_domesticindex.json"


def fetch_domestic_indices(codes: tuple) -> dict:
    """{itemCode: {close, prev, change, pct}} — polling/domestic/index
    (코스피/코스닥 등 국내지수, 사용자 2026-06-14 '다 네이버로'. VM probe 구조 확정:
    itemCode·closePriceRaw·compareToPreviousClosePriceRaw·compareToPreviousPrice.code
    (2상승/5하락)·fluctuationsRatioRaw). *Raw 필드 = 콤마 없는 숫자. 1분 캐시·
    naver_paused·graceful. 응답이 bare list 또는 {datas:[...]} 양형 모두 수용."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_DOM_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_DOM_CACHE, ttl=86400) or {}
    import requests
    out: dict = {}
    try:
        r = requests.get(_DOM_URL, headers=_HDRS, timeout=12,
                         params={"itemCodes": ",".join(codes)})
        raw = r.json() if r.status_code == 200 else []
    except Exception as exc:
        log.warning("naver domesticindex fetch error: %s", exc)
        return _cached(_DOM_CACHE, ttl=86400) or {}
    rows = raw.get("datas") if isinstance(raw, dict) else raw
    for it in rows if isinstance(rows, list) else []:
        ic = str(it.get("itemCode") or "").strip()
        close = _num(it.get("closePriceRaw") or it.get("closePrice"))
        if not ic or close is None:
            continue
        chg = _num(it.get("compareToPreviousClosePriceRaw")
                   or it.get("compareToPreviousClosePrice"))
        code = (it.get("compareToPreviousPrice") or {}).get("code")
        pctv = _num(it.get("fluctuationsRatioRaw") or it.get("fluctuationsRatio"))
        sign = -1.0 if code == "5" else 1.0      # 5=하락, 2=상승
        prev = (close - sign * chg) if chg is not None else close
        out[ic] = {"close": close, "prev": prev,
                   "change": (sign * abs(chg)) if chg is not None else 0.0,
                   "pct": (sign * abs(pctv)) if pctv is not None else 0.0}
    if out:
        _cache_write(_DOM_CACHE, out)
    else:
        return _cached(_DOM_CACHE, ttl=86400) or {}
    return out


_FX_URL = "https://api.stock.naver.com/marketindex/exchangeWorld"
_FX_CACHE = "naver_worldfx.json"


def fetch_world_fx() -> dict:
    """{reutersCode: {close, prev, change, pct}} — marketindex/exchangeWorld
    (세계 환율 cross-rate, 사용자 2026-06-14 '다 네이버로'. VM probe: reutersCode·
    closePrice·fluctuations·fluctuationsRatio·fluctuationsType.code 2상승/5하락).
    전체 113쌍 반환(인자 무시) → 호출부가 필요한 코드만 사용(EURUSD/GBPUSD/USDCNY
    등). ⚠️ 원/달러·엔/원 같은 KRW-base 는 미포함(세계 cross-rate 만, KRW 부재).
    1분 캐시·naver_paused·graceful. 응답 bare list/{datas:[]} 양형 수용."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_FX_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_FX_CACHE, ttl=86400) or {}
    import requests
    out: dict = {}
    try:
        r = requests.get(_FX_URL, headers=_HDRS, timeout=12)
        raw = r.json() if r.status_code == 200 else []
    except Exception as exc:
        log.warning("naver worldfx fetch error: %s", exc)
        return _cached(_FX_CACHE, ttl=86400) or {}
    rows = raw.get("datas") if isinstance(raw, dict) else raw
    for it in rows if isinstance(rows, list) else []:
        rc = str(it.get("reutersCode") or "").strip()
        close = _num(it.get("closePrice"))
        if not rc or close is None:
            continue
        chg = _num(it.get("fluctuations"))
        pct = _num(it.get("fluctuationsRatio"))
        code = (it.get("fluctuationsType") or {}).get("code")
        sign = -1.0 if code == "5" else 1.0      # 5=하락, 2=상승
        prev = (close - sign * chg) if chg is not None else close
        out[rc] = {"close": close, "prev": prev,
                   "change": (sign * abs(chg)) if chg is not None else 0.0,
                   "pct": (sign * abs(pct)) if pct is not None else 0.0}
    if out:
        _cache_write(_FX_CACHE, out)
    else:
        return _cached(_FX_CACHE, ttl=86400) or {}
    return out


_KRFX_URL = "https://api.stock.naver.com/marketindex/exchange"
_KRFX_CACHE = "naver_krfx.json"


def fetch_kr_fx() -> dict:
    """{reutersCode: {name, close, prev, change, pct}} — marketindex/exchange
    normalList (원/달러·원/엔 등 KRW-base 환율, 사용자 2026-06-14. VM probe: 엔드포인트
    200, reutersCode=FX_USDKRW 형식). exchangeWorld 와 동일 marketindex 패밀리라
    item 구조(closePrice·fluctuations·fluctuationsRatio·fluctuationsType.code) 재사용.
    전체 반환(인자 무시). 1분 캐시·naver_paused·graceful. normalList/datas/bare 수용."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_KRFX_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_KRFX_CACHE, ttl=86400) or {}
    import requests
    out: dict = {}
    try:
        r = requests.get(_KRFX_URL, headers=_HDRS, timeout=12)
        raw = r.json() if r.status_code == 200 else {}
    except Exception as exc:
        log.warning("naver kr fx fetch error: %s", exc)
        return _cached(_KRFX_CACHE, ttl=86400) or {}
    if isinstance(raw, dict):
        rows = raw.get("normalList") or raw.get("datas") or []
    else:
        rows = raw
    for it in rows if isinstance(rows, list) else []:
        rc = str(it.get("reutersCode") or it.get("symbolCode") or "").strip()
        rec = _parse_item(it) if rc else None
        if rc and rec and rec.get("close") is not None:
            out[rc] = rec
    if out:
        _cache_write(_KRFX_CACHE, out)
    else:
        return _cached(_KRFX_CACHE, ttl=86400) or {}
    return out


_FUT_URL = "https://stock.naver.com/api/polling/worldstock/futures"
_FUT_CACHE = "naver_worldfut.json"


def fetch_world_futures(codes: tuple) -> dict:
    """{reutersCode: {close, prev, change, pct}} — polling/worldstock/futures
    (미국 지수선물, 사용자 2026-06-14 'EScv1' 등). worldstock/index 와 동일 datas[]
    구조(reutersCode·closePrice·compareToPreviousClosePrice·compareToPreviousPrice.
    code 2상승/5하락). 1분 캐시·naver_paused·graceful."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_FUT_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_FUT_CACHE, ttl=86400) or {}
    import requests
    out: dict = {}
    try:
        r = requests.get(_FUT_URL, headers=_HDRS, timeout=12,
                         params={"reutersCodes": ",".join(codes)})
        raw = r.json() if r.status_code == 200 else {}
    except Exception as exc:
        log.warning("naver worldfutures fetch error: %s", exc)
        return _cached(_FUT_CACHE, ttl=86400) or {}
    datas = raw.get("datas") if isinstance(raw, dict) else raw
    for it in datas if isinstance(datas, list) else []:
        rc = str(it.get("reutersCode") or "").strip()
        close = _num(it.get("closePrice"))
        if not rc or close is None:
            continue
        chg = _num(it.get("compareToPreviousClosePrice"))
        code = (it.get("compareToPreviousPrice") or {}).get("code")
        sign = -1.0 if code == "5" else 1.0
        prev = (close - sign * chg) if chg is not None else close
        pct = (sign * abs(chg) / prev * 100.0) if (chg is not None and prev) else 0.0
        out[rc] = {"close": close, "prev": prev,
                   "change": (sign * abs(chg)) if chg is not None else 0.0, "pct": pct}
    if out:
        _cache_write(_FUT_CACHE, out)
    else:
        return _cached(_FUT_CACHE, ttl=86400) or {}
    return out


_ETF_URL = "https://stock.naver.com/api/polling/worldstock/etf"
_ETF_CACHE = "naver_worldetf.json"


def fetch_world_etf(codes: tuple) -> dict:
    """{reutersCode: {close, prev, change, pct}} — polling/worldstock/etf
    (미국 섹터 ETF XLF/XLV/XLP/XLE 등, 사용자 2026-06-14 '다 네이버로'). worldstock/
    index·futures 와 동일 datas[] 구조(reutersCode·closePrice·compareToPreviousClose
    Price·compareToPreviousPrice.code 2상승/5하락). ⚠️ reutersCode 형식 verify 필요
    (XLF / XLF.K / XLF.P 등). 1분 캐시·naver_paused·graceful."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_ETF_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_ETF_CACHE, ttl=86400) or {}
    import requests
    out: dict = {}
    try:
        r = requests.get(_ETF_URL, headers=_HDRS, timeout=12,
                         params={"reutersCodes": ",".join(codes)})
        raw = r.json() if r.status_code == 200 else {}
    except Exception as exc:
        log.warning("naver worldetf fetch error: %s", exc)
        return _cached(_ETF_CACHE, ttl=86400) or {}
    datas = raw.get("datas") if isinstance(raw, dict) else raw
    for it in datas if isinstance(datas, list) else []:
        rc = str(it.get("reutersCode") or "").strip()
        close = _num(it.get("closePrice"))
        if not rc or close is None:
            continue
        chg = _num(it.get("compareToPreviousClosePrice"))
        code = (it.get("compareToPreviousPrice") or {}).get("code")
        sign = -1.0 if code == "5" else 1.0
        prev = (close - sign * chg) if chg is not None else close
        pct = (sign * abs(chg) / prev * 100.0) if (chg is not None and prev) else 0.0
        out[rc] = {"close": close, "prev": prev,
                   "change": (sign * abs(chg)) if chg is not None else 0.0, "pct": pct}
    if out:
        _cache_write(_ETF_CACHE, out)
    else:
        return _cached(_ETF_CACHE, ttl=86400) or {}
    return out


# /majors = VM probe 확정(bare list). 옛 ?sortType=top 은 구조 미확정→블랭크였음.
_COIN_URL = "https://stock.naver.com/api/coin/rank/UPBIT/majors"
_COIN_FULL_URL = "https://stock.naver.com/api/coin/rank/UPBIT"
_COIN_CACHE = "naver_coins.json"


def fetch_naver_coins() -> dict:
    """{SYMBOL: {close(₩), prev, change, pct}} — coin/rank/UPBIT (업비트 원화시세,
    사용자 2026-06-14). VM probe 구조 확정(bare list): nfTicker(심볼)·tradePrice(원화
    숫자)·changeRate(%, 부호포함)·changeValue(원화 변동)·change(RISING/FALLING).
    ⚠️ Upbit=KRW 라 값이 원화(₩). 응답 bare list / {result|datas|coins:[...]} 수용.
    BNB 등 업비트 미상장은 자연 누락(graceful). 1분 캐시·naver_paused."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_COIN_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_COIN_CACHE, ttl=86400) or {}
    import requests

    def _grab(url, params=None):
        try:
            r = requests.get(url, headers=_HDRS, timeout=12, params=params or {})
            raw = r.json() if r.status_code == 200 else None
        except Exception as exc:
            log.warning("naver coins fetch error (%s): %s", url, exc)
            return {}
        if isinstance(raw, list):
            rows = raw
        elif isinstance(raw, dict):
            # VM probe 2026-06-14: 풀 랭킹은 {contents:[...]} 래퍼 (DOGE/TRX 등 100종).
            rows = (raw.get("contents") or raw.get("result") or raw.get("datas")
                    or raw.get("coins") or [])
        else:
            rows = []
        return _parse_coins(rows)

    out = _grab(_COIN_URL)                       # /majors (VM probe 확정 bare list)
    # 풀 랭킹으로 보강(USDT/USDC 등 majors 미포함분). 구조 동일 가정, 실패 무해.
    try:
        for k, v in _grab(_COIN_FULL_URL,
                          {"sortType": "top", "page": 1, "pageSize": 100}).items():
            out.setdefault(k, v)
    except Exception:
        pass
    if out:
        _cache_write(_COIN_CACHE, out)
    else:
        return _cached(_COIN_CACHE, ttl=86400) or {}
    return out


def _parse_coins(rows) -> dict:
    """coin/rank item 리스트 → {SYMBOL: {close, prev, change, pct}}. 순수(단위테스트).
    nfTicker·tradePrice·changeValue·changeRate·change(RISING/FALLING)."""
    out: dict = {}
    for it in rows if isinstance(rows, list) else []:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("nfTicker") or it.get("exchangeTicker")
                  or it.get("symbolCode") or "").upper().strip()
        close = _num(it.get("tradePrice") or it.get("closePrice")
                     or it.get("currentPrice"))
        if not sym or close is None:
            continue
        chg = _num(it.get("changeValue"))
        pct = _num(it.get("changeRate"))
        ctype = str(it.get("change") or "").upper()
        if ctype.startswith("FALL"):           # FALLING → 음수
            chg = -abs(chg) if chg is not None else chg
            pct = -abs(pct) if pct is not None else pct
        elif ctype.startswith("RIS"):          # RISING → 양수
            chg = abs(chg) if chg is not None else chg
            pct = abs(pct) if pct is not None else pct
        if chg is not None:
            prev = close - chg
        elif pct:
            prev = close / (1 + pct / 100.0)
        else:
            prev = close
        out[sym] = {"close": close, "prev": prev,
                    "change": chg if chg is not None else (close - prev),
                    "pct": pct if pct is not None else 0.0}
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("국내지수:", fetch_domestic_indices(("KOSPI", "KOSDAQ")))
    print("선물:", fetch_world_futures(("EScv1", "YMcv1", "NQcv1", "RTYcv1")))
    _co = fetch_naver_coins()
    print("코인:", {k: _co.get(k) for k in ("BTC", "ETH", "XRP", "SOL", "DOGE")})
    _kf = fetch_kr_fx()
    print("KR환율:", {k: _kf.get(k) for k in ("FX_USDKRW", "FX_JPYKRW")})
    _fx = fetch_world_fx()
    print("세계환율:", {k: _fx.get(k) for k in ("EURUSD", "USDCNY", "USDTWD")})
    d = fetch_commodities()
    print(f"원자재 {len(d)}종")
    for sc in ("CL", "BRN", "DCB", "NG", "GC", "SI", "HG", "AA", "NI",
               "PL", "ZC", "ZS", "ZW"):
        r = d.get(sc)
        if r:
            print(f"  {sc} {r['name']}: {r['close']:,} ({r['pct']:+.2f}%)")
