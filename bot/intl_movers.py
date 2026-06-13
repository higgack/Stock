"""HK 급등/급락 TOP — 홍콩은 가격제한이 없는 시장(US 동일)이라 상한가/하한가
대신 급등/급락(사용자 2026-06-13 '홍콩도 미국처럼'). HKEX 전종목
(intl_universe.full_universe) → yfinance 일봉 당일 등락률 상·하위 30.
SWR(시장-인지 신선도 / 스테일+백그라운드 킥 / 캐시부재 building) — **동기 계산
안 함**. 정규장 30분(무버는 장중 변동 큼) / 장 마감 후 재스캔 0. graceful.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("bot.intl_movers")

# market → (캐시명, 상태명, 라벨)
_CFG = {"HK": ("hk_movers_v1.json", "hk_movers_status.json", "홍콩 전종목")}
# 신선도는 시장-인지(finviz_client._session_fresh HK, 장중 30분 / 장 밖 마지막 마감
# 이후 재스캔 0) — US movers 와 동일 정책(무버는 장중 변동 큼, 사용자 2026-06-13).
_running: dict[str, bool] = {}
_lock = threading.Lock()


def _status_write(market: str, state: str, **kw) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_CFG[market][1], {"state": state, "ts": time.time(), **kw})
    except Exception:
        pass


def intl_movers_status(market: str) -> dict:
    try:
        from bot.finviz_client import _cached
        return _cached(_CFG[market][1], ttl=86400) or {}
    except Exception:
        return {}


def _compute(market: str) -> None:
    try:
        from bot.finviz_client import _compute_movers_from
        from bot.intl_universe import full_universe
        uni = full_universe(market)
        if not uni:
            _status_write(market, "failed", detail="universe empty")
            return
        _status_write(market, "running", total=len(uni))
        out = _compute_movers_from(
            uni, {t: t for t in uni}, _CFG[market][0],
            f"{_CFG[market][2]}(yfinance 당일 등락률)", market)
        _status_write(market, "done", up=len(out.get("up", [])),
                      down=len(out.get("down", [])))
    except Exception as exc:
        log.warning("intl movers compute (%s): %s", market, exc)
        _status_write(market, "failed", detail=str(exc)[:80])
    finally:
        with _lock:
            _running[market] = False


def _kick(market: str) -> None:
    with _lock:
        if _running.get(market):
            return
        _running[market] = True
    threading.Thread(target=_compute, args=(market,), daemon=True,
                     name=f"intl-movers-{market}").start()


def fetch_intl_movers(market: str) -> dict:
    """HK 급등/급락 — **동기 계산 안 함**. 시장-인지 신선도(정규장 30분 / 장 밖
    마지막 마감 이후 재스캔 0) 즉시 / 스테일+백그라운드 킥 / 캐시부재 building.
    실패 5분 백오프·진행중 30분 dedup. {up,down,ts,source,scanned,building,status}."""
    if market not in _CFG:
        return {"up": [], "down": [], "ts": "", "source": "", "building": False}
    from bot.finviz_client import (_CACHE_DIR, _FALLBACK_TTL_SEC, _cached,
                                   _session_fresh)
    cache = _CFG[market][0]
    stale = _cached(cache, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / cache).stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh(market, mt, _FALLBACK_TTL_SEC):
            return stale
    st = intl_movers_status(market)
    age = time.time() - (st.get("ts") or 0)
    if st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < 1800:
        pass
    else:
        _kick(market)
    if stale is not None:
        return {**stale, "building": st.get("state") == "running"}
    return {"up": [], "down": [], "ts": "", "source": "",
            "building": True, "status": st}
