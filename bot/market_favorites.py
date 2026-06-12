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


def _future_or_none(d_str):
    """다음 실적일이 오늘 이전(stale)이면 None — 빈칸 표시용."""
    try:
        if d_str and str(d_str)[:10] >= datetime.now().strftime("%Y-%m-%d"):
            return str(d_str)[:10]
    except Exception:
        pass
    return None


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
    lfy = info.get("lastFiscalYearEnd")
    fy_label = None
    if lfy and isinstance(lfy, (int, float)):
        fy_label = f"FY{datetime.fromtimestamp(lfy).year % 100:02d}"
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
        "market_cap": info.get("marketCap"),
        "eps_estimate": eps_est,
        "eps_is_actual": (info.get("forwardEps") is None
                          and info.get("trailingEps") is not None),
        "eps_fy_label": fy_label if (info.get("forwardEps") is None
                                     and info.get("trailingEps") is not None) else None,
        "eps_negative": (eps_est is not None and eps_est < 0),
        "per": per_val,
        "per_is_trailing": per_is_trailing,
        # 과거 날짜(yfinance KR calendar stale)는 빈칸 — 사용자 2026-06-11.
        "next_earnings": _future_or_none(next_earn),
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


def reorder_favorite(ticker: str, direction: str) -> bool:
    """Move a ticker up/down one position in the saved order. Persists.

    direction: 'up' (앞으로) | 'down' (뒤로). Returns True if order changed."""
    favorites = _load()
    idx = next((i for i, f in enumerate(favorites)
                if f.get("ticker", "").upper() == ticker.upper()), None)
    if idx is None:
        return False
    if direction == "up" and idx > 0:
        favorites[idx - 1], favorites[idx] = favorites[idx], favorites[idx - 1]
    elif direction == "down" and idx < len(favorites) - 1:
        favorites[idx + 1], favorites[idx] = favorites[idx], favorites[idx + 1]
    else:
        return False
    _save(favorites)
    return True


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
            prev_close = None
            try:
                fi = tk.fast_info
                price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                prev_close = getattr(fi, "previous_close", None)
            except Exception:
                pass
            if price is None:
                try:
                    info = tk.info or {}
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                    prev_close = prev_close or info.get("previousClose")
                except Exception:
                    info = {}
            # 가격 글리치 가드 (KLAC 클래스 — yfinance 분할 미조정 last_price):
            # 직전 종가 대비 ±75% 초과면 직전 종가로 교체 (교체 우선 정책).
            glitched = False
            try:
                from bot.price_sanity import quote_glitch_gap
                if quote_glitch_gap(price, prev_close):
                    price = prev_close
                    glitched = True
            except Exception:
                pass
            f["current_price"] = price

            if info is None:
                try:
                    info = tk.info or {}
                except Exception:
                    info = {}

            mcap = info.get("marketCap")
            if glitched and mcap:
                # 시총도 같은 글리치 가격 기반 — 주식수×직전종가로 재산출,
                # 주식수 부재 시 표기 생략(None)이 $3.15T 노출보다 낫다.
                shares = info.get("sharesOutstanding")
                mcap = (shares * prev_close
                        if (shares and prev_close) else None)
            f["market_cap"] = mcap

            fwd = info.get("forwardEps")
            trail = info.get("trailingEps")
            f["eps_estimate"] = fwd if fwd is not None else trail
            f["eps_is_actual"] = (fwd is None and trail is not None)

            fwd_pe = info.get("forwardPE")
            trail_pe = info.get("trailingPE")
            f["per"] = fwd_pe if fwd_pe is not None else trail_pe
            f["per_is_trailing"] = (fwd_pe is None and trail_pe is not None)

            lfy = info.get("lastFiscalYearEnd")
            fy_label = None
            if lfy and isinstance(lfy, (int, float)):
                fy_label = f"FY{datetime.fromtimestamp(lfy).year % 100:02d}"
            f["eps_fy_label"] = fy_label if f.get("eps_is_actual") else None
            f["eps_negative"] = (f.get("eps_estimate") is not None
                                 and f["eps_estimate"] < 0)

            try:
                cal = tk.calendar
                if isinstance(cal, dict):
                    if cal.get("Earnings Average") is not None and fwd is None:
                        f["eps_estimate"] = cal["Earnings Average"]
                        f["eps_is_actual"] = False
                    earn_dates = cal.get("Earnings Date") or []
                    if earn_dates:
                        d = earn_dates[0]
                        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                        f["next_earnings"] = _future_or_none(ds)
            except Exception:
                pass
            # 저장돼 있던 옛 날짜가 이미 과거면 빈칸 (yfinance 가 새 일정을
            # 안 주는 KR 케이스 — SK hynix 2026-04-22 사용자 2026-06-11).
            if f.get("next_earnings"):
                f["next_earnings"] = _future_or_none(f["next_earnings"])
            # calendar 부재/과거 → 실적탭과 동일 소스(earnings_dates, 미래
            # 추정 포함)로 폴백 — 하이닉스 2026-07-29 케이스(사용자 2026-06-11).
            if not f.get("next_earnings"):
                try:
                    ed = tk.earnings_dates
                    if ed is not None and len(ed.index):
                        today_s = datetime.now().strftime("%Y-%m-%d")
                        fut = sorted(str(ix)[:10] for ix in ed.index
                                     if str(ix)[:10] >= today_s)
                        if fut:
                            f["next_earnings"] = fut[0]
                except Exception:
                    pass
        except Exception:
            f["current_price"] = None

    with ThreadPoolExecutor(max_workers=min(len(favorites), 8)) as pool:
        pool.map(_refresh, favorites)

    return favorites
