"""일본 ストップ高/安(상한가/하한가) — TSE 制限値幅(price_sanity.jp_price_limit,
가격대별 tiered) 도달 종목 (사용자 2026-06-13 'JP 상하한가'). JP 전종목
(intl_universe.full_universe) → yfinance 일봉 당일 변동 vs 제한폭. 결정적(공개
표·스크래핑 불요). SWR(신선 6h / 스테일+백그라운드 킥 / 캐시부재 building) —
**동기 계산 안 함**. 무거운 전종목 스캔이라 6h 캐시(EOD 충분). graceful.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("bot.jp_stop")

_CACHE = "jp_stop_v1.json"
_STATUS = "jp_stop_status.json"
_TTL = 6 * 3600
_running = {"x": False}
_lock = threading.Lock()


def _status_write(state: str, **kw) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_STATUS, {"state": state, "ts": time.time(), **kw})
    except Exception:
        pass


def jp_stop_status() -> dict:
    try:
        from bot.finviz_client import _cached
        return _cached(_STATUS, ttl=86400) or {}
    except Exception:
        return {}


def _compute() -> None:
    try:
        from bot.finviz_client import _compute_jp_stop
        from bot.intl_universe import full_universe
        uni = full_universe("JP")
        if not uni:
            _status_write("failed", detail="universe empty")
            return
        _status_write("running", total=len(uni))
        out = _compute_jp_stop(uni, {t: t for t in uni}, _CACHE,
                               "일본 전종목(TSE 制限値幅)")
        _status_write("done", upper=len(out.get("upper", [])),
                      lower=len(out.get("lower", [])))
    except Exception as exc:
        log.warning("jp_stop compute: %s", exc)
        _status_write("failed", detail=str(exc)[:80])
    finally:
        with _lock:
            _running["x"] = False


def _kick() -> None:
    with _lock:
        if _running["x"]:
            return
        _running["x"] = True
    threading.Thread(target=_compute, daemon=True, name="jp-stop").start()


def fetch_jp_stop() -> dict:
    """일본 상한가/하한가(ストップ高/安) — **동기 계산 안 함**. 신선 6h 즉시 /
    스테일+백그라운드 킥 / 캐시부재 building. 실패 5분 백오프·진행중 30분 dedup.
    {upper, lower, ts, source, scanned, building, status}."""
    from bot.finviz_client import _cached
    fresh = _cached(_CACHE, ttl=_TTL)
    if fresh is not None:
        return fresh
    stale = _cached(_CACHE, ttl=86400)
    st = jp_stop_status()
    age = time.time() - (st.get("ts") or 0)
    if st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < 1800:
        pass
    else:
        _kick()
    if stale is not None:
        return {**stale, "building": st.get("state") == "running"}
    return {"upper": [], "lower": [], "ts": "", "source": "",
            "building": True, "status": st}
