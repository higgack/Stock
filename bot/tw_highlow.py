"""대만 52주 신고가/신저가 — yfinance TW 유니버스 백그라운드 스캔 (사용자
2026-06-13, VM 10/10·3.1s 검증). US 신고저 패턴(finviz_client._compute_highlow_
from 재사용) + SWR(신선 30분 즉시 / 스테일 + 백그라운드 킥). **동기 계산 절대
안 함**(전종목 1y 주봉 = 수 분 → 페이지 hang 금지). 유니버스 = TWSE STOCK_DAY_
ALL 일반종목(이미 있음). 비용 = 1일 몇 회 백그라운드 스캔(캐시), 돈 0.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

_CACHE = "highlow_tw_v4.json"   # v4 (2026-06-14): 中文→영문 번역명 강제 재스캔
_STATUS = "tw_highlow_status.json"
# 신선도는 시장-인지(finviz_client._session_fresh TW, 장중 1h / 장 밖 마지막 마감
# 이후 재스캔 0) — 사용자 2026-06-13 '모두 장중에만 1h'.
_running = False
_lock = threading.Lock()


def _tw_universe() -> tuple[list[str], dict]:
    """TWSE 전종목 일반종목 → (['2330.TW', ...], {ticker: name}). graceful []."""
    try:
        from bot.twse_client import fetch_stock_day_all, _is_common_stock
        rows = fetch_stock_day_all().get("rows", [])
    except Exception as exc:
        log.warning("tw highlow universe error: %s", exc)
        return [], {}
    uni, names = [], {}
    for s in rows:
        c = str(s.get("code") or "")
        if _is_common_stock(c):
            tk = f"{c}.TW"
            uni.append(tk)
            names[tk] = s.get("name") or c
    return uni, names


def _status_write(state: str, **kw) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_STATUS, {"state": state, "ts": time.time(), **kw})
    except Exception:
        pass


def tw_highlow_status() -> dict:
    try:
        from bot.finviz_client import _cached
        return _cached(_STATUS, ttl=86400) or {}
    except Exception:
        return {}


def _compute_tw_highlow() -> None:
    global _running
    try:
        from bot.finviz_client import _compute_highlow_from
        uni, names = _tw_universe()
        if not uni:
            _status_write("failed", detail="universe empty")
            return
        _status_write("running", total=len(uni))
        out = _compute_highlow_from(
            uni, names, _CACHE,
            "TWSE 전종목 산출(yfinance · 당일 52주 고저 갱신)", "TW")
        _status_write("done", high=len(out.get("high", [])),
                      low=len(out.get("low", [])))
    except Exception as exc:
        log.warning("tw highlow compute: %s", exc)
        _status_write("failed", detail=str(exc)[:80])
    finally:
        with _lock:
            _running = False


def _kick_tw_highlow() -> None:
    global _running
    with _lock:
        if _running:
            return
        _running = True
    threading.Thread(target=_compute_tw_highlow, daemon=True,
                     name="tw-highlow").start()


def fetch_tw_highlow() -> dict:
    """TW 52주 신고가/신저가 — **동기 계산 안 함**. 시장-인지 신선도(정규장 2h /
    장 밖 마지막 마감 이후 재스캔 0) 즉시 / 스테일 + 백그라운드 킥 / 캐시 부재 시
    building. 실패 5분 백오프·진행중 30분 dedup. {high,low,ts,source,building,status}."""
    from bot.finviz_client import _CACHE_DIR, _HL_INTRA_TTL, _cached, _session_fresh
    stale = _cached(_CACHE, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / _CACHE).stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh("TW", mt, _HL_INTRA_TTL):
            return stale
    st = tw_highlow_status()
    age = time.time() - (st.get("ts") or 0)
    if st.get("state") == "failed" and age < 300:
        pass        # 5분 백오프
    elif st.get("state") == "running" and age < 1800:
        pass        # 이미 산출 중
    else:
        _kick_tw_highlow()
    if stale is not None:
        return {**stale, "building": st.get("state") == "running"}
    return {"high": [], "low": [], "ts": "", "source": "",
            "building": True, "status": st}
