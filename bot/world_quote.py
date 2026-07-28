"""US/JP/HK/CN 단일 종목 실시간 시세 — 네이버 해외주식(worldstock).

사용자 2026-06-15 '다 네이버'. KR(naver_quote 국내) 과 짝을 이루는 해외판:
GET polling.finance.naver.com/api/realtime/worldstock/stock/<reutersCode>
→ {datas:[{closePriceRaw, fluctuationsRatioRaw, compareToPreviousPrice.code,
   marketValueFullRaw, ...}]} — 필드가 국내와 동일(2026-06-15 VM 검증, AAPL.O
   delayTime:0 실시간)이라 naver_quote._parse_quote 를 그대로 재사용.

reutersCode: US 무접미사 → 'AAPL.O'(NASDAQ)/'AAPL.N'(NYSE) 후보 시도, JP '7203.T'
· HK '0700.HK' · CN '600519.SS'/'…SZ' 는 yfinance 접미사 그대로(자동 검증 — 빈
datas 면 다음 후보). 작동한 reutersCode 는 캐시. 키 불필요·VM 차단 없음. graceful
None → 호출부가 yfinance 폴백.
"""
from __future__ import annotations

import logging
import time

from bot.naver_quote import _HDRS, _parse_quote

log = logging.getLogger(__name__)

_URL = "https://polling.finance.naver.com/api/realtime/worldstock/stock/{rc}"
_TTL = 30
_cache: dict[str, tuple[float, dict | None]] = {}
_rc_cache: dict[str, str] = {}   # ticker → 작동 확인된 reutersCode (재조회 0)


def _candidates(ticker: str) -> list[str]:
    """yfinance 티커 → 네이버 reutersCode 후보(첫 non-empty 가 정답)."""
    t = (ticker or "").strip().upper()
    if not t:
        return []
    if "." in t:
        base, suf = t.rsplit(".", 1)
        if suf in ("KS", "KQ", "TW"):      # KR=naver_quote · TW=tw_quote 담당
            return []
        if suf == "HK":
            digits = "".join(c for c in base if c.isdigit())
            cands = [t]
            if digits:
                cands += [digits.zfill(4) + ".HK", digits.zfill(5) + ".HK",
                          str(int(digits)) + ".HK"]
            return list(dict.fromkeys(cands))
        return [t]                          # JP .T · CN .SS/.SZ — reuters suffix 일치
    return [t + ".O", t + ".N"]             # US 무접미사 → NASDAQ·NYSE 시도


def fetch_world_quote(ticker: str) -> dict | None:
    """US/JP/HK/CN 티커 → 실시간 {price, pct, mcap, ...}. 30초 SWR. graceful None."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    now = time.time()
    hit = _cache.get(t)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    out = hit[1] if hit else None
    cands = ([_rc_cache[t]] if t in _rc_cache else []) + _candidates(t)
    try:
        import requests
        for rc in cands:
            try:
                r = requests.get(_URL.format(rc=rc), headers=_HDRS, timeout=8)
                if r.status_code != 200:
                    continue
                datas = (r.json().get("datas") or [])
                parsed = _parse_quote(datas[0]) if datas else None
                if parsed and parsed.get("price"):
                    parsed["source"] = "네이버 실시간"
                    out = parsed
                    _rc_cache[t] = rc       # 다음부턴 이 reutersCode 로 1콜
                    break
            except Exception:
                continue
    except Exception as exc:
        log.debug("world_quote %s failed: %s", t, exc)
    _cache[t] = (now, out)
    return out


if __name__ == "__main__":   # pragma: no cover — VM 1줄 진단
    import sys
    for _t in (sys.argv[1:] or ["AAPL", "7203.T", "0700.HK", "600519.SS"]):
        print(_t, "→", fetch_world_quote(_t))
