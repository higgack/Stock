"""CNN Fear & Greed Index — 시장 센티먼트 게이지 실지수 (사용자 2026-07-08).

비공식 JSON: https://production.dataviz.cnn.io/index/fearandgreed/graphdata
응답: {"fear_and_greed": {"score": 43.06, "rating": "fear",
       "timestamp": "...", "previous_close": 43.0, "previous_1_week": 30.0,
       "previous_1_month": 41.0, "previous_1_year": 75.0}, ...}
기본 UA 는 403 — 브라우저 UA 필수. 30분 디스크 캐시 + 실패 시 마지막 성공분
(stale 표기). 완전 실패 시 {} — 게이지는 기존 VIX 역산 폴백(라벨 정직 표기).
샌드박스 프록시 403 이라 실검증은 VM 런타임(WARNING 로그로 가시화).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    data: dict = {}
    try:
        import requests
        r = requests.get(_URL, timeout=15, headers={
            "User-Agent": _UA, "Accept": "application/json"})
        r.raise_for_status()
        data = _parse(r.json())
    except Exception as exc:
        log.warning("fear&greed fetch failed: %s", exc)
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
