"""CFTC COT(Commitments of Traders) 역발상 포지셔닝 게이트 (2026-07-26,
선물전용 예외).

claude-trading-skills 리뷰 갭 — 대형투기자(non-commercial) 순포지션이
과거 대비 극단적으로 쏠렸을 때(crowded positioning) 역발상 신호를 낸다
(Larry Williams 류 고전 COT Index 방법론: 트레일링 구간 대비 현재 순포지션
백분위, ≥80=과열매수(역발상 매도경계) ≤20=과열매도(역발상 매수경계)).
유료 FMP COT 엔드포인트 대신 무료 CFTC publicreporting Socrata 오픈데이터
사용.

⚠️ 시장특정 예외(사용자 승인 — "13F/COT 포함 9개 전체" 명시적 확인):
COT 보고서는 미국 선물시장(CFTC 규제) 참가자 포지션 공시 제도로 KR/JP
개별주식 대응 개념이 없음. 다만 S&P500/나스닥 E-mini 등 지수선물 COT 는
전반적 주식시장 심리 게이트로 유효해 bot/market_timing.py 매크로 레짐에
참고 신호로 병합(개별종목 게이트 아님 — market gate 코드 없음, 매크로
보드의 선택적 부가 신호).

CFTC dataset_id(Socrata 4x4 리소스코드) 를 하드코딩하지 않고 Socrata 표준
카탈로그 검색 API(api.us.socrata.com — CFTC 전용이 아닌 범용 플랫폼
기능)로 동적 조회 — 숫자/코드 암기 위험 회피(FRED release_id·SEC CIK 와
동일 '추측보고 금지' 패턴).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.cot_gate")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "cot"
_CATALOG_URL = "https://api.us.socrata.com/api/catalog/v1"
_CFTC_DOMAIN = "publicreporting.cftc.gov"
_TIMEOUT = 15

# 선물전용 시장 카탈로그 — CFTC "market_and_exchange_names" 필드 값(공식
# 표기 그대로). 지수선물(S&P500/나스닥) 은 market_timing.py 매크로레짐의
# 부가 신호용, 금/원유/통화는 향후 확장 여지(동일 구조).
_MARKETS = {
    "SP500": "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
    "NASDAQ": "NASDAQ-100 STOCK INDEX (MINI) - CHICAGO MERCANTILE EXCHANGE",
}


def _cache_path(key: str) -> Path:
    import re
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", key)
    return _CACHE_DIR / f"{safe}.json"


def _load_cache(key: str, max_age_h: float):
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        if (time.time() - p.stat().st_mtime) / 3600 < max_age_h:
            import json
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("cot_gate: cache read failed for %s: %s", key, exc)
    return None


def _save_cache(key: str, payload) -> None:
    try:
        import json
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log.debug("cot_gate: cache write failed for %s: %s", key, exc)


def find_cot_dataset_id(query: str = "Commitments of Traders Futures Only Legacy",
                        ttl_hours: float = 24 * 7) -> Optional[str]:
    """Socrata 카탈로그 검색(publicreporting.cftc.gov 도메인 한정)으로 COT
    데이터셋 리소스 ID(4x4) 조회. 7일 캐시. 실패/매치없음 → None(graceful)."""
    key = f"dataset_{query}"
    cached = _load_cache(key, ttl_hours)
    if cached is not None:
        return cached
    try:
        resp = requests.get(_CATALOG_URL, params={
            "domains": _CFTC_DOMAIN, "q": query, "limit": 1,
        }, timeout=_TIMEOUT)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return None
        rid = (results[0].get("resource") or {}).get("id")
        if rid:
            _save_cache(key, rid)
        return rid
    except Exception as exc:
        log.warning("cot_gate: catalog search failed for %r: %s", query, exc)
        return None


def fetch_cot_net_positions(market_key: str, weeks: int = 200,
                            dataset_id: Optional[str] = None) -> list:
    """market_key(_MARKETS) 의 최근 weeks 건 대형투기자(non-commercial) 순
    포지션 시계열 [(date, net_position), ...] 오름차순. dataset_id 미지정 시
    find_cot_dataset_id() 로 동적 조회. 키/네트워크/시장매치 실패 → []."""
    market_name = _MARKETS.get(market_key.upper())
    if not market_name:
        return []
    rid = dataset_id or find_cot_dataset_id()
    if not rid:
        return []
    key = f"hist_{market_key}_{rid}"
    cached = _load_cache(key, ttl_hours=12)
    if cached is not None:
        return [tuple(x) for x in cached]
    url = f"https://{_CFTC_DOMAIN}/resource/{rid}.json"
    try:
        resp = requests.get(url, params={
            "market_and_exchange_names": market_name,
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": weeks,
        }, timeout=_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json() or []
    except Exception as exc:
        log.warning("cot_gate: history fetch failed for %s: %s", market_key, exc)
        return []
    out = []
    for row in rows:
        d = row.get("report_date_as_yyyy_mm_dd", "")[:10]
        try:
            long_ = float(row.get("noncomm_positions_long_all", 0) or 0)
            short_ = float(row.get("noncomm_positions_short_all", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not d:
            continue
        out.append((d, long_ - short_))
    if out:
        _save_cache(key, out)
    return out


def cot_index(net_positions: list, lookback: int = 156) -> Optional[float]:
    """고전 COT Index(Larry Williams 류) — 트레일링 lookback 건(기본 156주
    ≈3년) 중 현재 순포지션의 백분위(0-100). 순수함수(테스트용). 100 =
    구간 내 최대(역사적 최고 매수쏠림), 0 = 최소(최고 매도쏠림). 데이터
    <2건 또는 구간 내 변동폭 0(전부 동일값) → None."""
    if len(net_positions) < 2:
        return None
    window = net_positions[-lookback:]
    current = window[-1]
    lo, hi = min(window), max(window)
    if hi == lo:
        return None
    return round((current - lo) / (hi - lo) * 100.0, 1)


def classify_cot_positioning(index: Optional[float], *, extreme_hi: float = 80.0,
                             extreme_lo: float = 20.0) -> str:
    """COT Index → 포지셔닝 상태. EXTREME_LONG_CROWDED(≥80, 역발상 매도경계)
    / EXTREME_SHORT_CROWDED(≤20, 역발상 매수경계) / NEUTRAL / UNKNOWN(None)."""
    if index is None:
        return "UNKNOWN"
    if index >= extreme_hi:
        return "EXTREME_LONG_CROWDED"
    if index <= extreme_lo:
        return "EXTREME_SHORT_CROWDED"
    return "NEUTRAL"


def price_failed_to_hold(closes: list, trigger_idx: int, direction: str,
                         confirm_days: int = 3) -> Optional[bool]:
    """뉴스/촉매 반응 실패 게이트 — trigger_idx 이후 confirm_days 거래일
    내 가격이 trigger_idx 종가를 반대방향으로 이탈하면 반응 실패(True).
    direction='up'(상승촉매 기대) 이면 이후 종가가 trigger 종가 아래로
    한 번이라도 내려가면 실패, 'down' 은 그 반대. 데이터 부족(trigger_idx
    가 범위 밖이거나 confirm_days 만큼 이후 데이터 없음) → None."""
    if trigger_idx < 0 or trigger_idx >= len(closes):
        return None
    end = trigger_idx + 1 + confirm_days
    if end > len(closes):
        return None
    trigger_close = closes[trigger_idx]
    follow = closes[trigger_idx + 1:end]
    if direction == "up":
        return any(c < trigger_close for c in follow)
    if direction == "down":
        return any(c > trigger_close for c in follow)
    return None


_SIGNAL_LABEL = {
    "EXTREME_LONG_CROWDED": "🔴 과열 매수쏠림 (역발상 매도경계)",
    "EXTREME_SHORT_CROWDED": "🟢 과열 매도쏠림 (역발상 매수경계)",
    "NEUTRAL": "⚪ 중립",
    "UNKNOWN": "— 데이터 부족",
}


def get_cot_signal(market_key: str) -> dict:
    """market_key 의 COT Index + 분류 — 실패해도 UNKNOWN 으로 graceful
    (market_timing.py 매크로레짐 부가신호용 진입점)."""
    hist = fetch_cot_net_positions(market_key)
    idx = cot_index([p for _, p in hist])
    return {"market": market_key, "index": idx,
            "signal": classify_cot_positioning(idx),
            "label": _SIGNAL_LABEL[classify_cot_positioning(idx)],
            "as_of": hist[-1][0] if hist else None}
