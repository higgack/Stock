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


def _naver_quote_for(ticker: str) -> Optional[dict]:
    """네이버 실시간 시세 {price, pct, mcap, name} — KR=네이버 국내·US/JP/CN/HK=
    네이버 해외 (사용자 2026-06-15 '관심종목 시총·현재가 네이버, 대만 제외').
    한 콜로 현재가·시총·한글명을 모두 받는다. TW(.TW)·기타(EU 등)·실패는 None
    → 호출부가 yfinance 폴백. (네이버는 KR 국내·US/JP/CN/HK 해외만 커버)."""
    country = _detect_country(ticker)
    try:
        if country == "KR":
            from bot.naver_quote import fetch_kr_quote
            return fetch_kr_quote(ticker)
        if country in ("US", "JP", "CN", "HK"):
            from bot.world_quote import fetch_world_quote
            return fetch_world_quote(ticker)
    except Exception as exc:
        log.debug("favorites: naver quote failed for %s: %s", ticker, exc)
    return None


def _resolve_kr_name(ticker: str, fallback: str) -> str:
    """네이버 한글 종목명 (정적, add_favorite 1회 영속). TW·기타·실패는
    영문 fallback. _naver_quote_for 단일 소스(가격·시총·이름 같은 콜)."""
    q = _naver_quote_for(ticker)
    return (q.get("name") if q else None) or fallback


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

    _en_name = info.get("longName") or info.get("shortName") or ticker
    entry = {
        "ticker": ticker,
        "name": _en_name,
        "name_kr": _resolve_kr_name(ticker, _en_name),
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


_FAV_CACHE: "list | None" = None
_FAV_CACHE_TS: float = 0.0
_FAV_TTL = 180   # 관심종목 가격 캐시 3분 — 위젯 반복 로드(브라우저 /api/favorites)가
#                  매번 fast_info 를 버스트해 회로차단을 재트립하던 것 차단 (사용자
#                  2026-06-14 '야후 멈춤 너무 힘들어'). dashboard_server 단일 프로세스 캐시.


def get_favorites_with_prices() -> list[dict]:
    """Return favorites with current_price + refreshed estimates via yfinance.

    가격 캐시 3분 + fast_info 회로차단 게이트 — 야후 rate-limit 재트립 차단."""
    global _FAV_CACHE, _FAV_CACHE_TS
    import time as _time
    if _FAV_CACHE is not None and (_time.time() - _FAV_CACHE_TS) < _FAV_TTL:
        return _FAV_CACHE
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    favorites = _load()
    if not favorites:
        return favorites
    # fast_info 허용? 회로차단 쿨다운/정지 중이면 skip → .info/history 폴백
    try:
        from bot.finviz_client import fast_info_ok, yf_paused
        _fi_allowed = fast_info_ok() and not yf_paused()
    except Exception:
        _fi_allowed = True

    def _refresh(f: dict) -> None:
        # 네이버 실시간 시세 우선 (사용자 2026-06-15 '시총·현재가 네이버, 대만
        # 제외') — 현재가·시총·한글명을 한 콜로. TW·기타·실패는 None → yfinance.
        nq = _naver_quote_for(f["ticker"])
        naver_price = nq.get("price") if nq else None
        if not f.get("name_kr"):
            f["name_kr"] = ((nq.get("name") if nq else None)
                            or f.get("name") or f["ticker"])
        try:
            tk = yf.Ticker(f["ticker"])
            price = naver_price       # 네이버 있으면 fast_info 생략(야후 부하·글리치↓)
            info = None
            prev_close = None
            if price is None and _fi_allowed:
                try:
                    fi = tk.fast_info
                    price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                    prev_close = getattr(fi, "previous_close", None)
                except Exception as _exc:
                    try:    # rate-limit 이면 회로차단 발동(전 소비처 skip·30분 쿨다운)
                        from bot.finviz_client import (fast_info_trip,
                                                       is_rate_limit_error)
                        if is_rate_limit_error(_exc):
                            fast_info_trip("favorites")
                    except Exception:
                        pass
            if price is None:
                try:
                    info = tk.info or {}
                    price = info.get("regularMarketPrice") or info.get("currentPrice")
                    prev_close = prev_close or info.get("previousClose")
                except Exception:
                    info = {}
            # 가격 글리치 가드 — yfinance 폴백 경로만 (네이버 실시간은 클린이라
            # skip). KLAC 클래스: 직전 종가 대비 ±75% 초과면 직전 종가로 교체.
            glitched = False
            if naver_price is None:
                try:
                    from bot.price_sanity import quote_glitch_gap
                    if quote_glitch_gap(price, prev_close):
                        price = prev_close
                        glitched = True
                    # 2차 — price·prev 가 둘 다 같은 미조정 기준이면 1차가 장님
                    # (KLAC $2,411 vs $2,398 통과). 조정 일봉 종가와 교차.
                    if not glitched and price:
                        hist = tk.history(period="5d")
                        if hist is not None and len(hist) and "Close" in hist:
                            hc = float(hist["Close"].dropna().iloc[-1])
                            if hc > 0 and quote_glitch_gap(price, hc):
                                price = hc
                                prev_close = hc
                                glitched = True
                except Exception:
                    pass
            f["current_price"] = price

            if info is None:
                try:
                    info = tk.info or {}      # EPS/PER/주식수/실적일 — yfinance 유지
                except Exception:
                    info = {}

            # 시총: 네이버 우선 (KR=원 신뢰 / 해외=price×shares 단위 sanity 통과
            # 시만 — 네이버 해외 시총 단위 불확실, 원/달러 혼동 방지), 없으면
            # yfinance (글리치 시 직전종가×주식수 재산출).
            mcap = info.get("marketCap")
            if glitched and mcap:
                shares = info.get("sharesOutstanding")
                mcap = (shares * prev_close
                        if (shares and prev_close) else None)
            naver_mcap = nq.get("mcap") if nq else None
            if naver_mcap and naver_price:
                if _detect_country(f["ticker"]) == "KR":
                    mcap = naver_mcap          # 국내 marketValueFullRaw = 원, 신뢰
                else:
                    sh = info.get("sharesOutstanding")
                    implied = naver_price * sh if sh else 0
                    if implied and 0.5 <= naver_mcap / implied <= 2.0:
                        mcap = naver_mcap      # 해외: 단위 일치 확인 시만
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

    # name_kr 백필분만 깨끗이 영속 (정적이라 1회) — volatile 가격은 저장 안 함:
    # 디스크 재로드 → name_kr 복사 → save. 이후 cold load 부턴 재호출 0.
    try:
        _kr = {f["ticker"]: f.get("name_kr") for f in favorites if f.get("name_kr")}
        disk = _load()
        _chg = False
        for d in disk:
            if not d.get("name_kr") and _kr.get(d["ticker"]):
                d["name_kr"], _chg = _kr[d["ticker"]], True
        if _chg:
            _save(disk)
    except Exception:
        pass

    _FAV_CACHE = favorites
    _FAV_CACHE_TS = _time.time()
    return favorites
