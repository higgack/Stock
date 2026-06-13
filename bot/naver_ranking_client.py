"""네이버 모바일 front-api 랭킹 클라이언트 — KR 52주 신고가/신저가 (전종목·한글명
native·1콜). 사용자 2026-06-13: pykrx+yfinance 전종목 스캔(수 분·느림)을 네이버
ready 데이터로 교체 — "신고가 신저가는 네이버에 있어".

VM 실측으로 엔드포인트·필드·파라미터 전부 확인(2026-06-13, 샌드박스는 네이버
도달 불가). graceful — 실패/빈 응답 시 빈 결과 → 호출부가 pykrx 스캔으로 폴백.

국내(domestic):
  GET front-api/domestic/stock/list?sortType={high52week|low52week|up|down|...}
      &category={all|KOSPI|KOSDAQ}&page=1&pageSize=N
  → {isSuccess, result:{stocks:[{itemCode, name(한글), stockExchangeType,
     currentPrice, fluctuationsType, fluctuationsRatio, accumulatedTradingVolume,
     marketValue, stockEndType, ...}], totalCount}}
무료·무키. 해외(worldstock) 무버는 후속(별도 함수)."""
from __future__ import annotations

import logging

log = logging.getLogger("bot.naver_ranking")

_BASE = "https://m.stock.naver.com/front-api"
_H = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://m.stock.naver.com/",
    "Accept": "application/json, text/plain, */*",
}
# stockExchangeType → 우리 티커 접미사 (KIS/yfinance/대시보드 lookup 일관)
_SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}


def _get_stocks(url: str) -> list | None:
    """네이버 front-api GET → result.stocks 리스트. graceful None."""
    try:
        import requests
        r = requests.get(url, headers=_H, timeout=12)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or not d.get("isSuccess"):
            return None
        res = d.get("result") or {}
        st = res.get("stocks")
        return st if isinstance(st, list) else None
    except Exception as exc:
        log.warning("naver_ranking GET 실패: %s", exc)
        return None


def _signed_pct(s: dict):
    """fluctuationsRatio(절대값 문자열) + fluctuationsType 으로 부호 결정.
    RISING/UPPER_LIMIT=+, FALLING/LOWER_LIMIT=-, 그 외(보합)=0."""
    try:
        v = abs(float(s.get("fluctuationsRatio") or 0))
    except (TypeError, ValueError):
        return None
    ft = str(s.get("fluctuationsType") or "")
    if "FALL" in ft or "LOWER" in ft:
        return round(-v, 2)
    return round(v, 2)


def _kr_row(s: dict) -> dict:
    """네이버 domestic stock → highlow_render 스키마 {ticker,name,price,pct,vol,mcap,ind}.
    mcap = marketValue(원) / 1e8 = 억 단위 (fmt_mcap 규약). 업종(ind) 네이버 미제공."""
    code = str(s.get("itemCode") or "")
    suf = _SUFFIX.get(str(s.get("stockExchangeType") or ""), "")
    mv = s.get("marketValue")
    try:
        mcap = round(float(mv) / 1e8, 2) if mv else None
    except (TypeError, ValueError):
        mcap = None
    return {
        "ticker": f"{code}{suf}" if code else code,
        "name": s.get("name") or code,
        "price": s.get("currentPrice"),
        "pct": _signed_pct(s),
        "vol": s.get("accumulatedTradingVolume"),
        "mcap": mcap,
        "ind": None,
    }


def _is_real_stock(s: dict) -> bool:
    """실종목만 — ETF/ETN/스팩 제외 (사용자 신고저 정책). stockEndType=='stock'
    + 이름에 '스팩' 없음. (52주 최고엔 ETN/레버리지 상품이 섞여 들어옴)."""
    if str(s.get("stockEndType") or "") != "stock":
        return False
    nm = str(s.get("name") or "")
    return "스팩" not in nm


def fetch_kr_highlow(limit: int = 200) -> dict:
    """KR 52주 신고가/신저가 — 네이버 전종목(한글명 native, sortType 2콜). ETF/ETN/
    스팩 제외. {high, low, ts, source}. graceful — 두 콜 모두 실패 시 high/low 빈
    (호출부가 pykrx 스캔 폴백). 시총순 정렬은 호출부(sort_by_mcap)."""
    from bot.finviz_client import _now_label
    out = {"high": [], "low": [], "ts": _now_label(),
           "source": "네이버 증권 52주 최고/최저(전종목·한글명)"}
    hi = _get_stocks(f"{_BASE}/domestic/stock/list?sortType=high52week"
                     f"&category=all&page=1&pageSize={limit}")
    lo = _get_stocks(f"{_BASE}/domestic/stock/list?sortType=low52week"
                     f"&category=all&page=1&pageSize={limit}")
    if hi:
        out["high"] = [_kr_row(s) for s in hi if _is_real_stock(s)]
    if lo:
        out["low"] = [_kr_row(s) for s in lo if _is_real_stock(s)]
    return out


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = fetch_kr_highlow()
    print(f"high {len(d['high'])} / low {len(d['low'])} ({d['source']})")
    for r in d["high"][:5]:
        print(" ", json.dumps(r, ensure_ascii=False))
