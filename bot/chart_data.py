"""Price-chart payload builder for the per-analysis detail page.

Produces a compact JSON-serializable dict (parallel arrays) that the
dashboard embeds and lightweight-charts renders client-side. The close
series + 10 EMA / 50 SMA / 200 SMA are computed from the SAME yfinance
1y close series as `_compute_technical_snapshot` (agent_utils), so the
chart's moving-average lines agree with the text TECHNICAL SNAPSHOT
(SSoT). Re-fetched here (not threaded through the graph run) for
decoupling — the analysis just finished seconds ago, so the daily-close
series is identical to what the analysts saw.

Universal: works for US + KR + JP + TW + CN/HK + EU (any yfinance-
covered ticker). Currency symbol / decimals come from market.py so the
chart price scale renders ₩ / ¥ / $ / € correctly. Returns None on any
failure — the detail page then renders text-only (graceful).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def build_price_chart(ticker: str, as_of: str | None = None) -> dict | None:
    """Return a compact chart payload for `ticker`, or None on failure.

    `as_of` (YYYY-MM-DD): when given, fetch the 1y window ENDING at that
    date (for backfilling old archive entries — no look-ahead, and the
    moving averages match the analysis-date TECHNICAL SNAPSHOT). When
    None (live path at analysis time), use period='1y' (ends today ≈
    analysis date).

    Shape (parallel arrays aligned to `times`):
        {
          "currency": "$",          # market currency symbol
          "decimals": 2,            # 0 for KRW/JPY, else 2
          "times":  ["2025-06-03", ...],   # daily (yyyy-mm-dd)
          "close":  [145.20, ...],
          "ema10":  [144.10, ...],         # full length
          "sma50":  [null, ..., 140.0, ...],   # null until 50th bar
          "sma200": [null, ..., 130.0, ...],   # omitted if <200 bars
        }
    """
    try:
        import yfinance as yf

        # Mirror _compute_technical_snapshot: 1y window, auto-adjusted.
        if as_of:
            from datetime import datetime, timedelta
            try:
                end_d = datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                end_d = None
            if end_d is not None:
                # ~400 calendar days ≈ 275 trading days — enough for a
                # trailing 200 SMA at the window's end + visible context.
                start_d = end_d - timedelta(days=400)
                hist = yf.Ticker(ticker).history(
                    start=start_d.strftime("%Y-%m-%d"),
                    end=end_d.strftime("%Y-%m-%d"),
                    auto_adjust=True,
                )
            else:
                hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        else:
            hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if hist is None or len(hist) < 20:
            return None
        close = hist["Close"].dropna()
        if len(close) < 20:
            return None

        currency, decimals = _currency_for(ticker)
        vol = hist["Volume"].reindex(close.index) if "Volume" in hist else None
        op = hist["Open"].reindex(close.index) if "Open" in hist else None
        hi = hist["High"].reindex(close.index) if "High" in hist else None
        lo = hist["Low"].reindex(close.index) if "Low" in hist else None
        return _series_payload(close, currency, decimals, vol, op, hi, lo)
    except Exception as exc:
        log.warning("chart_data: build failed for %s: %s", ticker, exc)
        return None


def _currency_for(ticker: str) -> tuple[str, int]:
    """Market-aware currency symbol + decimals (0 for KRW/JPY, else 2).
    Same source as the text snapshot's Bollinger / MA formatting."""
    try:
        from bot.market import get_market_config as _gcfg
        _c = _gcfg(ticker)
        return (
            _c.get("currency_symbol", "$"),
            0 if _c.get("currency") in ("KRW", "JPY") else 2,
        )
    except Exception:
        return "$", 2


def _series_payload(
    close, currency: str, decimals: int,
    volume=None, opens=None, highs=None, lows=None,
) -> dict:
    """Build the parallel-array chart payload from a pandas close Series.

    Includes: close + 10 EMA / 50 SMA / 200 SMA + RSI(14) + volume +
    Bollinger(20,2σ) + MACD(12,26,9) + OHLC (for candlestick toggle).
    Bollinger / MACD use the SAME formulas as _compute_technical_snapshot
    (SSoT) so the chart overlays match the text analysis. Each block is
    omitted gracefully when the series is too short or inputs are absent."""
    import math

    ema10 = close.ewm(span=10, adjust=False).mean()
    sma50 = close.rolling(50).mean() if len(close) >= 50 else None
    sma200 = close.rolling(200).mean() if len(close) >= 200 else None

    def _round(v, nd=decimals) -> float | None:
        try:
            f = float(v)
            if math.isnan(f):
                return None
            return round(f, nd)
        except Exception:
            return None

    payload: dict = {
        "currency": currency,
        "decimals": decimals,
        "times": [d.strftime("%Y-%m-%d") for d in close.index],
        "close": [_round(v) for v in close.values],
        "ema10": [_round(v) for v in ema10.values],
    }
    if sma50 is not None:
        payload["sma50"] = [_round(v) for v in sma50.values]
    if sma200 is not None:
        payload["sma200"] = [_round(v) for v in sma200.values]

    # OHLC (candlestick toggle). open/high/low aligned to close.index.
    if opens is not None and highs is not None and lows is not None:
        payload["open"] = [_round(v) for v in opens.values]
        payload["high"] = [_round(v) for v in highs.values]
        payload["low"] = [_round(v) for v in lows.values]

    # Bollinger Bands(20, 2σ) — overlay. Same as snapshot SSoT.
    if len(close) >= 20:
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        payload["bb_u"] = [_round(v) for v in (bb_mid + 2 * bb_std).values]
        payload["bb_m"] = [_round(v) for v in bb_mid.values]
        payload["bb_l"] = [_round(v) for v in (bb_mid - 2 * bb_std).values]

    # RSI(14) — Wilder exponential smoothing (same as snapshot SSoT).
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
        payload["rsi"] = [_round(v, 1) for v in rsi.values]

    # MACD(12, 26, 9) — lower pane (line + signal + histogram). Same as
    # snapshot SSoT. Extra decimal precision (3) since values are small.
    if len(close) >= 27:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        macd_sig = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - macd_sig
        md = max(decimals, 3)
        payload["macd"] = [_round(v, md) for v in macd_line.values]
        payload["macd_signal"] = [_round(v, md) for v in macd_sig.values]
        payload["macd_hist"] = [_round(v, md) for v in macd_hist.values]

    # Volume — histogram in the price pane bottom. Integer counts.
    if volume is not None:
        def _vol(v):
            try:
                f = float(v)
                if math.isnan(f):
                    return None
                return int(f)
            except Exception:
                return None
        payload["volume"] = [_vol(v) for v in volume.values]
    return payload


# On-demand timeframe fetch (dashboard /api/chart endpoint). interval +
# period are whitelisted; MAs (10/50/200) recompute on the chosen interval
# (so weekly view = 10wk/50wk/200wk — diverges from the daily text SSoT,
# which is expected). Returns None on failure (client keeps current view).
_VALID_INTERVALS = {"1d", "1wk", "1mo"}
_VALID_PERIODS = {"1mo", "3mo", "6mo", "1y", "3y", "5y", "max"}
# Range → 대략 캘린더 일수. yfinance 의 period 문자열에는 '3y' 가 없어
# (유효: 1mo/3mo/6mo/1y/2y/5y/10y/max) 전 범위를 start/end 로 통일 fetch
# → '3y' 정상 동작 + 1개월/3개월 추가가 일관되게 작동.
_RANGE_DAYS = {
    "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "3y": 1100, "5y": 1830,
}


def fetch_chart_payload(
    ticker: str, interval: str = "1d", period: str = "1y"
) -> dict | None:
    if interval not in _VALID_INTERVALS:
        interval = "1d"
    if period not in _VALID_PERIODS:
        period = "1y"
    try:
        import yfinance as yf
        from datetime import datetime, timedelta

        t = yf.Ticker(ticker)
        if period == "max":
            hist = t.history(period="max", interval=interval, auto_adjust=True)
        else:
            end = datetime.now() + timedelta(days=1)
            start = end - timedelta(days=_RANGE_DAYS.get(period, 366))
            hist = t.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=True,
            )
        if hist is None or len(hist) < 2:
            return None
        close = hist["Close"].dropna()
        if len(close) < 2:
            return None
        currency, decimals = _currency_for(ticker)
        vol = hist["Volume"].reindex(close.index) if "Volume" in hist else None
        op = hist["Open"].reindex(close.index) if "Open" in hist else None
        hi = hist["High"].reindex(close.index) if "High" in hist else None
        lo = hist["Low"].reindex(close.index) if "Low" in hist else None
        payload = _series_payload(close, currency, decimals, vol, op, hi, lo)
        payload["interval"] = interval
        payload["period"] = period
        return payload
    except Exception as exc:
        log.warning(
            "chart_data: fetch_chart_payload failed %s %s %s: %s",
            ticker, interval, period, exc,
        )
        return None


# Trade-plan price levels emitted in full_report by the Trader / PM:
#   **Entry Price**: ₩12,345   /   **Stop Loss**: ...   /   **Price Target**: ...
# plus Korean variants (진입가 / 손절가·손절매 / 목표가). Number may carry a
# currency prefix (₩/$/¥/€/NT$/A$) + thousands commas + optional decimals.
_LEVEL_PATTERNS = {
    "entry": re.compile(
        r"(?:Entry\s*Price|진입\s*가격?|매수\s*가)\s*\*{0,2}\s*[:：=]\s*"
        r"[^\d\n]{0,6}?([\d][\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    "stop": re.compile(
        r"(?:Stop\s*Loss|손절\s*가격?|손절매|매도\s*가)\s*\*{0,2}\s*[:：=]\s*"
        r"[^\d\n]{0,6}?([\d][\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    "target": re.compile(
        r"(?:Price\s*Target|Target\s*Price|목표\s*가격?|익절)\s*\*{0,2}\s*[:：=]\s*"
        r"[^\d\n]{0,6}?([\d][\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
}


def parse_trade_levels(
    full_report: str, close_values: list | None = None
) -> dict:
    """Extract entry / stop / target price levels from a saved full_report.

    Returns a dict with only the PLAUSIBLE levels present, e.g.
    ``{"entry": 145.0, "stop": 138.0, "target": 160.0}``. A parsed value
    is kept only when it falls within a sane band relative to the chart's
    close series (0.2x–5x of the last close) — this rejects mis-parses /
    unit slips so the chart never draws a misleading horizontal line
    (the exact risk that kept markers out of chart v1). Empty close_values
    → no plausibility filter possible → return {} (conservative).
    """
    if not full_report or not close_values:
        return {}
    try:
        last = float(close_values[-1])
        lo, hi = last * 0.2, last * 5.0
    except Exception:
        return {}
    out: dict = {}
    for key, pat in _LEVEL_PATTERNS.items():
        m = pat.search(full_report)
        if not m:
            continue
        try:
            val = float(m.group(1).replace(",", ""))
        except (ValueError, AttributeError):
            continue
        if lo <= val <= hi:
            out[key] = round(val, 2)
        else:
            log.info(
                "chart_data: %s level %.2f implausible vs last close %.2f — skipped",
                key, val, last,
            )
    return out
