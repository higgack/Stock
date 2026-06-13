"""JP/CN/HK 52주 신고가/신저가 — TW 패턴 일반화 (사용자 2026-06-13 '다른 나라도
모두 적용'). 유니버스 = bot.market 의 _*_INDUSTRY_PEERS curated 주요종목(시장당
~50-100, 이미 검증된 리스트). finviz_client._compute_highlow_from(US 신고저
스캐너) 재사용 + SWR(신선 30분/스테일+백그라운드 킥). **동기 계산 안 함**.

TW 는 별도(twse STOCK_DAY_ALL ~1000 전종목). JP/CN/HK 는 peer 주요종목 universe
— 풀시장은 인덱스 구성종목 소스 추가 시 확장(향후). VM yfinance 검증(10/10).
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# market → (peer 맵 속성명, 캐시명, 상태명, 위젯 라벨, native 접미사 필터)
# 접미사 필터 = peer 맵에 섞인 해외 비교군 제외(KR 반도체에 TSM/NVDA 등).
# JP/CN/HK 는 맵이 이미 native-only 라 필터가 no-op(검증됨).
_CFG = {
    "JP": ("_JP_INDUSTRY_PEERS", "highlow_jp_v1.json",
           "jp_highlow_status.json", "일본 주요종목", (".T",)),
    "CN_A": ("_CN_A_INDUSTRY_PEERS", "highlow_cn_v1.json",
             "cn_highlow_status.json", "중국 A주 주요종목", (".SS", ".SZ")),
    "HK": ("_HK_INDUSTRY_PEERS", "highlow_hk_v1.json",
           "hk_highlow_status.json", "홍콩 주요종목", (".HK",)),
    # KR — 사용자 2026-06-13 '한국도 신고가신저가'. KIS 신고저 순위 엔드포인트
    # (1콜·더 쌈)는 VM 검증 대기 → 우선 검증된 yfinance 유니버스 스캔으로.
    "KR": ("_KR_INDUSTRY_PEERS", "highlow_kr_v1.json",
           "kr_highlow_status.json", "한국 주요종목", (".KS", ".KQ")),
}
_TTL = 30 * 60
_running: dict[str, bool] = {}
_lock = threading.Lock()


def _universe(market: str) -> tuple[list[str], dict]:
    """peer 맵의 unique 티커(주요종목). names 는 ticker 기본(맵에 명칭 없음)."""
    cfg = _CFG.get(market)
    if not cfg:
        return [], {}
    try:
        from bot import market as mkt
        peers = getattr(mkt, cfg[0], {}) or {}
    except Exception as exc:
        log.warning("intl highlow universe error (%s): %s", market, exc)
        return [], {}
    suffix = cfg[4] if len(cfg) > 4 else None   # native 시장 접미사(해외 비교군 제외)
    seen, uni, names = set(), [], {}
    for vals in peers.values():
        for x in (vals if isinstance(vals, (list, tuple)) else [vals]):
            t = str(x[0] if isinstance(x, (list, tuple)) else x).strip()
            if not t or t in seen:
                continue
            if suffix and not t.endswith(suffix):
                continue
            seen.add(t)
            uni.append(t)
            names[t] = t
    return uni, names


def _status_write(market: str, state: str, **kw) -> None:
    try:
        from bot.finviz_client import _cache_write
        _cache_write(_CFG[market][2], {"state": state, "ts": time.time(), **kw})
    except Exception:
        pass


def intl_highlow_status(market: str) -> dict:
    try:
        from bot.finviz_client import _cached
        return _cached(_CFG[market][2], ttl=86400) or {}
    except Exception:
        return {}


def _compute(market: str) -> None:
    try:
        from bot.finviz_client import _compute_highlow_from
        uni, names = _universe(market)
        if not uni:
            _status_write(market, "failed", detail="universe empty")
            return
        _status_write(market, "running", total=len(uni))
        out = _compute_highlow_from(
            uni, names, _CFG[market][1],
            f"{_CFG[market][3]} 산출(yfinance · 52주 고저 1% 근접)", market)
        _status_write(market, "done", high=len(out.get("high", [])),
                      low=len(out.get("low", [])))
    except Exception as exc:
        log.warning("intl highlow compute (%s): %s", market, exc)
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
                     name=f"intl-highlow-{market}").start()


def fetch_intl_highlow(market: str) -> dict:
    """JP/CN_A/HK 52주 신고가/신저가 — **동기 계산 안 함**. 신선 30분 즉시 /
    스테일+백그라운드 킥 / 캐시부재 building. 실패 5분 백오프·진행중 30분
    dedup (TW/US 미러). {high,low,ts,source,building,status}."""
    if market not in _CFG:
        return {"high": [], "low": [], "ts": "", "source": "", "building": False}
    from bot.finviz_client import _cached
    cache = _CFG[market][1]
    fresh = _cached(cache, ttl=_TTL)
    if fresh is not None:
        return fresh
    stale = _cached(cache, ttl=86400)
    st = intl_highlow_status(market)
    age = time.time() - (st.get("ts") or 0)
    if st.get("state") == "failed" and age < 300:
        pass
    elif st.get("state") == "running" and age < 1800:
        pass
    else:
        _kick(market)
    if stale is not None:
        return {**stale, "building": st.get("state") == "running"}
    return {"high": [], "low": [], "ts": "", "source": "",
            "building": True, "status": st}
