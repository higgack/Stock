"""네이버 경유 위젯이 **왜 비었나** — 갈래 이름 단일 출처(#38·#82).

위젯이 비는 원인은 넷이고 **처방이 전부 다르다**:

| 갈래   | 뜻                                   | 처방                         |
|--------|--------------------------------------|------------------------------|
| paused | 우리가 네이버 호출을 꺼 뒀다         | `/naverpause` 로 해제        |
| http   | 원천이 거절(403·429·5xx)하거나 못 닿음 | 기다림 · 차단이면 IP/헤더    |
| parse  | 200 을 받았는데 우리가 못 읽었다      | 원천 구조 변경 — 우리가 고칠 것 |
| empty  | 원천이 정말 0건                       | 고칠 것 없음                 |

'데이터 없음' 하나로 뭉뚱그리면 사용자가 원인을 짐작하게 된다(#82 '없음'만
말하는 진단은 추측을 부른다 · #52 조용한 것과 죽은 것). 화면·로그·`/health`
가 **같은 문구**를 쓰도록 여기 한 곳에서 만든다 — 복제하면 한쪽만 고쳐진다.
"""
from __future__ import annotations

PAUSED = "네이버 호출 일시정지 중(NAVER_PAUSE 마커 · 텔레그램 /naverpause 로 해제)"


def http_reason(status: int | None, size: int | None = None,
                exc: BaseException | None = None) -> str:
    """HTTP 결과 → 사유 한 줄. 갈래마다 처방이 다르므로 상태코드를 그대로 싣는다
    (#290 숫자만 세는 원장은 처방이 정반대인 갈래를 못 가른다)."""
    if exc is not None:
        return f"원천에 닿지 못함 — {type(exc).__name__}: {str(exc)[:80]}"
    if status is None:
        return "원천에 닿지 못함 — 응답 없음"
    if status == 200:
        return f"원천이 200 을 줬는데 본문이 비었음({size or 0}B)"
    if status in (401, 403):
        return f"원천이 HTTP {status} — 차단·봇 탐지 의심(우리 IP·헤더)"
    if status == 429:
        return f"원천이 HTTP {status} — 요청 한도 초과"
    if 500 <= status < 600:
        return f"원천이 HTTP {status} — 원천 장애"
    return f"원천이 HTTP {status}"


def parse_reason(what: str, size: int) -> str:
    """200 을 받았는데 0건 파싱 — 원천 구조 변경 의심(우리가 고칠 것).

    ⚠️ '원천에 정말 없다' 와 구별해서 쓸 것 — 목록 페이지는 비는 날이 없으므로
    0건이면 구조 변경이지만, 날짜 필터가 있는 목록은 정상적으로 0건일 수 있다."""
    return (f"원천 응답({size:,}B)은 받았는데 {what}을 한 건도 못 읽었습니다 "
            "— 원천 구조 변경 의심(우리가 고칠 것)")


def stale_label(age_sec: float | int | None) -> str:
    """저장분 나이 → '(3시간 전)'. 못 재면 빈 문자열(#165 안 잰 것을 말하지 않는다).
    형제 위젯(TW 업종, #306)과 같은 규약 — 분/시간 경계 120분."""
    try:
        m = int(float(age_sec) // 60)
    except (TypeError, ValueError):
        return ""
    return f"{m}분 전" if m < 120 else f"{m // 60}시간 전"
