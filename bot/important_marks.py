"""중요(important) 마크 — 대시보드 카드별 '중요' 토글 서버 저장 (사용자 2026-06-26).

모바일·데스크탑 등 기기 무관 동기화를 위해 브라우저(localStorage) 대신 서버에
저장한다. 단일 인증(운영자 1인)이라 전역 1벌이면 충분. 표면(surface)별로 카드의
안정적 고유 id 를 키로 보관:

    { "<surface>": { "<id>": <unix_ts>, ... }, ... }

surface = analysis|screener|screen|dart|valuechain|daily_byte|reddit|blog|
realestate|cheongyak (각 대시보드 1개). id = 카드 안정 식별자(예: ticker|date,
date|filename, rcept_no, company|relation|target). 정적 HTML 은 페이지 로드 시
GET /api/important 로 현재 마크를 받아 ★ 상태·필터를 그린다(재생성 무관, 즉시 반영).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("bot.important_marks")

_FILE = Path.home() / ".tradingagents" / "important_marks.json"
_LOCK = threading.Lock()

# 허용 surface — 오타·임의 키 누수 방지(전 대시보드 단일 레지스트리).
SURFACES = (
    "analysis", "screener", "screen", "dart", "valuechain",
    "daily_byte", "reddit", "blog", "realestate", "cheongyak",
)
_MAX_ID = 256          # id 길이 가드(비정상 입력 차단)


def _load() -> dict:
    if _FILE.exists():
        try:
            d = json.loads(_FILE.read_text("utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(_FILE)


def all_marks() -> dict[str, list[str]]:
    """{surface: [id...]} — 전 표면 현재 마크. 페이지 로드 시 GET 으로 1회 전달."""
    with _LOCK:
        data = _load()
    out: dict[str, list[str]] = {}
    for surf, ids in data.items():
        if isinstance(ids, dict):
            out[surf] = list(ids.keys())
    return out


def marks(surface: str) -> list[str]:
    """한 표면의 마크된 id 목록."""
    return all_marks().get(surface, [])


def toggle(surface: str, mark_id: str, on: bool) -> bool:
    """surface/id 마크를 on/off. 반환 = 적용 후 상태(True=중요). 잘못된 입력은
    그대로 무시(False 반환, 저장 안 함). 동시쓰기 안전(_LOCK + atomic replace)."""
    surface = (surface or "").strip()
    mark_id = (mark_id or "").strip()
    if surface not in SURFACES or not mark_id or len(mark_id) > _MAX_ID:
        return False
    with _LOCK:
        data = _load()
        bucket = data.get(surface)
        if not isinstance(bucket, dict):
            bucket = {}
            data[surface] = bucket
        if on:
            bucket[mark_id] = int(time.time())
        else:
            bucket.pop(mark_id, None)
            if not bucket:
                data.pop(surface, None)
        _save(data)
    return bool(on)
