"""CNN Fear & Greed Index — 시장 센티먼트 게이지 실지수 (사용자 2026-07-08).

비공식 JSON: https://production.dataviz.cnn.io/index/fearandgreed/graphdata
응답: {"fear_and_greed": {"score": 43.06, "rating": "fear",
       "timestamp": "...", "previous_close": 43.0, "previous_1_week": 30.0,
       "previous_1_month": 41.0, "previous_1_year": 75.0}, ...}
기본 UA 는 403 — 브라우저 UA 필수. 30분 디스크 캐시 + 실패 시 마지막 성공분
(stale 표기). 완전 실패 시 {} — 게이지는 기존 VIX 역산 폴백(라벨 정직 표기).
샌드박스 프록시 403 이라 실검증은 VM 런타임(WARNING 로그로 가시화).

VIX 서브지표(2026-07-26, 사용자 요청 — "VIX 는 가장 정확한 CNN 값으로,
양쪽 다"): 공개적으로 문서화된 graphdata 응답 구조상 최상위에
market_volatility_vix.data = [[epoch_ms, value], ...] 형태로 원시 VIX
시계열이 같이 온다(공식 API 문서는 없음 — 널리 알려진 리버스엔지니어링
구조 기반 추정, 이 세션은 CNN 접근이 막혀 있어 실검증 불가·VM 런타임에서만
확인 가능). _extract_vix_component 가 이 구조를 관대하게 파싱하고, 실패
시(구조가 다르거나 키 없음) None 반환 — 호출부(macro_snapshot.py 매크로9
위젯 · market_timing.py 변동성 카드)는 기존 네이버→yfinance 폴백 체인으로
조용히 이어감(값 소실 없음, 조용한 오작동도 없음).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.fear_greed")

_KST = timezone(timedelta(hours=9))
_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_CACHE = Path.home() / ".tradingagents" / "fear_greed.json"
_TTL = 30 * 60

_RATING_KR = {
    "extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립",
    "greed": "탐욕", "extreme greed": "극단적 탐욕",
}


def _extract_vix_component(payload: dict):
    """graphdata 응답의 시장변동성(VIX) 서브지표 원시값 추출 — 모듈 상단
    독스트링 참조(구조 추정, 이 세션 실검증 불가). market_volatility_vix.
    data 마지막 [ts, value] 우선, 없으면 value/score 스칼라 키도 시도.
    전부 실패 시 None(순수 tolerant 파싱, 크래시 없음)."""
    comp = (payload or {}).get("market_volatility_vix")
    if not isinstance(comp, dict):
        return None
    series = comp.get("data")
    if isinstance(series, list) and series:
        last = series[-1]
        if isinstance(last, (list, tuple)) and len(last) >= 2:
            try:
                return float(last[1])
            except (TypeError, ValueError):
                pass
    for key in ("value", "score"):
        v = comp.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _parse(payload: dict) -> dict:
    """CNN graphdata → 우리 스키마. 필드 tolerant — score 없으면 {}."""
    fg = (payload or {}).get("fear_and_greed") or {}
    score = fg.get("score")
    if not isinstance(score, (int, float)):
        return {}
    rating = str(fg.get("rating") or "").strip().lower()
    ts = ""
    raw_ts = str(fg.get("timestamp") or "")
    try:
        dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        ts = dt.astimezone(_KST).strftime("%m.%d %H:%M KST")
    except ValueError:
        pass

    def _i(v):
        return int(round(v)) if isinstance(v, (int, float)) else None

    return {
        "score": int(round(score)),
        "rating": rating,
        "rating_kr": _RATING_KR.get(rating, rating or "—"),
        "ts": ts,
        "prev_close": _i(fg.get("previous_close")),
        "prev_1w": _i(fg.get("previous_1_week")),
        "prev_1m": _i(fg.get("previous_1_month")),
        "prev_1y": _i(fg.get("previous_1_year")),
        "vix": _extract_vix_component(payload),
    }


def fetch_fear_greed() -> dict:
    """실 CNN Fear & Greed {score, rating_kr, ts, prev_*} — 30분 캐시.
    실패 시 마지막 성공분(있으면), 그것도 없으면 {}(호출부 VIX 폴백)."""
    try:
        with open(_CACHE, encoding="utf-8") as f:
            c = json.load(f) or {}
    except Exception:
        c = {}
    now = time.time()
    if c.get("data") and now - (c.get("ts") or 0) < _TTL:
        return c["data"]
    # 실패 스로틀 — CNN 차단 환경에서 매 market regen(30초~1분)마다 15초
    # 타임아웃을 물고 전체 갱신이 느려지는 것 방지: 실패 후 5분간 재시도 안
    # 함(마지막 성공분/폴백 즉시 반환).
    if now - (c.get("fail_ts") or 0) < 300:
        return dict(c["data"], stale=True) if c.get("data") else {}
    data: dict = {}
    try:
        import requests
        r = requests.get(_URL, timeout=15, headers={
            "User-Agent": _UA, "Accept": "application/json"})
        r.raise_for_status()
        data = _parse(r.json())
    except Exception as exc:
        log.warning("fear&greed fetch failed: %s", exc)
    if not data:
        try:
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(c, fail_ts=now), f, ensure_ascii=False)
            os.replace(tmp, _CACHE)
        except Exception:
            pass
    if data:
        try:
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _CACHE.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"data": data, "ts": now}, f, ensure_ascii=False)
            os.replace(tmp, _CACHE)
        except Exception as exc:
            log.warning("fear&greed cache write failed: %s", exc)
        return data
    if c.get("data"):
        return dict(c["data"], stale=True)
    return {}


def fetch_cnn_vix() -> Optional[float]:
    """CNN Fear&Greed graphdata 의 시장변동성(VIX) 서브지표 최신값(2026-07-26,
    사용자 요청 — "VIX 는 가장 정확한 CNN 값으로") — fetch_fear_greed() 와
    동일 fetch/캐시 재사용(추가 네트워크 콜 0). 파싱 실패/CNN 접근불가 시
    None(호출부가 네이버→yfinance 로 폴백, 모듈 상단 독스트링 참조)."""
    data = fetch_fear_greed()
    v = data.get("vix")
    return float(v) if isinstance(v, (int, float)) else None
