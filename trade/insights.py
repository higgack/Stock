"""변동 감지(fingerprint) + 파이프라인 상태 저장.

급등률·급증액·산업트렌드는 모두 같은 관세청 sweep에서 나오므로, 저장된
집계 스냅샷의 해시 한 개로 '이번 틱에 데이터가 실제로 바뀌었는지'를 판정
한다. 관세청 확정치는 매월 ~15일 한 번 갱신되므로, 이 게이트가 4회/일 틱
중 변동이 있는 틱에서만 후속 작업(완료 DM·[B단계] LLM 추가신호 생성)을
돌리게 해 채널 스팸과 불필요한 LLM 호출을 막는다(blast-radius/비용 가드).
"""
from __future__ import annotations

import hashlib
import time

# fingerprint 대상: 집계 결과 컬럼만. updated_ts는 제외 — 값이 동일하면 매
# 틱 재저장(DELETE+INSERT)돼도 fingerprint는 불변이어야 하므로.
_FP_TABLES = [
    ("industry_series", "industry, months_json, imports_json"),
    ("mti_series", "mti6, payload_json, import_json"),
]


def data_fingerprint(conn) -> str:
    """저장된 산업/하위품목(MTI) 집계의 안정적 해시. 새 확정월이 들어오거나
    값이 정정되면 바뀌고, 동일 데이터 재저장으로는 안 바뀐다."""
    parts: list[str] = []
    for tbl, cols in _FP_TABLES:
        try:
            rows = conn.execute(f"SELECT {cols} FROM {tbl} ORDER BY 1").fetchall()
        except Exception:
            rows = []
        parts.append(repr(rows))
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_state ("
        "key TEXT PRIMARY KEY, value TEXT, updated_ts REAL)"
    )


def get_state(conn, key: str) -> str | None:
    _ensure(conn)
    row = conn.execute(
        "SELECT value FROM pipeline_state WHERE key=?", (key,)
    ).fetchone()
    return row[0] if row else None


def set_state(conn, key: str, value: str, now: float | None = None) -> None:
    _ensure(conn)
    conn.execute(
        "INSERT INTO pipeline_state (key, value, updated_ts) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_ts=excluded.updated_ts",
        (key, value, now if now is not None else time.time()),
    )
    conn.commit()
