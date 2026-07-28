"""Market favorites (관심종목) — CRUD for market.html watchlist.

Stores saved tickers with snapshot data (price at save time, estimates)
in a simple JSON file. No LLM, no recurring cost — yfinance only.
"""

from __future__ import annotations

import json
import logging
import threading as _threading
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
    # 추가/삭제/순서변경 후 SWR 가격 캐시 무효화 — 안 하면 get_favorites_with_prices
    # 가 옛 목록(삭제분 포함)을 stale 로 계속 줘서 '휴지통/추가가 안 먹는' 것처럼
    # 보임(사용자 2026-06-16 '휴지통 작동 안 함'). 다음 조회가 _load()(갱신 디스크)
    # 즉시 반영 + 백그라운드 가격 재계산. _FAV_CACHE 는 아래에서 정의(런타임 global).
    global _FAV_CACHE
    _FAV_CACHE = None


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
    """네이버 한글 종목명 (정적, add_favorite 1회 영속). KR/US/JP/CN/HK=네이버.
    TW(.TW/.TWO)=네이버 미커버 → 대만 신고가 페이지와 **동일한** chart_translate
    번역(사용자 2026-06-15 '대만도 대만신고가처럼'). names_kr.json 캐시가 티커
    기준이라 신고가가 이미 채운 한글명을 그대로 공유(₩0·동일 표기). 그 외(EU
    등)·실패는 영문 fallback."""
    q = _naver_quote_for(ticker)
    if q and q.get("name"):
        return q["name"]
    if _detect_country(ticker) == "TW":
        try:
            from bot.chart_translate import translate_names_kr
            kr = translate_names_kr([(ticker, fallback)])
            if kr.get(ticker):
                return kr[ticker]
        except Exception as exc:
            log.debug("favorites: TW name translate failed for %s: %s", ticker, exc)
    return fallback


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
    """Move a ticker in the saved order. Persists.

    direction: 'up'/'down' (한 칸) | 'top'/'bottom' (맨 위/아래 — 사용자 2026-06-17
    '하나씩 올리면 끝까지 한참'). Returns True if order changed."""
    favorites = _load()
    idx = next((i for i, f in enumerate(favorites)
                if f.get("ticker", "").upper() == ticker.upper()), None)
    if idx is None:
        return False
    if direction == "up" and idx > 0:
        favorites[idx - 1], favorites[idx] = favorites[idx], favorites[idx - 1]
    elif direction == "down" and idx < len(favorites) - 1:
        favorites[idx + 1], favorites[idx] = favorites[idx], favorites[idx + 1]
    elif direction == "top" and idx > 0:
        favorites.insert(0, favorites.pop(idx))      # 맨 위로
    elif direction == "bottom" and idx < len(favorites) - 1:
        favorites.append(favorites.pop(idx))         # 맨 아래로
    else:
        return False
    _save(favorites)
    return True


_FAV_CACHE: "list | None" = None
_FAV_CACHE_TS: float = 0.0
_FAV_TTL = 180   # 관심종목 가격 캐시 3분 — 위젯 반복 로드(브라우저 /api/favorites)가
#                  매번 fast_info 를 버스트해 회로차단을 재트립하던 것 차단 (사용자
#                  2026-06-14 '야후 멈춤 너무 힘들어'). dashboard_server 단일 프로세스 캐시.
_FAV_REFRESHING = False
_FAV_LOCK = _threading.Lock()


def get_favorites_with_prices() -> list[dict]:
    """관심종목 + 현재가/추정치 — **렌더-세이프 SWR (사용자 2026-06-16 '오래걸려')**.

    엔드포인트(/api/favorites)가 종목당 yfinance(tk.info EPS/PER·fast_info·history)를
    동기로 때려 '불러오는 중…'이 오래 걸리던 것 해소: 신선 캐시 즉시 / 스테일·콜드 시
    **백그라운드 daemon 갱신 + 즉시 반환**(스테일 있으면 스테일, 없으면 이름만 —
    위젯 폴이 곧 가격 채움). 가격 캐시 3분 + fast_info 회로차단 게이트 유지."""
    global _FAV_CACHE, _FAV_CACHE_TS
    import time as _time
    now = _time.time()
    if _FAV_CACHE is not None and (now - _FAV_CACHE_TS) < _FAV_TTL:
        return _FAV_CACHE
    _kick_fav_refresh()              # 스테일/콜드 → 백그라운드 full 갱신(비차단)
    if _FAV_CACHE is not None:
        return _FAV_CACHE           # 스테일 즉시(곧 daemon 이 갱신)
    return _load()                  # 첫 로드 — 이름만(가격은 위젯 다음 폴에 채워짐)


def _kick_fav_refresh() -> None:
    """백그라운드 daemon — full yfinance 갱신(_compute). dedup + daemon(종료 블로킹 0)."""
    global _FAV_REFRESHING
    with _FAV_LOCK:
        if _FAV_REFRESHING:
            return
        _FAV_REFRESHING = True

    def _run():
        global _FAV_REFRESHING
        try:
            _compute_favorites_with_prices()
        except Exception as exc:
            log.warning("favorites refresh: %s", exc)
        finally:
            with _FAV_LOCK:
                _FAV_REFRESHING = False

    _threading.Thread(target=_run, daemon=True, name="fav-refresh").start()


def _compute_favorites_with_prices() -> list[dict]:
    """관심종목 full 갱신(yfinance per-ticker) — 백그라운드 daemon 전용. _FAV_CACHE 적재."""
    global _FAV_CACHE, _FAV_CACHE_TS
    import time as _time
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
        # name_kr 미해결(부재 OR 영문 fallback==name)이면 (재)해석 — #419 이전
        # 영문으로 영속된 TW / Naver 일시실패분을 자가치유(if-not-name_kr 게이트가
        # 영문 영속분을 영구 고착시키던 것 해소). 진짜 한글명(≠name)은 skip.
        # translate_names_kr·Naver 둘 다 캐시라 재호출 싸다.
        _cur_kr = f.get("name_kr")
        if not _cur_kr or _cur_kr == f.get("name"):
            _new_kr = ((nq.get("name") if nq else None)
                       or _resolve_kr_name(f["ticker"], f.get("name") or f["ticker"]))
            if _new_kr:
                f["name_kr"] = _new_kr
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
        by_t = {f["ticker"]: f for f in favorites}
        disk = _load()
        _chg = False
        for d in disk:
            f = by_t.get(d["ticker"])
            nk = f.get("name_kr") if f else None
            if not nk:
                continue
            # 한글명(영문 name 과 다름)이면 갱신(영문→한글 치유 포함); 디스크가
            # 비어있으면 영문 fallback 이라도 채움. 기존 한글명을 영문으로 안 덮음.
            if (nk != f.get("name") and nk != d.get("name_kr")) or not d.get("name_kr"):
                d["name_kr"], _chg = nk, True
        if _chg:
            _save(disk)
    except Exception:
        pass

    _FAV_CACHE = favorites
    _FAV_CACHE_TS = _time.time()
    return favorites
