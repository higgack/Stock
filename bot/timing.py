"""요청별 단계 계측 — 동시 요청이 서로의 값을 덮지 않게.

⚠️ 왜 필요한가(2026-08-22 실측): 단계 시간을 **모듈 전역 dict 하나**에
쌓고 있었다. 대시보드는 `ThreadingHTTPServer` 라 사용자가 탭 세 개를 열면
세 종목의 수집이 겹치는데, 그러면 `chart-timing` 한 줄이 **누구 것인지 알
수 없다**(옛 `indicators=380.451s` 판독도 같은 위험을 안고 있었다 —
감사가 다른 요청의 상태를 보고 말하는 실수 #114·#35 의 계측판).

스레드로컬은 답이 아니다 — 일부 단계는 **풀 워커 스레드**에서 기록된다
(`_enrich_kr` 의 `kr:*`, 보조 6종). 그래서 **요청 키**(티커 등)로 가른다.
워커는 자기가 무슨 종목을 재는지 알기 때문이다.
"""

from __future__ import annotations

import threading

_KEEP = 32          # 최근 N개 키만 — 프로세스가 오래 살아도 안 자란다


class Stages:
    """`stages[key][stage] = 초`. 키 단위로 시작·읽기."""

    def __init__(self, keep: int = _KEEP) -> None:
        self._lock = threading.Lock()
        self._d: dict[str, dict[str, float]] = {}
        self._order: list[str] = []
        self._keep = keep

    def start(self, key: str) -> None:
        """이 키의 계측을 새로 시작(옛 값 제거)."""
        with self._lock:
            self._d[key] = {}
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            while len(self._order) > self._keep:
                self._d.pop(self._order.pop(0), None)

    def set(self, key: str, stage: str, sec: float) -> None:
        with self._lock:
            self._d.setdefault(key, {})[stage] = round(sec, 3)

    def snapshot(self, key: str) -> dict[str, float]:
        """그 키의 단계들. 없으면 빈 dict(캐시 히트 등)."""
        with self._lock:
            return dict(self._d.get(key) or {})
