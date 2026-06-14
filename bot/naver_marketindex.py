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


def fetch_world_indices(codes: tuple) -> dict:
    """{reutersCode: {close, prev, change, pct}} — polling/worldstock/index
    (사용자 2026-06-14 '다 네이버로', VM probe 구조 확정: datas[].reutersCode·
    indexName·closePrice·compareToPreviousClosePrice·compareToPreviousPrice.code
    2상승/5하락). 니케이(.N225)·대만(.TWII)·VIX(.VIX)·필반(.SOX) 등. 1분 캐시·
    naver_paused·graceful."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_IDX_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_IDX_CACHE, ttl=86400) or {}
    import requests
    out: dict = {}
    try:
        r = requests.get(_INDEX_URL, headers=_HDRS, timeout=12,
                         params={"reutersCodes": ",".join(codes)})
        datas = (r.json() or {}).get("datas") or [] if r.status_code == 200 else []
    except Exception as exc:
        log.warning("naver worldindex fetch error: %s", exc)
        return _cached(_IDX_CACHE, ttl=86400) or {}
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
        _cache_write(_IDX_CACHE, out)
    else:
        return _cached(_IDX_CACHE, ttl=86400) or {}
    return out


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


_COIN_URL = "https://stock.naver.com/api/coin/rank/UPBIT"
_COIN_CACHE = "naver_coins.json"


def _coin_symbol(it: dict) -> str:
    """item → 코인 심볼(BTC/ETH…) 대문자. 다양한 필드/표기(KRW-BTC·BTC-KRW) 수용."""
    for k in ("symbolCode", "symbol", "code", "reutersCode", "market", "coinCode"):
        v = it.get(k)
        if v:
            s = str(v).upper()
            for sep in ("KRW-", "-KRW", "UPBIT:", "/KRW"):
                s = s.replace(sep, "")
            return s.strip()
    return ""


def fetch_naver_coins() -> dict:
    """{SYMBOL: {close(₩), prev, change, pct}} — coin/rank/UPBIT (업비트 원화시세,
    사용자 2026-06-14 '코인도 다 줬어'). ⚠️ Upbit=KRW 라 값이 원화. 응답 구조가
    Naver-convention(closePrice·fluctuationsRatio·compareToPreviousPrice.code) /
    Upbit-native(trade_price·signed_change_rate·change) 양쪽 모두 방어 파싱. 리스트는
    재귀 탐색. 1분 캐시·naver_paused·graceful."""
    from bot.finviz_client import _cache_write, _cached, naver_paused
    c = _cached(_COIN_CACHE, ttl=_TTL)
    if isinstance(c, dict) and c:
        return c
    if naver_paused():
        return _cached(_COIN_CACHE, ttl=86400) or {}
    import requests
    try:
        r = requests.get(_COIN_URL, headers=_HDRS, timeout=12,
                         params={"sortType": "top", "page": 1, "pageSize": 100})
        raw = r.json() if r.status_code == 200 else None
    except Exception as exc:
        log.warning("naver coins fetch error: %s", exc)
        return _cached(_COIN_CACHE, ttl=86400) or {}

    def _find_list(o):                          # 가격필드 가진 dict 리스트 재귀 탐색
        if isinstance(o, list):
            if o and isinstance(o[0], dict) and any(
                    k in o[0] for k in ("closePrice", "trade_price", "tradePrice",
                                        "currentPrice", "price")):
                return o
        if isinstance(o, dict):
            for v in o.values():
                got = _find_list(v)
                if got:
                    return got
        return None

    rows = _find_list(raw) or []
    out: dict = {}
    for it in rows if isinstance(rows, list) else []:
        if not isinstance(it, dict):
            continue
        sym = _coin_symbol(it)
        close = _num(it.get("closePrice") or it.get("tradePrice")
                     or it.get("trade_price") or it.get("currentPrice")
                     or it.get("price"))
        if not sym or close is None:
            continue
        # 등락 — Naver(%, fluctuationsRatio + code 2/5) 또는 Upbit(signed_change_rate ratio)
        pct = _num(it.get("fluctuationsRatio"))
        code = ((it.get("compareToPreviousPrice") or it.get("fluctuationsType")
                 or {}).get("code"))
        if pct is not None:                     # Naver convention
            sign = -1.0 if code == "5" else 1.0
            pct = sign * abs(pct)
        else:                                   # Upbit native
            scr = _num(it.get("signed_change_rate") or it.get("signedChangeRate"))
            chg_t = str(it.get("change") or "").upper()
            sgn = -1.0 if chg_t == "FALL" else 1.0
            pct = ((scr * 100.0) if scr is not None and abs(scr) <= 1 else (scr or 0.0))
            if scr is not None and chg_t in ("RISE", "FALL"):
                pct = sgn * abs(pct)
        prev = close / (1 + pct / 100.0) if pct else close
        out[sym] = {"close": close, "prev": prev,
                    "change": close - prev, "pct": pct}
    if out:
        _cache_write(_COIN_CACHE, out)
    else:
        return _cached(_COIN_CACHE, ttl=86400) or {}
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
