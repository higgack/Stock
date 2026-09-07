"""일일 작동 원장 (사용자 2026-06-13 '제대로 됐는지 확인하는 게 쉽지 않아').

관세청 스캔/probe/갱신/오류, BeOn 포워드 등 '오늘 실제로 한 일'을
KST 날짜별 카운터로 적립 → 자정 결산(daily_digest)이 읽어 활동한 날에만
"📊 06/10 결산 — 포워드 100건…" DM. 오류 알람의 일 1회 dedup 게이트도
이 카운터(bump 반환값 == 1)로 해결. 순수 json 파일 — LLM 0·₩0.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
LEDGER_PATH = _DATA_DIR / "run_ledger.json"
_KEEP_DAYS = 14


def _today() -> str:
    return datetime.now(_KST).date().isoformat()


def _load() -> dict:
    try:
        d = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    # 14일 prune — 결산은 어제 하루만 읽으므로 무한 성장 불필요
    cutoff = (datetime.now(_KST).date() - timedelta(days=_KEEP_DAYS)).isoformat()
    d = {k: v for k, v in d.items() if k >= cutoff}
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(LEDGER_PATH)
    except OSError:
        pass


def bump(field: str, n: int = 1, date_key: str | None = None) -> int:
    """카운터 += n, 누계 반환. 반환값 1 == '오늘 첫 발생' → 오류 알람
    일 1회 dedup 게이트로 사용."""
    d = _load()
    day = d.setdefault(date_key or _today(), {})
    day[field] = int(day.get(field, 0)) + n
    _save(d)
    return day[field]


def bump_kind(field: str, kind: str, date_key: str | None = None) -> None:
    """오류 **갈래**를 같이 적립 — `{field}_kinds` 하위 카운터.

    ⚠️ 숫자만 세면 결산이 `probe 오류 42회` 라고만 말한다. 타임아웃·요청한도·
    인증·원천장애는 **처방이 정반대**라 운영자가 그 42를 보고 짐작하게 된다
    (#82 '없음'만 말하는 진단은 추측을 부른다 · #279 진단의 모든 문장이 잰
    것인지 물을 것). 갈래는 예외에서 **재서** 붙인다 — 모르면 클래스명 그대로.
    """
    if not kind:
        return
    d = _load()
    day = d.setdefault(date_key or _today(), {})
    kinds = day.setdefault(f"{field}_kinds", {})
    if not isinstance(kinds, dict):          # 옛 형식 방어
        kinds = day[f"{field}_kinds"] = {}
    kinds[kind] = int(kinds.get(kind, 0)) + 1
    _save(d)


def day_counts(date_key: str) -> dict:
    """해당 KST 날짜의 카운터 dict (없으면 {})."""
    return dict(_load().get(date_key, {}))
