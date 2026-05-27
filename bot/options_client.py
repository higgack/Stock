"""US equity options market signals — ATM IV + put/call ratios.

Uses yfinance.Ticker.option_chain() (no API key, public market data).
Prefetched in parallel with EDGAR tasks for US analyses.

Signals computed:
  - Front-month ATM implied volatility (%)
  - Put/call volume ratio (front month)
  - Put/call open-interest ratio (front month)

Silent degradation for tickers without listed options (small-caps, some
ETFs). Rule applies to all US-listed equity analyses going forward.
"""
from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger("options_client")


def get_options_signals(ticker: str) -> Optional[dict]:
    """Compute front-month ATM IV + put/call ratios via yfinance.

    Returns dict with keys iv_atm, pcr_volume, pcr_oi, expiry —
    or None on any error / no-options ticker.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return None

        expiry = exps[0]  # nearest = front month
        chain = t.option_chain(expiry)
        calls = chain.calls.copy()
        puts = chain.puts.copy()
        if calls.empty or puts.empty:
            return None

        # Spot price from fast_info (avoids duplicate .info HTTP call)
        fi = t.fast_info
        spot = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
        if not spot:
            return None

        # ATM IV: average IV of 3 call strikes nearest spot
        calls["_dist"] = (calls["strike"] - spot).abs()
        atm_iv = calls.nsmallest(3, "_dist")["impliedVolatility"].dropna()
        iv_atm = float(atm_iv.mean()) if not atm_iv.empty else None

        call_vol = float(calls["volume"].fillna(0).sum())
        put_vol  = float(puts["volume"].fillna(0).sum())
        call_oi  = float(calls["openInterest"].fillna(0).sum())
        put_oi   = float(puts["openInterest"].fillna(0).sum())

        return {
            "iv_atm":     round(iv_atm, 4) if iv_atm is not None else None,
            "pcr_volume": round(put_vol / call_vol, 3) if call_vol > 0 else None,
            "pcr_oi":     round(put_oi / call_oi, 3)  if call_oi > 0 else None,
            "expiry":     expiry,
        }
    except Exception as exc:
        _log.debug("options_signals failed for %s: %s", ticker, exc)
        return None


def format_options_block(signals: Optional[dict]) -> str:
    """Format options signals dict for agent prompt injection."""
    if not signals:
        return ""

    expiry = signals.get("expiry", "?")
    lines = [f"=== US 옵션 시장 시그널 (yfinance, 만기: {expiry}) ==="]

    iv = signals.get("iv_atm")
    if iv is not None:
        iv_pct = round(iv * 100, 1)
        if iv_pct >= 60:
            iv_tag = "극단적 불확실성 — 이벤트 임박 또는 시장 공포"
        elif iv_pct >= 40:
            iv_tag = "높음 — 변동성 프리미엄 상승 / 시장 우려"
        elif iv_pct >= 20:
            iv_tag = "보통"
        else:
            iv_tag = "낮음 — 시장 안도 / 변동성 프리미엄 압축"
        lines.append(f"ATM IV (내재변동성): {iv_pct:.1f}% [{iv_tag}]")

    pcr_v = signals.get("pcr_volume")
    if pcr_v is not None:
        if pcr_v >= 1.5:
            pcr_tag = "풋 편중 — 공포 / 헤지 수요 급증"
        elif pcr_v >= 0.8:
            pcr_tag = "중립"
        else:
            pcr_tag = "콜 편중 — 낙관 / 투기적 매수"
        lines.append(f"Put/Call 거래량 비율: {pcr_v:.2f} [{pcr_tag}]")

    pcr_oi = signals.get("pcr_oi")
    if pcr_oi is not None:
        lines.append(f"Put/Call 미결제약정 비율: {pcr_oi:.2f}")

    lines.append(
        "▶ ATM IV = 시장의 5거래일 내 변동 기대치."
        " IV≥40% = 이벤트/실적 임박 또는 공포 신호."
        " P/C 거래량 비율≥1.2 = 풋 헤지 증가 (하방 우려)."
        " 분석가는 이 수치를 결론의 리스크/catalyst 섹션에 인용."
    )
    return "\n".join(lines)
