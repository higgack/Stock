"""JP/CN/HK ê¸‰ë“±Â·ê¸‰ë½ TOP ???í•œê°€/?˜í•œê°€ ?ˆëŠ” ?œì¥(JP ?¶é™?¤å¹…Â·CN Â±10/20%)??
ë¯¸êµ­ì²˜ëŸ¼ ê·¸ëƒ¥ ?ìŠ¹/?˜ë½ TOP ë¡??œì‹œ(?¬ìš©??2026-06-13 'ì¤‘êµ­Â·?ì½©Â·?¼ë³¸?€ ë¯¸êµ­?°ë¼').
**?¤ì´ë²?worldstock ?°ì„ **(?œê?ëª…Â·ê±°?˜ë?ê¸ˆÂ·ì‹œì´?nativeÂ·1ì½œì”©) ???…ì¢…?€ yfinance
enrich(?¼í›„ë°©ì‹). ?¤ì´ë²??¤íŒ¨ ??yfinance ?„ì¢…ëª??¤ìº” ?´ë°±.
SWR(?œì¥-?¸ì? ? ì„ ??/ ?¤í…Œ??ë°±ê·¸?¼ìš´????/ ìºì‹œë¶€??building) ??**?™ê¸° ê³„ì‚°
????*. ?•ê·œ??1h / ??ë§ˆê° ???¬ìŠ¤ìº?0 (?¬ìš©??2026-06-13 'ëª¨ë‘ ?¥ì¤‘?ë§Œ 1h').
graceful.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("bot.intl_movers")

# market ??(ìºì‹œëª? ?íƒœëª? ?¼ë²¨). JP/CN_A/HK ?„ë? ?¤ì´ë²?ë¬´ë²„(?¬ìš©??2026-06-13).
_CFG = {
    "HK": ("hk_movers_v1.json", "hk_movers_status.json", "?ì½© ?„ì¢…ëª?),
    "JP": ("jp_movers_v1.json", "jp_movers_status.json", "?¼ë³¸(TSE)"),
    "CN_A": ("cn_movers_v1.json", "cn_movers_status.json", "ì¤‘êµ­ Aì£?),
}
# ? ì„ ?„ëŠ” ?œì¥-?¸ì?(finviz_client._session_fresh HK, ?¥ì¤‘ 1h / ??ë°?ë§ˆì?ë§?ë§ˆê°
# ?´í›„ ?¬ìŠ¤ìº?0) ??US movers ?€ ?™ì¼ ?•ì±…(?¬ìš©??2026-06-13 'ëª¨ë‘ ?¥ì¤‘?ë§Œ 1h').
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
        # ?¤ì´ë²?worldstock ?°ì„  (US ë¬´ë²„ ë¯¸ëŸ¬ ???œê?ëª…Â·ê±°?˜ë?ê¸ˆÂ·ì‹œì´?nativeÂ·1ì½œì”©,
        # ?¬ìš©??2026-06-13). ?…ì¢…(+?…ì¢…ë¶„í¬)?€ ?¤ì´ë²?ë¯¸ì œê³???yfinance enrich(?¼í›„ë°©ì‹).
        try:
            from bot.naver_ranking_client import fetch_intl_movers_naver
            nv = fetch_intl_movers_naver(market)
            if nv.get("up") or nv.get("down"):
                try:
                    # ?…ì¢…?€ yfinance enrich ? ì?(?¤ì´ë²?ë¬´ë²„ + ?¼í›„ ?…ì¢… ?•ì±…).
                    from bot.finviz_client import _fetch_industries
                    hits = [r["ticker"] for r in nv["up"] + nv["down"] if r.get("ticker")]
                    inds = _fetch_industries(hits)
                    for r in nv["up"] + nv["down"]:
                        if not r.get("ind"):
                            r["ind"] = inds.get(r["ticker"])
                except Exception as exc:
                    log.warning("intl movers ?…ì¢… enrich (%s): %s", market, exc)
                from bot.finviz_client import _cache_write
                _cache_write(_CFG[market][0], nv)
                _status_write(market, "done", up=len(nv["up"]),
                              down=len(nv["down"]), src="naver")
                return
        except Exception as exc:
            log.warning("naver intl movers (%s) ??yfinance ?´ë°±: %s", market, exc)
        # ?´ë°±: yfinance ?„ì¢…ëª??¤ìº” (?¤ì´ë²??¤íŒ¨ ??
        from bot.finviz_client import _compute_movers_from
        from bot.intl_universe import full_universe
        if market == "CN_A":
            try:
                from bot.akshare_client import list_cn_a_universe
                uni_map = list_cn_a_universe()
                uni = list(uni_map.keys()) if uni_map else []
            except Exception as exc:
                log.warning("CN_A full universe via akshare failed: %s", exc)
                uni = full_universe(market)
        else:
            uni = full_universe(market)
        if not uni:
            _status_write(market, "failed", detail="universe empty")
            return
        _status_write(market, "running", total=len(uni))
        out = _compute_movers_from(
            uni, {t: t for t in uni}, _CFG[market][0],
            f"{_CFG[market][2]}(yfinance ?¹ì¼ ?±ë½ë¥?", market)
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
    """HK ê¸‰ë“±/ê¸‰ë½ ??**?™ê¸° ê³„ì‚° ????*. ?œì¥-?¸ì? ? ì„ ???•ê·œ??1h / ??ë°?
    ë§ˆì?ë§?ë§ˆê° ?´í›„ ?¬ìŠ¤ìº?0) ì¦‰ì‹œ / ?¤í…Œ??ë°±ê·¸?¼ìš´????/ ìºì‹œë¶€??building.
    ?¤íŒ¨ 5ë¶?ë°±ì˜¤?„Â·ì§„?‰ì¤‘ 30ë¶?dedup. {up,down,ts,source,scanned,building,status}."""
    if market not in _CFG:
        return {"up": [], "down": [], "ts": "", "source": "", "building": False}
    from bot.finviz_client import (_CACHE_DIR, _MOVERS_INTRA_TTL, _cached,
                                   _session_fresh)
    cache = _CFG[market][0]
    stale = _cached(cache, ttl=86400)
    if stale is not None:
        try:
            mt = (_CACHE_DIR / cache).stat().st_mtime
        except OSError:
            mt = 0.0
        if _session_fresh(market, mt, _MOVERS_INTRA_TTL):   # ?¥ì¤‘ 1ë¶??¬ìš©??2026-06-14)
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
