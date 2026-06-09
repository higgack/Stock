"""Market favorites (관심종목) — CRUD for market.html watchlist.

Stores saved tickers with snapshot data (price at save time, estimates)
in a simple JSON file. No LLM, no recurring cost — yfinance only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.market_favorites")

_FAVORITES_FILE = Path.home() / ".tradingagents" / "market_favorites.json"


def _load() -> list[dict]:
    if _FAVORITES_FILE.exists():
        try:
            return json.loads(_FAVORITES_FILE.read_text("utf-8"))
        except Exception:
            return []
    return []


def _save(favorites: list[dict]) -> None:
    _FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FAVORITES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(favorites, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(_FAVORITES_FILE)


def _detect_country(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")):
        return "KR"
    if t.endswith(".T"):
        return "JP"
    if t.endswith((".TW", ".TWO")):
        return "TW"
    if t.endswith((".SS", ".SZ")):
        return "CN"
    if t.endswith(".HK"):
        return "HK"
    if t.endswith((".L", ".IL")):
        return "UK"
    if t.endswith((".DE", ".F")):
        return "DE"
    if t.endswith(".PA"):
        return "FR"
    return "US"


_CURRENCY_MAP = {
    "KRW": "₩", "JPY": "¥", "TWD": "NT$", "CNY": "¥",
    "HKD": "HK$", "GBP": "£", "EUR": "€", "USD": "$",
}


def add_favorite(ticker: str) -> Optional[dict]:
    """Fetch snapshot from yfinance and append to favorites. None on dupe/error."""
    import yfinance as yf

    favorites = _load()
    if any(f["ticker"].upper() == ticker.upper() for f in favorites):
        return None

    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as exc:
        log.warning("favorites: yfinance failed for %s: %s", ticker, exc)
        return None

    eps_est = info.get("forwardEps")
    per_val = info.get("forwardPE")
    per_is_trailing = False
    if per_val is None:
        per_val = info.get("trailingPE")
        if per_val is not None:
            per_is_trailing = True
    next_earn = None
    try:
        cal = tk.calendar
        if isinstance(cal, dict):
            earn_dates = cal.get("Earnings Date") or []
            if earn_dates:
                d = earn_dates[0]
                next_earn = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            if cal.get("Earnings Average") is not None:
                eps_est = cal["Earnings Average"]
    except Exception:
        pass

    price = info.get("regularMarketPrice") or info.get("currentPrice")
    currency = info.get("currency", "USD")
    now = datetime.now()

    entry = {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "country": _detect_country(ticker),
        "saved_date": now.strftime("%Y-%m-%d"),
        "saved_time": now.strftime("%H:%M"),
        "saved_price": price,
        "currency": currency,
        "currency_symbol": _CURRENCY_MAP.get(currency, "$"),
        "eps_estimate": eps_est,
        "per": per_val,
        "per_is_trailing": per_is_trailing,
        "next_earnings": next_earn,
    }

    favorites.append(entry)
    _save(favorites)
    return entry


def remove_favorite(ticker: str) -> bool:
    """Remove ticker from favorites. Returns True if removed."""
    favorites = _load()
    before = len(favorites)
    favorites = [f for f in favorites if f["ticker"].upper() != ticker.upper()]
    if len(favorites) < before:
        _save(favorites)
        return True
    return False


def get_favorites() -> list[dict]:
    """Return all saved favorites."""
    return _load()


def get_favorites_with_prices() -> list[dict]:
    """Return favorites with current_price + refreshed estimates via yfinance."""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    favorites = _load()
    if not favorites:
        return favorites

    def _refresh(f: dict) -> None:
        try:
            tk = yf.Ticker(f["ticker"])
            price = None
            info = None
            try:
                fi = tk.fast_info
                price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
            except Exception:
                pass
            if price is None:
                try:
                    info = tk.info or {}
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                except Exception:
                    info = {}
            f["current_price"] = price

            if info is None:
                try:
                    info = tk.info or {}
                except Exception:
                    info = {}

            fwd = info.get("forwardEps")
            trail = info.get("trailingEps")
            f["eps_estimate"] = fwd if fwd is not None else trail
            f["eps_is_actual"] = (fwd is None and trail is not None)

            fwd_pe = info.get("forwardPE")
            trail_pe = info.get("trailingPE")
            f["per"] = fwd_pe if fwd_pe is not None else trail_pe
            f["per_is_trailing"] = (fwd_pe is None and trail_pe is not None)

            try:
                cal = tk.calendar
                if isinstance(cal, dict):
                    if cal.get("Earnings Average") is not None and fwd is None:
                        f["eps_estimate"] = cal["Earnings Average"]
                        f["eps_is_actual"] = False
                    earn_dates = cal.get("Earnings Date") or []
                    if earn_dates:
                        d = earn_dates[0]
                        f["next_earnings"] = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
            except Exception:
                pass
        except Exception:
            f["current_price"] = None

    with ThreadPoolExecutor(max_workers=min(len(favorites), 8)) as pool:
        pool.map(_refresh, favorites)

    return favorites
