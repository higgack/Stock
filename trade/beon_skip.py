"""BeOn 재포워드 차단 set — 사용자 한 번 탭으로 반복 메시지 영구 차단.

문제: 유저-포워드(채널포스트 아님)·fwd_from 체인 불완전 메시지는 backfill_beon
의 dedup(inbox.jsonl 기반)이 식별 못 해, 매 동기화 틱(2시간)마다 '신규'로 재
판정 → trade 채널에 같은 메시지를 영원히 재포워드(사용자 2026-06-15 '형식 안
맞는 거 두 시간마다 계속 옴'). 해결: '동기화 완료' 알림의 🚫 인라인 버튼 →
trade-bot 콜백이 그 BeOn 소스 msg_id 를 이 set 에 add → backfill_beon 이 다음
틱부터 후보에서 제외(contains). 두 프로세스(스크립트·봇) 공유 파일.

저장: $TRADE_DATA_DIR/beon_skip.json (없으면 ~/.trade/). atomic write. graceful.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _path() -> Path:
    base = os.environ.get("TRADE_DATA_DIR") or str(Path.home() / ".trade")
    return Path(base) / "beon_skip.json"


def load() -> set[int]:
    """차단된 BeOn 소스 msg_id 집합. 파일 부재/손상 시 빈 set(graceful)."""
    try:
        data = json.loads(_path().read_text("utf-8"))
        return {int(x) for x in data}
    except Exception:
        return set()


def contains(msg_id) -> bool:
    try:
        return int(msg_id) in load()
    except Exception:
        return False


def add(msg_ids) -> int:
    """msg_id(들)을 차단 set 에 추가. 반환 = 추가 후 총 개수. atomic·graceful."""
    cur = load()
    if isinstance(msg_ids, (int, str)):
        msg_ids = [msg_ids]
    for m in msg_ids:
        try:
            cur.add(int(m))
        except Exception:
            pass
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(cur)), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass
    return len(cur)
