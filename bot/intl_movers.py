"""JP/CN/HK 급등·급락 TOP — 미국처럼 그냥 상승/하락 TOP 로 표시(사용자 2026-06-13 '중국·홍콩·일본은 미국따라').

네이버 worldstock 우선(종목명·거래대금·시총 native·1콜씩) 후 종목별 yfinance enrich.
네이버가 실패하면 yfinance 종목 스캔으로 폴백.
SWR(장-밖 신선도 / 스테일+백그라운드 / 캐시부재 building) — **동기 계산 안 함**.
신선도: 장중 1h / 장마감 후 재스캔 0 (사용자 2026-06-13 '모두 장중에만 1h').
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("bot.intl_movers")

# market → (캐시명, 상태명, 라벨). JP/CN_A/HK 전용 급등락.
_CFG = {
    "HK": ("hk_movers_v1.json", "hk_movers_status.json", "홍콩 주요종목"),
    "JP": ("jp_movers_v1.json", "jp_movers_status.json", "일본(TSE)"),
    "CN_A": ("cn_movers_v1.json", "cn_movers_status.json", "중국 A주"),
}
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
        # 네이버 worldstock 우선 → 성공 시 yfinance 업종 enrich.
        try:
            from bot.naver_ranking_client import fetch_intl_movers_naver
            nv = fetch_intl_movers_naver(market)
            if nv.get("up") or nv.get("down"):
                try:
                    from bot.finviz_client import _fetch_industries
                    hits = [r["ticker"] for r in nv["up"] + nv["down"] if r.get("ticker")]
                    inds = _fetch_industries(hits)
                    for r in nv["up"] + nv["down"]:
                        if not r.get("ind"):
                            r["ind"] = inds.get(r["ticker"])
                except Exception as exc:
                    log.warning("intl movers enrich (%s): %s", market, exc)
                from bot.finviz_client import _cache_write
                _cache_write(_CFG[market][0], nv)
                _status_write(market, "done", up=len(nv["up"]), down=len(nv["down"]), src="naver")
                return
        except Exception as exc:
            log.warning("naver intl movers (%s) → yfinance fallback: %s", market, exc)

        # fallback: yfinance ??? ??
        from bot.finviz_client import _compute_movers_from
        from bot.intl_universe import full_universe

        uni_map: dict = {}
        if market == "CN_A":
            # CN_A universe = AKShare A-share listing. full_universe() only
            # supports JP/HK; the old code unconditionally overwrote uni with
            # full_universe(market) right after, so CN_A always ended up empty
            # and the board reported "universe empty". Synthetic CN_A_####
            # placeholder tickers were also dropped (not real symbols).
            try:
                from bot.akshare_client import list_cn_a_universe
                uni_map = list_cn_a_universe() or {}
                uni = list(uni_map.keys())
            except Exception as exc:
                log.warning("CN_A universe via akshare failed: %s", exc)
                uni = []
        else:
            uni = full_universe(market)

        if not uni:
            log.warning("intl movers universe empty (%s) — fallback to broad scan", market)
            _status_write(market, "failed", detail="universe empty")
            return

        _status_write(market, "running", total=len(uni))
        out = _compute_movers_from(uni, {t: (uni_map.get(t) or t) for t in uni}, _CFG[market][0], f"{_CFG[market][2]}(yfinance 일일 등락)", market)
        _status_write(market, "done", up=len(out.get("up", [])), down=len(out.get("down", [])))
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
    threading.Thread(target=_compute, args=(market,), daemon=True, name=f"intl-movers-{market}").start()


def fetch_intl_movers(market: str) -> dict:
    if market not in _CFG:
        return {"up": [], "down": [], "ts": "", "source": "", "building": False}
    from bot.finviz_client import (_CACHE_DIR, _MOVERS_INTRA_TTL, _cached, _session_fresh)
    cache = _CFG[market][0]
    stale = _cached(cache, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / cache).stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh(market, mt, _MOVERS_INTRA_TTL):
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
    return {"up": [], "down": [], "ts": "", "source": "", "building": True, "status": st}