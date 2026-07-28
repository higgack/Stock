"""US equity options market signals — ATM IV + put/call ratios.

Uses yfinance.Ticker.option_chain() (no API key, public market data).
Prefetched in parallel with EDGAR tasks for US analyses.

Signals computed:
  - Front-expiry ATM implied volatility (%) + days-to-expiry (DTE)
  - ~30-day baseline ATM IV (term-structure reference)
  - Put/call volume ratio (front expiry)
  - Put/call open-interest ratio (front expiry)

The front expiry on weekly-listed names is often a 0-7 DTE weekly. Across
an earnings event its ANNUALISED ATM IV mechanically explodes (100%+),
because the whole expected move is priced into ~1 day of time. That spike
is a genuine event proxy when earnings is imminent, but a false "extreme
fear" signal on a quiet weekly. We therefore surface BOTH the front-expiry
IV (event proxy) and a ~30-day baseline IV (stable reference), and let the
formatter cross-reference the earnings calendar so the analyst reads the
spike correctly. CRM 2026-05-28 surfaced this (122.2% front-weekly IV on an
earnings day drove the PM verdict).

Silent degradation for tickers without listed options (small-caps, some
ETFs). Rule applies to all US-listed equity analyses going forward.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

_log = logging.getLogger("options_client")

# Target horizon for the stable baseline IV (VIX-style 30-day convention).
_BASELINE_TARGET_DAYS = 30


def _atm_iv_for_expiry(t, expiry: str, spot: float) -> Optional[float]:
    """Mean implied vol of the 3 call strikes nearest spot for one expiry."""
    try:
        chain = t.option_chain(expiry)
        calls = chain.calls
        if calls is None or calls.empty:
            return None
        calls = calls.copy()
        calls["_dist"] = (calls["strike"] - spot).abs()
        atm_iv = calls.nsmallest(3, "_dist")["impliedVolatility"].dropna()
        return float(atm_iv.mean()) if not atm_iv.empty else None
    except Exception:
        return None


def _dte(expiry: str) -> Optional[int]:
    """Calendar days from today to an 'YYYY-MM-DD' expiry string."""
    try:
        return (_dt.date.fromisoformat(expiry) - _dt.date.today()).days
    except Exception:
        return None


def get_options_signals(ticker: str) -> Optional[dict]:
    """Disk-cached (ticker, today; 12h TTL) wrapper around the option-chain
    computation. F6 (2026-05-29 audit): the full option-chain download is
    among the heaviest yfinance endpoints and was re-run on every
    build_instrument_context call (up to 8× per analysis) with no cache and
    no explicit timeout. Intraday IV / PCR drift is tolerable at the 5-day
    horizon (same rationale as the cnyes 4h news TTL). Only non-None results
    are cached so a transient failure isn't pinned for 12h."""
    import json as _json
    import time as _time
    from pathlib import Path as _Path
    _path = (
        _Path.home() / ".tradingagents" / "cache"
        / f"options_{ticker}_{_dt.date.today().isoformat()}.json"
    )
    if _path.exists():
        try:
            if (_time.time() - _path.stat().st_mtime) / 3600 < 12:
                return _json.loads(_path.read_text("utf-8"))
        except Exception:
            pass
    result = _compute_options_signals(ticker)
    if result:
        try:
            _path.parent.mkdir(parents=True, exist_ok=True)
            _path.write_text(
                _json.dumps(result, ensure_ascii=False), encoding="utf-8",
            )
        except Exception:
            pass
    return result


def _compute_options_signals(ticker: str) -> Optional[dict]:
    """Compute front-expiry ATM IV + put/call ratios + 30-day baseline IV.

    Returns dict with keys iv_atm, dte, iv_atm_30d, expiry_30d, pcr_volume,
    pcr_oi, expiry — or None on any error / no-options ticker.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        exps = t.options
        if not exps:
            return None

        expiry = exps[0]  # nearest listed expiry (often a weekly)
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

        # ATM IV: average IV of 3 call strikes nearest spot (front expiry)
        calls["_dist"] = (calls["strike"] - spot).abs()
        atm_iv = calls.nsmallest(3, "_dist")["impliedVolatility"].dropna()
        iv_atm = float(atm_iv.mean()) if not atm_iv.empty else None

        call_vol = float(calls["volume"].fillna(0).sum())
        put_vol  = float(puts["volume"].fillna(0).sum())
        call_oi  = float(calls["openInterest"].fillna(0).sum())
        put_oi   = float(puts["openInterest"].fillna(0).sum())

        # ~30-day baseline IV: the listed expiry closest to 30 days out.
        # Skips the front weekly so a 1-DTE earnings straddle can't masquerade
        # as the stock's representative volatility level.
        iv_30d: Optional[float] = None
        expiry_30d: Optional[str] = None
        dated = [(e, _dte(e)) for e in exps]
        dated = [(e, d) for e, d in dated if d is not None and d >= 0]
        if dated:
            target = min(
                dated, key=lambda ed: abs(ed[1] - _BASELINE_TARGET_DAYS)
            )
            expiry_30d = target[0]
            if expiry_30d == expiry:
                iv_30d = iv_atm  # only one usable expiry — same as front
            else:
                iv_30d = _atm_iv_for_expiry(t, expiry_30d, spot)

        return {
            "iv_atm":     round(iv_atm, 4) if iv_atm is not None else None,
            "dte":        _dte(expiry),
            "iv_atm_30d": round(iv_30d, 4) if iv_30d is not None else None,
            "expiry_30d": expiry_30d,
            "pcr_volume": round(put_vol / call_vol, 3) if call_vol > 0 else None,
            "pcr_oi":     round(put_oi / call_oi, 3)  if call_oi > 0 else None,
            "expiry":     expiry,
        }
    except Exception as exc:
        _log.debug("options_signals failed for %s: %s", ticker, exc)
        return None


def _iv_tag(iv_pct: float) -> str:
    if iv_pct >= 60:
        return "극단적 — 이벤트 임박 또는 시장 공포"
    if iv_pct >= 40:
        return "높음 — 변동성 프리미엄 상승 / 시장 우려"
    if iv_pct >= 20:
        return "보통"
    return "낮음 — 시장 안도 / 변동성 프리미엄 압축"


def format_options_block(
    signals: Optional[dict], days_to_earnings: Optional[int] = None
) -> str:
    """Format options signals dict for agent prompt injection.

    days_to_earnings: signed int from days_until_earnings() — positive = days
    until next earnings, negative = days since. Used to attribute a front-IV
    spike to an imminent earnings event (expected, normal) vs flagging it as a
    front-weekly annualisation artifact on a quiet week.
    """
    if not signals:
        return ""

    expiry = signals.get("expiry", "?")
    dte = signals.get("dte")
    dte_str = f", {dte}일 후 만기" if dte is not None else ""
    lines = [f"=== US 옵션 시장 시그널 (yfinance, 만기: {expiry}{dte_str}) ==="]

    iv = signals.get("iv_atm")
    iv_pct = round(iv * 100, 1) if iv is not None else None
    iv30 = signals.get("iv_atm_30d")
    iv30_pct = round(iv30 * 100, 1) if iv30 is not None else None

    # Earnings inside the front expiry window? (front DTE, fall back to 7d)
    window = dte if (dte is not None and dte >= 0) else 7
    earnings_imminent = (
        days_to_earnings is not None and -2 <= days_to_earnings <= window
    )
    # Short-dated front expiry whose annualised IV is structurally inflated.
    front_weekly = dte is not None and 0 <= dte <= 7

    if iv_pct is not None:
        label = f"전방 만기 ATM IV: {iv_pct:.1f}% [{_iv_tag(iv_pct)}]"
        lines.append(label)
    if iv30_pct is not None and signals.get("expiry_30d") != expiry:
        lines.append(
            f"~30일 baseline ATM IV: {iv30_pct:.1f}% [{_iv_tag(iv30_pct)}]"
            "  ← 종목의 대표 변동성 수준"
        )

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

    # Earnings-aware interpretation footer.
    if earnings_imminent:
        lines.append(
            f"▶ 실적 발표 임박 (D{days_to_earnings:+d}). 전방 만기 IV의"
            " 상승은 실적 이벤트를 가격에 반영한 정상적 프리미엄이며"
            " '시장 공포'가 아니다 — 발표 직후 IV crush (급락) 예상."
            " 5거래일 horizon 에서는 양방향 변동성이 극대화되므로, 발표"
            " 결과 확인 전 신규 방향성 베팅은 과도한 리스크. baseline(~30일)"
            " IV 가 종목의 평상시 변동성 수준이다."
        )
    elif front_weekly and iv_pct is not None and iv30_pct is not None and iv_pct >= iv30_pct * 1.5:
        lines.append(
            f"▶ 전방 만기가 단기 위클리(D-{dte})라 연율화 ATM IV({iv_pct:.0f}%)"
            " 가 구조적으로 과대될 수 있다. 임박한 실적 이벤트는 감지되지"
            f" 않았으므로, 종목의 실제 변동성은 ~30일 baseline({iv30_pct:.0f}%)"
            " 을 기준으로 판단하라. 전방-30일 IV 격차가 클수록 시장이 단기"
            " 이벤트(미확인)를 가격에 반영 중일 가능성."
        )
    else:
        lines.append(
            "▶ ATM IV = 시장의 단기 변동 기대치. IV≥40% = 이벤트/실적"
            " 임박 또는 공포 신호. P/C 거래량 비율≥1.2 = 풋 헤지 증가"
            " (하방 우려). baseline(~30일) IV 가 종목의 대표 변동성"
            " 수준이며, 전방 만기 IV 가 이를 크게 상회하면 단기 이벤트"
            " 프리미엄으로 해석. 분석가는 이 수치를 결론의 리스크/"
            "catalyst 섹션에 인용."
        )
    return "\n".join(lines)
