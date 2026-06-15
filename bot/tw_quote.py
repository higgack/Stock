"""TW 단일 종목 실시간 시세 — 대만증권거래소(TWSE) 공식 MIS.

사용자 2026-06-15 'TW=대만거래소'. 네이버 worldstock 이 TW 미지원이라 공식
API 사용: GET mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_<code>.tw
(상장) 또는 otc_<code>.tw(TPEx) → {msgArray:[{z:현재가, y:전일종가, o/h/l,
v:거래량, ...}]}. 세션 쿠키 선취득(index.jsp) 후 호출. pct=(z/y-1)*100.
z 가 '-'(체결 없음)면 pz(揭示참고가) 폴백. 키 불필요. graceful None → yfinance.
2026-06-15 VM 검증(2330 z=2375 y=2310).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

_IDX = "https://mis.twse.com.tw/stock/index.jsp"
_API = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TTL = 30
_cache: dict[str, tuple[float, dict | None]] = {}
_session = None   # 쿠키 유지(MIS 는 세션 필요)


def _num(v):
    try:
        x = float(str(v).replace(",", ""))
        return x if x == x else None       # NaN 방어
    except Exception:
        return None


def _parse_tw(a: dict) -> dict | None:
    """msgArray[0] → {price, pct, open, high, low, volume}. price 없으면 None."""
    if not isinstance(a, dict):
        return None
    z = _num(a.get("z"))
    if z is None or z <= 0:
        z = _num(a.get("pz"))              # 체결 없으면 揭示참고가
    if z is None or z <= 0:
        return None
    y = _num(a.get("y"))
    pct = ((z / y - 1) * 100) if (y and y > 0) else None
    return {"price": z, "pct": pct, "open": _num(a.get("o")), "high": _num(a.get("h")),
            "low": _num(a.get("l")), "volume": _num(a.get("v")), "source": "TWSE 실시간"}


def fetch_tw_quote(ticker: str) -> dict | None:
    """TW 티커(2330.TW/2330) → 실시간 {price, pct, ...}. 30초 SWR. graceful None."""
    code = (ticker or "").strip().upper().split(".")[0]
    if not code.isdigit():
        return None
    now = time.time()
    hit = _cache.get(code)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    out = hit[1] if hit else None
    try:
        import requests
        global _session
        if _session is None:
            _session = requests.Session()
            _session.headers.update({"User-Agent": _UA})
            try:
                _session.get(_IDX, timeout=8)   # 세션 쿠키
            except Exception:
                pass
        for ex in ("tse", "otc"):              # 상장 → TPEx 순 시도
            try:
                r = _session.get(
                    _API,
                    params={"ex_ch": f"{ex}_{code}.tw", "json": "1", "delay": "0"},
                    headers={"Referer": _IDX}, timeout=8,
                )
                if r.status_code != 200:
                    continue
                arr = (r.json().get("msgArray") or [])
                parsed = _parse_tw(arr[0]) if arr else None
                if parsed:
                    out = parsed
                    break
            except Exception:
                continue
    except Exception as exc:
        log.debug("tw_quote %s failed: %s", code, exc)
    _cache[code] = (now, out)
    return out


if __name__ == "__main__":   # pragma: no cover — VM 1줄 진단
    import sys
    for _t in (sys.argv[1:] or ["2330", "2317.TW"]):
        print(_t, "→", fetch_tw_quote(_t))
