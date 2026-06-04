"""Price-glitch sanity guards (shared, dependency-light).

yfinance occasionally returns a single bad value — a wrong split/adjustment
basis, stale, or junk price — for one ticker at one moment. Grafted into a
chart or an analysis it paints a phantom crash: 파크시스템스 140860.KS
2026-05-20 showed 현재가 ₩163,700 against a real ~₩280-300K (below its own
52-week low ₩205,000, -43% from the 10 EMA), and the whole bearish thesis +
Sell/Underweight verdict was built on that phantom value.

These pure helpers DETECT such outliers so callers can REPLACE the price with
the last good close (preferred) or, failing that, BLOCK price-derived
reasoning (user policy 2026-06-04: 교체 우선·차단 폴백). Dependency-light
(stdlib only; the market lookup is the caller's job) so they're unit-testable
without the heavy analysis stack. Universal — every market.
"""
from __future__ import annotations

# Daily-price-limit markets (KR ±30% / TW ±10% / CN ±10–20% / JP) cannot move
# beyond their limit in one session, so a larger single-session gap is
# physically impossible = a data glitch. No-limit markets (US/EU/HK) use a
# looser bound so genuine news gaps (earnings, FDA, M&A) aren't dropped.
_LIMIT_MARKETS = ("KR", "TW", "CN_A", "JP")


def snapshot_gap_for_market(market: str) -> float:
    """Max plausible single-session move (vs the recent median) by market."""
    return 0.35 if market in _LIMIT_MARKETS else 0.50


def recent_median(values, n: int = 5):
    """Median of the up-to-`n` values *preceding* the last element, or None
    when fewer than 3 valid priors exist. Used to judge whether the LAST
    close is an outlier without being skewed by that last value itself."""
    try:
        prior = [float(v) for v in values[-(n + 1):-1]
                 if isinstance(v, (int, float)) and v == v]
    except Exception:
        return None
    if len(prior) < 3:
        return None
    s = sorted(prior)
    m = len(s)
    return s[m // 2] if m % 2 else (s[m // 2 - 1] + s[m // 2]) / 2.0


def last_close_is_glitch(values, max_gap: float) -> bool:
    """True when the LAST close is an implausible single-session outlier vs
    the median of the preceding closes (gap > max_gap), or is itself NaN/≤0.

    Conservative: needs ≥7 points, otherwise returns False (never over-fires
    on short/sparse series). Catches the yfinance bad-last-bar glitch
    (파크시스템스 140860 2026-05-20: last ≈ -45% vs the recent median)."""
    try:
        if not values or len(values) < 7:
            return False
        last = values[-1]
        if not (isinstance(last, (int, float)) and last == last and last > 0):
            return True   # NaN / ≤0 last bar is itself bad → drop it
        med = recent_median(values, 5)
        if med is None or med <= 0:
            return False
        return abs(float(last) / med - 1.0) > max_gap
    except Exception:
        return False


def price_outlier_vs_refs(px, low52=None, high52=None, sma50=None,
                          sma200=None, gap: float = 0.35) -> bool:
    """True when `px` contradicts the reference levels:

    - below the 52-week low / above the 52-week high (a logical
      impossibility — the 52w range includes the current price; primary
      signal), or
    - far (> gap) from BOTH the 50- and 200-day moving averages
      (corroborating, used when 52w refs are absent).

    Only fires when the relevant refs are valid; no usable refs → False
    (can't judge, never over-fires). 파크시스템스 140860 2026-05-20:
    현재가 ₩163,700 < 52주 최저 ₩205,000 → True."""
    try:
        px = float(px)
    except (TypeError, ValueError):
        return False
    if not (px == px and px > 0):
        return False

    def _v(x):
        return float(x) if isinstance(x, (int, float)) and x == x and x > 0 else None

    low52, high52, sma50, sma200 = _v(low52), _v(high52), _v(sma50), _v(sma200)
    if low52 is not None and px < low52 * 0.97:
        return True
    if high52 is not None and px > high52 * 1.03:
        return True
    if (sma50 is not None and sma200 is not None
            and abs(px / sma50 - 1.0) > gap and abs(px / sma200 - 1.0) > gap):
        return True
    return False
