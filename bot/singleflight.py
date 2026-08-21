"""같은 작업이 **동시에** 두 번 돌지 않게 — 하나만 하고 결과를 나눠 준다.

⚠️ 왜 필요한가(2026-08-21 `api-timing` 실측): 같은 요청이 **같은 밀리초에
두 번** 들어오고 있었다.

    api-timing /api/quarterly ticker=USDE 10325ms
    api-timing /api/quarterly ticker=USDE 10327ms
    api-timing /api/band ticker=376300.KQ 2116ms
    api-timing /api/band ticker=376300.KQ 1982ms

둘 다 바깥 원천을 각각 두드린다 — 화면이 스스로 부하를 두 배로 만든다.
디스크 캐시는 **끝난 뒤에만** 도와주므로 진행 중인 중복은 못 막는다.
탭을 두 개 열어도 같은 일이 나므로 막는 자리는 **서버**다.

⚠️ 리더가 오래 걸리면 팔로워도 같이 기다린다 — 무한 대기는 안 되므로
상한(기본 180초)을 두고, 넘으면 팔로워가 스스로 한다(느려도 응답은 한다).
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("bot.singleflight")

_WAIT_MAX = 180.0


class _Call:
    __slots__ = ("ev", "ok", "val")

    def __init__(self):
        self.ev = threading.Event()
        self.ok = False
        self.val = None


_LOCK = threading.Lock()
_INFLIGHT: dict[str, _Call] = {}


def once(key: str, fn, wait_max: float = _WAIT_MAX):
    """`key` 가 진행 중이면 그 결과를 **기다려 공유**하고, 아니면 직접 한다.

    리더가 던진 예외는 팔로워에게도 그대로 전달된다 — 한쪽만 성공한 척하면
    화면이 갈라진다.
    """
    with _LOCK:
        call = _INFLIGHT.get(key)
        leader = call is None
        if leader:
            call = _Call()
            _INFLIGHT[key] = call
    if leader:
        try:
            call.val, call.ok = fn(), True
        except BaseException as exc:                           # noqa: BLE001
            call.val, call.ok = exc, False
        finally:
            with _LOCK:
                _INFLIGHT.pop(key, None)
            call.ev.set()
        if call.ok:
            return call.val
        raise call.val
    if not call.ev.wait(wait_max):
        # 리더가 너무 오래 — 스스로 한다(무한 대기 금지).
        log.info("singleflight: %s 대기 초과 — 직접 수행", key)
        return fn()
    if call.ok:
        return call.val
    raise call.val


def inflight() -> int:
    """진행 중인 키 수 — 진단용."""
    with _LOCK:
        return len(_INFLIGHT)
