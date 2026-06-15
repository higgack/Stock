"""단일 KR 종목 실시간 시세 — Naver polling.finance (키 불필요·VM IP 차단
없음·장중 라이브).

왜: 상세 페이지(검색 카드 + 차트 현재가)의 KR 현재가가 KIS 키 부재 시
yfinance 로 폴백하는데, yfinance 의 KR 시세는 EOD(종가)라 장중엔 직전 거래일
종가(예: 월요일 장중에 금요일 종가)를 준다(사용자 2026-06-15 'NXT 클릭→
금요일종가'). NXT·신고저·급등락 등 다른 surface 가 이미 쓰는 Naver 와 동일하게
상세도 Naver 실시간으로 채워 키·IP차단 무관하게 장중 라이브를 보장한다.

엔드포인트(2026-06-15 VM 검증):
  GET polling.finance.naver.com/api/realtime/domestic/stock/<6자리코드>
  → {datas:[{closePriceRaw(현재가), fluctuationsRatioRaw(등락%),
       compareToPreviousPrice.code(2 상승/5 하락), marketValueFullRaw(시총·원),
       localTradedAt(체결시각), marketStatus, tradeStopType, ...}]}
장중엔 closePriceRaw 가 라이브로 갱신, 장후엔 당일 종가. graceful(실패 시 None).
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
    "Referer": "https://m.stock.naver.com/",
}
_TTL = 30  # 30초 SWR — 장중 준실시간 (상위 /api/quote 5분 캐시가 추가로 dedup)
_cache: dict[str, tuple[float, dict | None]] = {}


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def _parse_quote(item: dict) -> dict | None:
    """datas[0] 항목 → {price, pct, mcap, ts} (순수·단위테스트용). price 없으면 None.

    price = closePriceRaw(현재가). pct = fluctuationsRatioRaw 에 부호 적용
    (compareToPreviousPrice.code '5'=하락이면 음수). mcap = marketValueFullRaw
    (원 단위, 그대로). ts = localTradedAt(마지막 체결시각, 신선도 표시용)."""
    if not isinstance(item, dict):
        return None
    price = _num(item.get("closePriceRaw") or item.get("closePrice"))
    if not price:
        return None
    pct = _num(item.get("fluctuationsRatioRaw") or item.get("fluctuationsRatio"))
    if pct is not None:
        cmp = item.get("compareToPreviousPrice") or {}
        falling = isinstance(cmp, dict) and str(cmp.get("code")) == "5"
        pct = -abs(pct) if falling else abs(pct)
    return {
        "price": price,
        "pct": pct,
        "mcap": _num(item.get("marketValueFullRaw")),
        "ts": item.get("localTradedAt"),
        # 당일 OHLCV — 차트의 당일 일봉을 라이브로 그리는 데 사용(yahoo 가 장중
        # 당일 봉을 EOD/미제공하는 문제 해소). close 는 price 와 동일.
        "open": _num(item.get("openPriceRaw")),
        "high": _num(item.get("highPriceRaw")),
        "low": _num(item.get("lowPriceRaw")),
        "close": price,
        "volume": _num(item.get("accumulatedTradingVolumeRaw")),
    }


def fetch_kr_quote(code: str) -> dict | None:
    """6자리 KR 종목코드(또는 '267260.KS') → 실시간 {price, pct, mcap, ts}.

    30초 SWR 캐시. 어떤 실패든 None 으로 degrade(상세는 yfinance 폴백 유지) —
    '오류 나면 안 된다'. 네트워크 실패 시 직전 캐시값(있으면) 유지."""
    code = (code or "").strip().upper().split(".")[0]
    if not code.isdigit() or not (4 <= len(code) <= 6):
        return None
    code = code.zfill(6)
    now = time.time()
    hit = _cache.get(code)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    out: dict | None = hit[1] if hit else None  # 실패 시 직전값 유지
    try:
        import requests
        r = requests.get(_URL.format(code=code), headers=_HDRS, timeout=8)
        if r.status_code == 200:
            j = r.json()
            datas = (j.get("datas") if isinstance(j, dict) else None) or []
            it = datas[0] if datas and isinstance(datas[0], dict) else None
            parsed = _parse_quote(it) if it else None
            if parsed:
                out = parsed
    except Exception as exc:
        log.debug("naver_quote %s failed: %s", code, exc)
    _cache[code] = (now, out)
    return out


if __name__ == "__main__":  # pragma: no cover — VM 1줄 진단
    import sys
    for _c in (sys.argv[1:] or ["267260"]):
        print(_c, "→", fetch_kr_quote(_c))
