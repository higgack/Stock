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

import re

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


def parse_reason(what: str, size: int, *, unit: str = "B") -> str:
    """200 을 받았는데 0건 파싱 — 원천 구조 변경 의심(우리가 고칠 것).

    ⚠️ '원천에 정말 없다' 와 구별해서 쓸 것 — 목록 페이지는 비는 날이 없으므로
    0건이면 구조 변경이지만, 날짜 필터가 있는 목록은 정상적으로 0건일 수 있다.

    ⚠️ `unit` 은 **거짓말을 막으려고** 있다: HTML 시절엔 `size` 가 바이트였는데
    JSON 전환 뒤 호출부 넷이 **행 수**를 넘기면서 화면이 `원천 응답(20B)` 이라고
    적고 있었다(독립 리뷰 2026-09-11 M4). 20행을 20바이트라고 말하면 운영자가
    '응답이 잘렸나' 를 보러 간다 — 라벨에 기준을 박을 것(#34·#64 단위가 다른
    값을 한 자리에 넣지 말 것).
    """
    return (f"원천 응답({size:,}{unit})은 받았는데 {what}을 한 건도 못 읽었습니다 "
            "— 원천 구조 변경 의심(우리가 고칠 것)")


def shape_reason(what: str, got: object) -> str:
    """200·JSON 파싱까지 됐는데 **모양이 다르다** — 목록을 기대했는데 아닌 경우.

    ⚠️ `parse_reason`(행은 왔는데 못 읽음)과 갈라 쓸 것. 모양이 다르면 우리
    파서가 아니라 **엔드포인트 계약**이 바뀐 것이라 고칠 자리가 다르다(#82).
    그리고 `isinstance(raw, list)` 를 안 물으면 dict 응답이 `len()` 0 을 거쳐
    '원천이 0건' 으로 조용히 통과한다(독립 리뷰 2026-09-11 H1).
    """
    t = type(got).__name__
    if isinstance(got, dict):
        keys = ", ".join(list(got)[:6]) or "(빈 dict)"
        return (f"원천이 {what} 자리에 목록이 아니라 dict 를 줬습니다 "
                f"— 키: {keys} · 엔드포인트 계약 변경 의심(우리가 고칠 것)")
    return (f"원천이 {what} 자리에 목록이 아니라 {t} 를 줬습니다 "
            "— 엔드포인트 계약 변경 의심(우리가 고칠 것)")


def get_json(url: str, *, headers: dict, log, tag: str,
             timeout: int = 15, **kwargs):
    """(파싱된 JSON, 실패 사유) — 네이버 JSON API 공용 GET(#38 단일 출처).

    ⚠️ 형제 클라이언트가 이 함수를 **각자 복제**하고 있었고 이미 갈라져
    있었다(한쪽만 'JSON 아님' 을 로그에 남겼다 — 독립 리뷰 2026-09-11 M6).
    복제하면 갈래 어휘가 한쪽만 고쳐진다.

    ⚠️ 옛 HTML 경로의 `_get2` 는 응답을 **euc-kr 로 강제 디코딩**한다
    (finance.naver.com HTML 이 그랬다). JSON API 는 UTF-8 이라 그 경로를
    그대로 쓰면 한글이 통째로 깨진다 — 그래서 별도 함수다.
    ⚠️ **빈 리스트는 실패가 아니다** — 원천이 0건인 것과 못 받은 것은 처방이
    다르므로 사유는 `""` 로 둔다(#54·#82).
    """
    import requests

    try:
        from bot.finviz_client import naver_paused
        if naver_paused():
            return None, PAUSED
    except Exception:                                       # noqa: BLE001
        pass
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, **kwargs)
        size = len(resp.content or b"")
        if resp.status_code != 200:
            log.warning("%s json: %s -> HTTP %s", tag, url, resp.status_code)
            return None, http_reason(resp.status_code, size)
        try:
            return resp.json(), ""
        except ValueError:
            log.warning("%s json: %s -> JSON 아님 (%dB)", tag, url, size)
            return None, parse_reason("JSON", size)
    except Exception as exc:                                # noqa: BLE001
        log.warning("%s json: fetch failed %s: %s", tag, url, exc)
        return None, http_reason(None, exc=exc)


_HTTP_CODE_RE = re.compile(r"원천이 HTTP (\d{3})")


def reason_rank(reason: str) -> int:
    """사유 → **얼마나 행동 가능한가**(작을수록 먼저 보고). 순수.

    후보 사다리는 실패 사유를 여럿 만든다. 옛 판은 그중 **마지막** 것을 화면에
    적었다 — 2026-09-12 실측에서 1순위(증명된 호스트)가 `HTTP 400`, 2·3순위
    (추측 후보)가 `HTTP 404` 였는데 화면은 404 를 적어, 운영자를 '주소가
    없다'(=새 주소를 찾아라)로 보냈다. 실제로는 **주소는 있고 우리 파라미터가
    거부된 것**이라 고칠 자리가 다르다(#275 여러 라벨 자리를 훑는 진단은 '먼저
    찾은 것'이 아니라 **가장 행동 가능한 것**을 머리에 둘 것 · #82).

    | 값 | 뜻                                        | 처방                    |
    |----|-------------------------------------------|-------------------------|
    | 0  | 우리가 안 물어봤다(일시정지)               | `/naverpause` 해제      |
    | 1  | 주소는 답했다 — 거절·못 읽음(400·403·429·5xx·parse) | 요청·파서·한도·원천장애 |
    | 2  | 주소가 없거나 못 닿았다(404·타임아웃)       | 주소를 다시 찾는다      |
    | 3  | 사유조차 없다                              | 판정 불가              |

    ⚠️ 문자열을 훑는 판정이라 **여기 형제 생산부**(`http_reason`·`parse_reason`
    ·`shape_reason`)와 한 파일에 둔다 — 한쪽만 바뀌면 갈라지므로 회귀가 그
    생산부가 **실제로 만든 문자열**로 왕복을 고정한다(#19·#38·#155).
    """
    r = str(reason or "")
    if not r:
        return 3
    if PAUSED in r:
        return 0
    m = _HTTP_CODE_RE.search(r)
    if m:
        return 2 if m.group(1) == "404" else 1
    if "닿지 못함" in r:
        return 2
    # 200 을 받고 못 읽었다(parse·shape·자원 오류) — 우리가 고칠 자리가 있다.
    return 1


def stale_label(age_sec: float | int | None) -> str:
    """저장분 나이 → '(3시간 전)'. 못 재면 빈 문자열(#165 안 잰 것을 말하지 않는다).
    형제 위젯(TW 업종, #306)과 같은 규약 — 분/시간 경계 120분, 시간/일 경계 48시간
    (`168시간 전` 은 사람이 못 읽는다 — 독립 리뷰 2026-09-11)."""
    try:
        m = int(float(age_sec) // 60)
    except (TypeError, ValueError):
        return ""
    if m < 120:
        return f"{m}분 전"
    h = m // 60
    return f"{h}시간 전" if h < 48 else f"{h // 24}일 전"
