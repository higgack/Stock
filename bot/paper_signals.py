"""NOAH 판정 → 페이퍼 자동 주문 (E0.5b).

설계 §1 '분석 ≠ 실행 분리': analyzer 는 신호만 내고, **이 레이어가** 그
신호를 페이퍼 주문으로 변환한다(analyzer 는 paper 를 모름). `auto on` 일
때만 동작 — 자동매매는 opt-in 이 안전.

- 매수(Buy/Overweight) → 자본 `AUTO_SIZING_PCT` 사이징 페이퍼 매수(Risk Gate
  통과 시), 진입 후 `HORIZON_DAYS` 거래일 자동 청산(NOAH 평가 윈도 일치).
- 매도(Sell/Underweight) → 보유 중이면 청산.
- 보유(Hold) → 무시.
멱등: idem=auto:{ticker}:{KST date} → 같은 날 같은 종목 자동 주문 1회.
graceful — 어떤 예외도 분석 흐름을 깨지 않게 None 으로 degrade.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

AUTO_SIZING_PCT = 0.05     # 매수당 자본 비율 (Risk Gate 캡 안)
HORIZON_DAYS = 5           # 진입 후 자동 청산까지 거래일 (NOAH 5거래일 윈도)
_KST = timezone(timedelta(hours=9))


def _direction(rating: str) -> str:
    """판정 문자열 → up(매수) / down(매도) / hold."""
    r = (rating or "").strip().lower()
    if any(k in r for k in ("buy", "overweight", "매수")):
        return "up"
    if any(k in r for k in ("sell", "underweight", "매도")):
        return "down"
    return "hold"


def on_analysis(ticker: str, rating: str) -> Optional[str]:
    """분석 완료 시 호출 — auto on 이면 판정대로 페이퍼 주문. 알림 텍스트 또는
    None(무동작/auto off/예외) 반환. 호출부는 반환 텍스트를 채널에 전달."""
    try:
        from bot import paper_trading
        if not paper_trading.auto_enabled():
            return None
        tkr = (ticker or "").strip().upper()
        today = datetime.now(_KST).strftime("%Y-%m-%d")
        d = _direction(rating)

        if d == "up":
            base = paper_trading.starting_capital_krw()
            if base <= 0:
                return None
            ok, msg = paper_trading.buy_value(
                tkr, base * AUTO_SIZING_PCT,
                idem=f"auto:{tkr}:{today}", horizon_days=HORIZON_DAYS)
            return (f"🤖 자동매수 (판정 {rating}): {msg}" if ok
                    else f"🤖 자동매수 보류 (판정 {rating}): {msg}")

        if d == "down":
            held = tkr in paper_trading.get_account().get("positions", {})
            if not held:
                return None
            ok, msg = paper_trading.close_position(tkr, idem=f"auto:{tkr}:{today}")
            return f"🤖 자동청산 (판정 {rating}): {msg}" if ok else None

        return None   # hold → 무동작
    except Exception as exc:
        log.debug("paper_signals.on_analysis(%s) failed: %s", ticker, exc)
        return None
