"""NXT(넥스트레이드) 외국인·기관 순매수/순매도 — 네이버 trendForeignOrg.

KR 장전(08:00~09:00)·장후(15:30~20:00) 연장거래(NXT)의 투자자별 순매수/순매도
상위 (사용자 2026-06-14, 엔드포인트 직접 발굴). yfinance·KIS 가 KR 시간외를 못
주는 공백을 네이버 NXT 데이터로 메움. 야후 fast_info 무관(네이버).

응답(VM probe 2026-06-14 확정): {sections:{buyRankList:[...], sellRankList:[...]}}
item: itemcode·itemname·accTradeVolume(순매수량,매도음수)·accTradeAmount(순매수
금액 원)·dailyTradeVolume(총거래량)·nowPrice·prevChangeRate·prevChangePrice.
무료·무키·graceful.
"""
from __future__ import annotations

import logging

log = logging.getLogger("bot.nxt_client")

_BASE = "https://stock.naver.com/api/domestic/market/trend/trendForeignOrg"
_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json", "Referer": "https://stock.naver.com/",
}
# 외국인=FOREIGNER(probe 확정). 기관은 후보 다중 시도(투자자 코드 미확정 —
# 첫 성공 응답 사용, 전부 실패 시 빈 섹션 '데이터 없음' 정직 표기).
_ORGAN_CANDIDATES = ("ORGAN", "ORGANIZATION", "INSTITUTION", "ORGN")


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None


def _row(it: dict) -> dict:
    """trendForeignOrg item → 표준 dict. 순매수금액=억원, 순매수량=주."""
    amt = _num(it.get("accTradeAmount"))    # 원 (매도 음수)
    return {
        "code": str(it.get("itemcode") or "").strip(),
        "name": str(it.get("itemname") or "").strip(),
        "net_amt": round(amt / 1e8, 1) if amt is not None else None,  # 억원
        "net_qty": int(_num(it.get("accTradeVolume")))
        if _num(it.get("accTradeVolume")) is not None else None,
        "volume": int(_num(it.get("dailyTradeVolume")))
        if _num(it.get("dailyTradeVolume")) is not None else None,
        "price": _num(it.get("nowPrice")),
        "pct": _num(it.get("prevChangeRate")),
    }


def _fetch(investor: str, trade_type: str = "NXT",
           market: str = "ALL", page_size: int = 20) -> dict | None:
    """단일 investor/tradeType → {buy:[...], sell:[...], date}. graceful None."""
    import requests
    params = {"investorType": investor, "tradeType": trade_type,
              "marketType": market, "startIdx": 0, "pageSize": page_size,
              "periodType": "DAY"}
    try:
        r = requests.get(_BASE, headers=_HDRS, params=params, timeout=12)
        if r.status_code != 200:
            return None
        sec = (r.json() or {}).get("sections") or {}
    except Exception as exc:
        log.warning("nxt %s/%s fetch error: %s", investor, trade_type, exc)
        return None
    buy = [_row(x) for x in (sec.get("buyRankList") or [])]
    sell = [_row(x) for x in (sec.get("sellRankList") or [])]
    if not buy and not sell:
        return None
    date = ""
    for x in (sec.get("buyRankList") or []) + (sec.get("sellRankList") or []):
        date = str(x.get("bizdateTo") or "")
        break
    return {"buy": buy, "sell": sell, "date": date}


def fetch_nxt_foreign(trade_type: str = "NXT") -> dict | None:
    """NXT 외국인 순매수/순매도 상위. graceful None."""
    return _fetch("FOREIGNER", trade_type)


def fetch_nxt_organ(trade_type: str = "NXT") -> dict | None:
    """NXT 기관 순매수/순매도 상위 — investor 코드 후보 다중 시도. graceful None."""
    for inv in _ORGAN_CANDIDATES:
        out = _fetch(inv, trade_type)
        if out:
            out["investor_code"] = inv
            return out
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for label, fn in (("외국인", fetch_nxt_foreign), ("기관", fetch_nxt_organ)):
        d = fn()
        if not d:
            print(f"[{label}] 데이터 없음")
            continue
        print(f"[{label} NXT {d.get('date')}] 순매수 {len(d['buy'])} / "
              f"순매도 {len(d['sell'])}"
              + (f" (investor={d.get('investor_code')})" if d.get("investor_code") else ""))
        for r in d["buy"][:3]:
            print(f"  +{r['name']} 순매수 {r['net_amt']}억 ({r['pct']:+.2f}%)")
