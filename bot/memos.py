"""내 메모 — 대시보드 카드별 자유 메모 서버 저장 (사용자 2026-06-26).

★ 중요 마크(bot.important_marks)와 동일 패턴·동일 surface/id 키. 기기 무관 동기화를
위해 서버 JSON 저장. 빈 텍스트 저장 = 삭제. 정적 재생성에도 유지(클라가 GET 으로 로드).

    { "<surface>": { "<id>": {"text": "...", "updated": <ts>} } }
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from bot.important_marks import SURFACES, _MAX_ID

log = logging.getLogger("bot.memos")

_FILE = Path.home() / ".tradingagents" / "memos.json"
_LOCK = threading.Lock()
_MAX_TEXT = 8000          # 메모 본문 길이 가드


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


def all_memos() -> dict[str, dict[str, str]]:
    """{surface: {id: text}} — 전 표면 메모(점 표시 + 편집 prefill 용)."""
    with _LOCK:
        data = _load()
    out: dict[str, dict[str, str]] = {}
    for surf, items in data.items():
        if isinstance(items, dict):
            out[surf] = {k: (v.get("text", "") if isinstance(v, dict) else str(v))
                         for k, v in items.items()}
    return out


def get(surface: str, mem_id: str) -> str:
    """한 카드의 메모 텍스트(없으면 '')."""
    return all_memos().get(surface, {}).get(mem_id, "")


def set_memo(surface: str, mem_id: str, text: str) -> dict:
    """메모 저장. 빈 텍스트 = 삭제. 반환 {ok, has, text}. 잘못된 입력은 ok=False."""
    surface = (surface or "").strip()
    mem_id = (mem_id or "").strip()
    text = (text or "").strip()
    if surface not in SURFACES or not mem_id or len(mem_id) > _MAX_ID:
        return {"ok": False, "error": "invalid surface/id"}
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT]
    with _LOCK:
        data = _load()
        bucket = data.get(surface)
        if not isinstance(bucket, dict):
            bucket = {}
            data[surface] = bucket
        if text:
            bucket[mem_id] = {"text": text, "updated": int(time.time())}
        else:
            bucket.pop(mem_id, None)
            if not bucket:
                data.pop(surface, None)
        _save(data)
    return {"ok": True, "has": bool(text), "text": text}
