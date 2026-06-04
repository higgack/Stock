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
    # MA-divergence is a FALLBACK glitch signal — only meaningful when the
    # reliable 52-week range refs are unavailable. When BOTH 52w refs are valid
    # and px is within them (the two checks above didn't fire), px is a
    # plausible price; a large gap from the MAs is then a GENUINE strong trend
    # (parabolic up / deep drawdown), NOT a data glitch. Flagging it would
    # false-fire '데이터 이상' on correct data and bury the real froth/over-
    # extension signal — 삼성전기 009150 2026-06-04: 현재가 ₩1,716,000 ∈
    # 52주[₩122,800, ₩2,200,000], a genuine ~14x melt-up; 티로보틱스 117730:
    # in-range -41% real drawdown. So only use the MA fallback when we could
    # NOT already judge plausibility via a valid 52-week range.
    have_valid_52w = low52 is not None and high52 is not None
    if (not have_valid_52w and sma50 is not None and sma200 is not None
            and abs(px / sma50 - 1.0) > gap and abs(px / sma200 - 1.0) > gap):
        return True
    return False


def should_hard_freeze_technicals(px, low52, high52, signals) -> bool:
    """Given a >30% price-vs-SMA gap with external `signals`, decide HARD
    freeze (ban all technical indicators) vs SOFT (technicals still valid).

    Split-staleness — the case the freeze is meant for — pushes the current
    price OUTSIDE the 52-week range (the historical series is on a different
    split basis; 140860 2026-05-20: 현재가 < 52주 최저). A genuine decline /
    rally keeps the price WITHIN the 52-week range (티로보틱스 117730
    2026-06-04: 현재가 ₩11,280 ∈ [₩9,820, ₩30,900], a real -41% drawdown).

    So: a price-axis signal (split / 거래정지 / 시장경보) → always HARD. A
    non-price-axis signal only (e.g. shares×price↔marketCap divergence, which
    can itself be a suffix/valuation-data artifact) with the price IN-RANGE →
    SOFT (technicals are on a consistent basis; the divergence is a valuation
    concern, not a reason to freeze technicals). No signals → SOFT."""
    if not signals:
        return False
    price_axis = any(
        ("split" in str(s).lower() or "거래정지" in str(s) or "시장경보" in str(s))
        for s in signals
    )
    if price_axis:
        return True
    try:
        lo, hi, p = float(low52), float(high52), float(px)
        in_52w = lo > 0 and hi > 0 and lo <= p <= hi
    except (TypeError, ValueError):
        in_52w = False
    return not in_52w   # in-range → SOFT (real move); else/unknown → HARD
