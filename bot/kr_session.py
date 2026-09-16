"""KRX·NXT 거래 세션 창 — **단일 출처**(네이버증권 공지 153, 사용자 2026-09-16).

2026년 개편으로 **KRX 에도 애프터마켓(16:00~20:00)** 이 생겼다. 그 전까지
'한국 시간외 = NXT' 라는 가정이 코드 여러 곳에 리터럴 창으로 박혀 있었고
(`prepost_client._in_kr_extended_window` 등), 그대로 두면 KRX 보드가 NXT 창을
쓰거나 그 반대가 된다 — 같은 계산을 두 곳이 따로 적으면 반드시 갈라진다
(#38). 그래서 창은 여기 한 벌만 두고 화면·수집기·진단이 전부 이걸 부른다.

⚠️ 여기 적힌 시각은 **원천(네이버증권 공지 153)이 밝힌 것**이다. 우리가 잰
것이 아니므로 원천이 개편하면 이 표를 고쳐야 한다(#165 — 재지 않은 것을
단정하지 말 것. 그래서 표마다 출처를 적는다).

휴장일은 여기서 판정하지 않는다 — 주말만 거른다. 거래일 판정은 이미
`bot.market`/`exchange_calendars` 가 하므로 여기에 두 번째 달력을 두지
않는다(#38).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# (시작 hh, 시작 mm, 종료 hh, 종료 mm, 국면키, 한글 라벨).
# 종료가 시작보다 이르면 자정을 넘는 구간(익일까지).
# 출처: https://stock.naver.com/notice/153 (사용자 2026-09-16 제시).
_KRX: tuple[tuple[int, int, int, int, str, str], ...] = (
    (8, 0, 9, 0, "preopen", "개장전"),
    (9, 0, 15, 30, "regular", "정규장"),
    (15, 30, 16, 0, "regular_close", "정규장 마감"),
    (16, 0, 20, 0, "after", "애프터마켓"),
    (20, 0, 8, 0, "after_close", "애프터마켓 마감"),
)
_NXT: tuple[tuple[int, int, int, int, str, str], ...] = (
    (7, 10, 8, 0, "preopen", "개장전"),
    (8, 0, 8, 50, "pre", "프리마켓"),
    (8, 50, 9, 0, "pre_close", "프리마켓 마감"),
    (9, 0, 15, 20, "regular", "정규장"),
    (15, 20, 15, 40, "regular_close", "정규장 마감"),
    (15, 40, 20, 0, "after", "애프터마켓"),
    (20, 0, 7, 10, "after_close", "애프터마켓 마감"),
)
VENUES = {"KRX": _KRX, "NXT": _NXT}


def _table(venue: str):
    t = VENUES.get((venue or "").upper())
    if t is None:
        raise ValueError(f"알 수 없는 거래소: {venue!r} (KRX|NXT)")
    return t


def now_kst() -> datetime:
    return datetime.now(KST)


def _in(span, h: int, m: int) -> bool:
    sh, sm, eh, em, _k, _l = span
    cur, start, end = h * 60 + m, sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end        # 자정 넘김


def phase(venue: str, now: datetime | None = None) -> tuple[str, str]:
    """(국면키, 한글 라벨). 주말이면 ('closed', '휴장'). 순수(now 주입 가능)."""
    t = now or now_kst()
    if t.tzinfo is not None:
        t = t.astimezone(KST)
    if t.weekday() >= 5:
        return ("closed", "휴장")
    for span in _table(venue):
        if _in(span, t.hour, t.minute):
            return (span[4], span[5])
    return ("closed", "휴장")


def in_after_market(venue: str, now: datetime | None = None) -> bool:
    """그 거래소의 **애프터마켓 체결 창** 안인가. 'after_close'(장 끝난 뒤
    표시만 유지되는 구간)는 False — 체결이 없으므로 재수집할 이유가 없다."""
    return phase(venue, now)[0] == "after"


def in_pre_market(venue: str, now: datetime | None = None) -> bool:
    """프리마켓 체결 창. KRX 는 프리마켓이 **없다**(개장전은 호가접수만) —
    공지 153 에 프리마켓 구간이 NXT 에만 있기 때문이고, 그래서 여기서
    KRX 는 항상 False 다(#43 — 없는 것을 있는 척하지 않는다)."""
    return phase(venue, now)[0] == "pre"


def in_extended_window(venue: str, now: datetime | None = None) -> bool:
    """수집기가 '지금 재스캔할 가치가 있나' 를 묻는 창 = 프리 ∪ 애프터.
    ⚠️ NXT 는 `pre_close`(08:50~09:00) 도 포함한다 — 그 구간엔 단일가
    매매체결이 일어나 값이 바뀐다(옛 `_in_kr_extended_window` 의 08:00~09:00
    과 동일 동작. 창을 좁히면 그 10분이 조용히 빈다, #171)."""
    k = phase(venue, now)[0]
    return k in ("pre", "pre_close", "after")


def window_label(venue: str) -> str:
    """화면 각주용 — 그 거래소의 체결 창을 사람이 읽는 문장으로. 창을
    화면에 리터럴로 적으면 이 표와 갈라진다(#55)."""
    parts = []
    for sh, sm, eh, em, k, lb in _table(venue):
        if k in ("pre", "after"):
            parts.append(f"{lb} {sh:02d}:{sm:02d}–{eh:02d}:{em:02d}")
    return " · ".join(parts) + " KST"
