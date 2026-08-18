"""Finnhub client — free US equity supplements (60 req/min).

Fills three US data gaps that yfinance + SEC EDGAR don't cover:
  - Earnings surprise: actual vs estimate EPS/revenue (8 quarters)
  - Analyst recommendation trends: buy/hold/sell distribution over time
  - Insider sentiment: MSPR aggregate (net insider buying ratio)

API: https://finnhub.io/api/v1
Auth: free API key required (FINNHUB_API_KEY in .env). 60 req/min.
All data is free tier.

12h disk cache per (ticker, endpoint, date).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

from bot.env_keys import env_key as _env_key

log = logging.getLogger("bot.finnhub")

_API = "https://finnhub.io/api/v1"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "finnhub"
_CACHE_TTL_HOURS = 12
_TIMEOUT = 15


def _api_key() -> Optional[str]:
    return _env_key("FINNHUB_API_KEY") or None


def _cache_get(key: str) -> Optional[dict]:
    f = _CACHE_DIR / key
    if not f.exists():
        return None
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h >= _CACHE_TTL_HOURS:
            return None
        return json.loads(f.read_text())
    except Exception:
        return None


def _cache_set(key: str, data):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / key).write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _fetch(endpoint: str, **params) -> Optional[dict | list]:
    key = _api_key()
    if not key:
        return None
    params["token"] = key
    try:
        resp = requests.get(f"{_API}{endpoint}", params=params, timeout=_TIMEOUT)
        if resp.status_code == 429:
            log.warning("finnhub: rate limited on %s", endpoint)
            return None
        if resp.status_code != 200:
            log.warning("finnhub: %s returned %d", endpoint, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        log.warning("finnhub: %s fetch failed: %s", endpoint, exc)
        return None


def _is_us_ticker(ticker: str) -> bool:
    if not ticker:
        return False
    parts = ticker.split(".")
    if len(parts) == 1:
        return True
    return parts[-1].upper() not in (
        "TW", "TWO", "KS", "KQ", "T", "HK", "SS", "SZ", "BJ",
        "L", "AS", "PA", "DE", "MI", "OL", "ST", "HE", "CO",
        "AX", "NS", "BO",
    )


def _symbol(ticker: str) -> str:
    parts = ticker.split(".")
    return parts[0] if len(parts) == 1 else ticker


# ------------------------------------------------------------------
# Earnings surprise (biggest gap)
# ------------------------------------------------------------------

def fetch_earnings_surprise(ticker: str, limit: int = 8) -> Optional[list]:
    """Quarterly EPS actual vs estimate for recent quarters."""
    if not _is_us_ticker(ticker):
        return None
    sym = _symbol(ticker)
    ck = f"earnings_{sym}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    data = _fetch("/stock/earnings", symbol=sym, limit=limit)
    if data and isinstance(data, list) and len(data) > 0:
        _cache_set(ck, data)
        return data
    return None


# ------------------------------------------------------------------
# Analyst recommendation trends
# ------------------------------------------------------------------

def fetch_recommendation_trends(ticker: str) -> Optional[list]:
    """Monthly analyst recommendation distribution (buy/hold/sell counts)."""
    if not _is_us_ticker(ticker):
        return None
    sym = _symbol(ticker)
    ck = f"rec_{sym}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    data = _fetch("/stock/recommendation", symbol=sym)
    if data and isinstance(data, list) and len(data) > 0:
        _cache_set(ck, data)
        return data
    return None


# ------------------------------------------------------------------
# Insider sentiment (MSPR)
# ------------------------------------------------------------------

def fetch_insider_sentiment(ticker: str) -> Optional[dict]:
    """Insider sentiment — MSPR (Monthly Share Purchase Ratio)."""
    if not _is_us_ticker(ticker):
        return None
    sym = _symbol(ticker)
    ck = f"insent_{sym}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    data = _fetch("/stock/insider-sentiment", symbol=sym, from_="2024-01-01")
    if data and isinstance(data, dict) and data.get("data"):
        _cache_set(ck, data)
        return data
    return None


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------

def format_earnings_block(rows: Optional[list]) -> str:
    """Format earnings surprise into a compact text block."""
    if not rows:
        return ""
    lines = ["• Earnings Surprise (Finnhub, 최근 분기):"]
    sorted_rows = sorted(rows, key=lambda r: r.get("period", ""), reverse=True)
    for r in sorted_rows[:8]:
        period = r.get("period", "?")
        actual = r.get("actual")
        estimate = r.get("estimate")
        surprise_pct = r.get("surprisePercent")
        if actual is None:
            continue
        est_str = f"est ${estimate:.2f}" if estimate is not None else "est N/A"
        act_str = f"act ${actual:.2f}"
        surp_str = ""
        if surprise_pct is not None:
            sign = "+" if surprise_pct >= 0 else ""
            beat = "Beat" if surprise_pct > 0 else ("Miss" if surprise_pct < 0 else "In-line")
            surp_str = f" → {beat} {sign}{surprise_pct:.1f}%"
        lines.append(f"  {period}: {act_str} / {est_str}{surp_str}")
    return "\n".join(lines) if len(lines) > 1 else ""


def format_recommendation_block(rows: Optional[list]) -> str:
    """Format recommendation trends into a compact text block."""
    if not rows:
        return ""
    sorted_rows = sorted(rows, key=lambda r: r.get("period", ""), reverse=True)
    latest = sorted_rows[0] if sorted_rows else {}
    sb = latest.get("strongBuy", 0)
    b = latest.get("buy", 0)
    h = latest.get("hold", 0)
    s = latest.get("sell", 0)
    ss = latest.get("strongSell", 0)
    total = sb + b + h + s + ss
    if total == 0:
        return ""
    period = latest.get("period", "?")[:10]
    buy_pct = (sb + b) / total * 100
    sell_pct = (s + ss) / total * 100
    parts = [
        f"Strong Buy {sb}",
        f"Buy {b}",
        f"Hold {h}",
        f"Sell {s}",
        f"Strong Sell {ss}",
    ]
    line = (
        f"• Analyst Recommendations ({period}, {total}명):"
        f" {' / '.join(parts)}"
        f" (Buy {buy_pct:.0f}% / Sell {sell_pct:.0f}%)"
    )

    if len(sorted_rows) >= 2:
        prev = sorted_rows[1]
        prev_buy = prev.get("strongBuy", 0) + prev.get("buy", 0)
        prev_total = sum(prev.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
        if prev_total > 0:
            prev_buy_pct = prev_buy / prev_total * 100
            delta = buy_pct - prev_buy_pct
            if abs(delta) >= 3:
                direction = "↑" if delta > 0 else "↓"
                line += f"\n  전월 대비 Buy 비중 {direction} {abs(delta):.0f}%p"

    return line


def format_insider_sentiment_block(data: Optional[dict]) -> str:
    """Format insider sentiment MSPR into a compact text block."""
    if not data or not data.get("data"):
        return ""
    rows = data["data"]
    sorted_rows = sorted(rows, key=lambda r: r.get("month", ""), reverse=True)
    recent = sorted_rows[:3]
    if not recent:
        return ""
    lines = ["• Insider Sentiment MSPR (Finnhub, 최근 3개월):"]
    for r in recent:
        month = r.get("month", "?")
        mspr = r.get("mspr", 0)
        change = r.get("change", 0)
        sign = "+" if mspr >= 0 else ""
        signal = "Net Buy" if mspr > 0 else ("Net Sell" if mspr < 0 else "Neutral")
        lines.append(f"  {month}: MSPR {sign}{mspr:.1f} ({signal}), 변동 {change:+.0f}건")
    return "\n".join(lines)


def finnhub_key_ready() -> bool:
    return bool(_api_key())
