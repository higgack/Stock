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


def exact_session_gap(prev_close, upper_limit, lower_limit):
    """Precise max plausible single-session fractional move from a stock's
    OWN daily price limits (vs the per-market hardcoded `snapshot_gap_for_
    market`). For KR the exchange publishes per-stock 상한가/하한가 (≈ ±30%
    of the prior close, but exact); using that as the glitch threshold is
    tighter and more accurate than the blanket 0.35 — it catches a -32%
    phantom that 0.35 would wave through, and won't false-fire inside the
    real limit.

    Returns max(|upper/prev − 1|, |lower/prev − 1|), or None when any input
    is missing/invalid (caller then keeps the market default). Pure /
    stdlib-only — unit-testable. Universal helper; the limit *source* is
    market-specific (KR via KIS today), the principle applies anywhere a
    venue has daily price limits."""
    try:
        pc, u, l = float(prev_close), float(upper_limit), float(lower_limit)
    except (TypeError, ValueError):
        return None
    if not (pc > 0 and u > 0 and l > 0):
        return None
    gap = max(abs(u / pc - 1.0), abs(l / pc - 1.0))
    # Sanity: a sane limit gap is within (0, 0.35]. Outside that the fields
    # are likely junk/misparsed → fall back to the market default.
    return gap if 0.0 < gap <= 0.35 else None


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


def within_52w_range(px, low52, high52, tol: float = 0.03) -> bool:
    """True when `px` sits INSIDE the 52-week [low, high] band (±tol).

    A genuine large single-session move in a no-limit market (CAST +126%
    / RGNT +752% 2026-06-15, US) lands INSIDE the 52-week range — it is a
    real news move, not a split glitch. Magnitude vs the prior close
    alone (e.g. >75%) can NOT tell the two apart, so callers gate the
    magnitude-based glitch guard on this: in-range → trust the price (skip
    the guard); only a price OUTSIDE the 52w band (KLAC $2,411 vs ~$300
    high) is the impossible-move glitch worth correcting.

    Returns False when either 52w ref is missing/invalid — the caller
    then falls back to the magnitude guard (conservative; never suppresses
    a real glitch just because refs are absent)."""
    try:
        lo, hi, p = float(low52), float(high52), float(px)
    except (TypeError, ValueError):
        return False
    return lo > 0 and hi > 0 and lo * (1 - tol) <= p <= hi * (1 + tol)


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


def quote_glitch_gap(last, prev, max_gap: float = 0.75) -> bool:
    """단일 호가(현재가 vs 직전 종가) 글리치 판정 — KLAC 2026-06-12 클래스.

    yfinance fast_info/info 가 분할 미조정·stale 값을 last_price 로 반환하면
    가격·등락·시총(가격×주식수)이 한꺼번에 깨진다 (KLAC $2,411/$3.15T).
    대형주 일일 ±75% 는 무한도 시장(US)에서도 비현실 — 진짜 뉴스 갭은
    크게 잡아도 ±50% 안쪽이라 0.75 는 false-fire 없이 글리치만 잡는다.
    KR/TW/CN/JP 는 일일 한도가 있어 더더욱 안전. 입력 결측/0 은 False
    (판정 불가 시 손대지 않음 — 보수적). 순수·stdlib (전 대시보드 공유:
    관심종목·미국 무버·시총 fetch·검색카드)."""
    try:
        lp, pc = float(last), float(prev)
        if lp <= 0 or pc <= 0:
            return False
        return abs(lp / pc - 1.0) > max_gap
    except (TypeError, ValueError):
        return False


# 일본 TSE 制限値幅(daily price limit) 테이블 — 基準値段(전일종가) → 제한폭(엔).
# 가격대별 tiered(±% 고정 아님). ストップ高/安(상한가/하한가) 판정용 (사용자
# 2026-06-13 JP 상하한가). 공개·고정 표라 결정적(스크래핑 불요·테스트 가능).
_JP_LIMIT_TABLE = (
    (100, 30), (200, 50), (500, 80), (700, 100), (1000, 150),
    (1500, 300), (2000, 400), (3000, 500), (5000, 700), (7000, 1000),
    (10000, 1500), (15000, 3000), (20000, 4000), (30000, 5000),
    (50000, 7000), (70000, 10000), (100000, 15000), (150000, 30000),
    (200000, 40000), (300000, 50000), (500000, 70000), (700000, 100000),
    (1000000, 150000), (1500000, 300000), (2000000, 400000),
    (3000000, 500000), (5000000, 700000), (7000000, 1000000),
    (10000000, 1500000),
)


def jp_price_limit(prev_close) -> float | None:
    """일본 制限値幅(엔) — 전일종가 기준. ストップ高 = prev+limit, ストップ安 =
    prev-limit. None on bad input. (예: ¥3,000 → ±¥700, ¥150 → ±¥50)."""
    try:
        p = float(prev_close)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    for thr, width in _JP_LIMIT_TABLE:
        if p < thr:
            return float(width)
    return float(_JP_LIMIT_TABLE[-1][1])
