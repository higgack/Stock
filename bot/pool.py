"""외부 조회용 **공용 스레드 풀** — 동시 요청이 겹쳐도 팬아웃이 안 곱해지게.

⚠️ 왜 필요한가(사용자 2026-08-21 "전보다 더 걸리는것 같아. 내가 동시에
돌리는게 많아서 그런가?"): 대시보드는 `ThreadingHTTPServer` 라 요청마다
스레드가 뜬다. 거기에 **요청마다 새 풀**을 만들면 동시에 두 종목을 열 때
바깥 원천(DART·금융위)으로 나가는 동시 요청이 그대로 곱해진다 —
한 요청이 빨라지려고 넣은 병렬화가 여러 요청에선 서로를 느리게 만든다.
그래서 **프로세스 전체에 상한 하나**를 둔다.

⚠️ **중첩 금지.** 이 풀에서 도는 작업이 다시 이 풀에 제출하고 그 결과를
기다리면 슬롯이 서로를 기다려 **교착**한다. 그래서 이 풀은 **말단 팬아웃
전용**이다 — `enrich:KR` 같은 상위 병렬은 자기 풀을 그대로 쓴다(그쪽은
작업 수만큼 풀을 열고 바깥으로 직접 나가지 않는다). 회귀가 이 규약을
지킨다.

상한은 `STOCK_FETCH_POOL` 로 조절(기본 24).
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("bot.pool")

_LOCK = threading.Lock()
_POOL: ThreadPoolExecutor | None = None


def _max_workers() -> int:
    try:
        n = int(os.environ.get("STOCK_FETCH_POOL", "24"))
    except ValueError:
        n = 24
    return max(2, min(n, 64))


def shared_pool() -> ThreadPoolExecutor:
    """프로세스 공용 풀(지연 생성)."""
    global _POOL
    if _POOL is None:
        with _LOCK:
            if _POOL is None:
                _POOL = ThreadPoolExecutor(max_workers=_max_workers(),
                                           thread_name_prefix="fetch")
    return _POOL


def map_bounded(fn, items: list) -> list:
    """`items` 를 공용 풀에서 병렬 실행 → **입력 순서** 결과 리스트.

    한 항목이 던지면 그 자리에 None 을 넣는다 — 하나의 실패가 나머지를
    막지 않는다(호출부가 None 을 걸러 쓴다).
    """
    items = list(items or [])
    if not items:
        return []
    if len(items) == 1:
        try:
            return [fn(items[0])]
        except Exception as exc:                               # noqa: BLE001
            log.debug("pool: 단일 작업 실패: %s", exc)
            return [None]
    futs = [shared_pool().submit(fn, it) for it in items]
    out = []
    for f in futs:
        try:
            out.append(f.result())
        except Exception as exc:                               # noqa: BLE001
            log.debug("pool: 작업 실패: %s", exc)
            out.append(None)
    return out
