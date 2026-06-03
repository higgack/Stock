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

log = logging.getLogger(__name__)


def build_price_chart(ticker: str) -> dict | None:
    """Return a compact chart payload for `ticker`, or None on failure.

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
        hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if hist is None or len(hist) < 20:
            return None
        close = hist["Close"].dropna()
        if len(close) < 20:
            return None

        # Market-aware currency symbol / decimals (same source as the
        # text snapshot's Bollinger / MA formatting).
        try:
            from bot.market import get_market_config as _gcfg
            _c = _gcfg(ticker)
            currency = _c.get("currency_symbol", "$")
            decimals = 0 if _c.get("currency") in ("KRW", "JPY") else 2
        except Exception:
            currency, decimals = "$", 2

        ema10 = close.ewm(span=10, adjust=False).mean()
        sma50 = close.rolling(50).mean() if len(close) >= 50 else None
        sma200 = close.rolling(200).mean() if len(close) >= 200 else None

        def _round(v) -> float | None:
            try:
                import math
                f = float(v)
                if math.isnan(f):
                    return None
                return round(f, decimals)
            except Exception:
                return None

        times = [d.strftime("%Y-%m-%d") for d in close.index]
        payload: dict = {
            "currency": currency,
            "decimals": decimals,
            "times": times,
            "close": [_round(v) for v in close.values],
            "ema10": [_round(v) for v in ema10.values],
        }
        if sma50 is not None:
            payload["sma50"] = [_round(v) for v in sma50.values]
        if sma200 is not None:
            payload["sma200"] = [_round(v) for v in sma200.values]
        return payload
    except Exception as exc:
        log.warning("chart_data: build failed for %s: %s", ticker, exc)
        return None
