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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = fetch_commodities()
    print(f"원자재 {len(d)}종")
    for sc in ("CL", "BRN", "DCB", "NG", "GC", "SI", "HG", "AA", "NI",
               "PL", "ZC", "ZS", "ZW"):
        r = d.get(sc)
        if r:
            print(f"  {sc} {r['name']}: {r['close']:,} ({r['pct']:+.2f}%)")
