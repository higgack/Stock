"""Telethon 엔티티 해석 · 시작 실패 사유 — trade 스크립트 공용 헬퍼.

왜(2026-08-27 실측, CLAUDE.md #258): 나쁜양파 동기화가
`FloodWaitError: A wait of 8073 seconds is required (caused by
ResolveUsernameRequest)` 로 죽고, 알림은 "Telethon 세션 만료 또는 채널
접근 불가 — 재인증 확인"을 띄웠다. 결함이 둘 겹친 것:

1. `client.get_entity("<username>")` 은 **매 실행마다** ResolveUsernameRequest
   를 쏜다 — 텔레그램은 그 호출을 계정 단위로 강하게 제한한다. 백필 2종
   (6h/2h 타이머) + 리스너 2종(크래시 시 systemd 재시작마다 재해석!) +
   진단 스크립트가 같은 계정으로 반복 해석하니 한도에 걸린다.
   `get_input_entity` 는 **세션 DB 캐시를 먼저** 본다(username 포함) —
   캐시 미스(새 세션)일 때만 네트워크로 나가고, 그 결과가 세션에 저장돼
   다음 실행부터 해석 요청이 0 이 된다.
2. FloodWait 는 세션 만료가 아니다 — 기다리면 풀린다. 사유를 뭉뚱그리면
   운영자가 멀쩡한 세션을 재인증하러 간다(#82 갈래를 이름으로 부를 것).

telethon 을 module-level 로 import 하지 않는다 — 판정은 예외 이름·속성으로
가르므로(순수) telethon 이 없는 샌드박스에서도 테스트가 돈다(#41).
"""
from __future__ import annotations


async def resolve_peer(client, ref):
    """username/id → InputPeer. **세션 캐시 우선**(get_input_entity) —
    문자열 username 을 get_entity 로 해석하면 매번 ResolveUsernameRequest 가
    나간다. 캐시 미스(새 세션) 첫 1회만 get_entity 로 채운다.

    다운스트림(iter_messages · forward_messages · events.NewMessage(chats=) ·
    telethon.utils.get_peer_id)은 전부 InputPeer 를 받는다 — 단 `.id` 속성은
    InputPeer 에 없으므로 로깅은 get_peer_id 로 할 것.
    """
    try:
        return await client.get_input_entity(ref)
    except Exception:                                          # noqa: BLE001
        return await client.get_entity(ref)


def startup_failure_note(exc) -> str:
    """세션/접근 실패 알림에 붙일 **사유 갈래**(#82). 갈래별 처방이 다르다 —
    FloodWait 에 '재인증 확인'이라고 적으면 운영자가 헛걸음한다."""
    name = type(exc).__name__
    secs = getattr(exc, "seconds", None)
    if name == "FloodWaitError":
        tail = ""
        if isinstance(secs, (int, float)) and secs == secs:
            tail = f" {int(secs)}초(≈{int(secs) // 60}분)"
        return (f"텔레그램 요청 제한(FloodWait{tail}) — 세션은 정상이고 "
                "재인증 불필요. 대기시간이 지나면 다음 정기 실행이 자동 "
                "재시도합니다.")
    if name in ("AuthKeyError", "AuthKeyUnregisteredError",
                "AuthKeyDuplicatedError", "SessionRevokedError",
                "SessionExpiredError", "SessionPasswordNeededError",
                "UnauthorizedError"):
        return "Telethon 세션 만료/철회 — 재인증이 필요합니다."
    if name in ("ChannelPrivateError", "ChannelInvalidError",
                "UsernameNotOccupiedError", "UsernameInvalidError"):
        return ("채널 접근 불가(비공개 전환·링크 변경 가능성) — 채널 상태를 "
                "확인하세요.")
    return "원인 미상 — 세션/채널/네트워크 갈래를 로그로 확인하세요."
