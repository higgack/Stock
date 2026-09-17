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
    화면에 리터럴로 적으면 이 표와 갈라진다(#55).

    ⚠️ **`in_extended_window` 와 같은 구간을 말해야 한다.** 옛 판은 `pre`·
    `after` 만 적어 NXT 프리마켓을 `08:00–08:50` 으로 줄여 말했는데, 수집기는
    `pre_close`(08:50~09:00)에도 돌고 있었다 — 08:55 에 빈 화면이 "그 창에
    확인해 주세요" 라며 **이미 닫혔다고** 말한다(독립 리뷰 2026-09-16 H5 ·
    #55 설명이 코드와 어긋나면 버그). 그래서 라벨도 같은 술어에서 파생하고,
    맞닿은 구간은 하나로 잇는다.
    """
    spans = []
    for sh, sm, eh, em, k, lb in _table(venue):
        if k not in ("pre", "pre_close", "after"):
            continue
        s_, e_ = sh * 60 + sm, eh * 60 + em
        if spans and spans[-1][1] == s_:        # 맞닿았으면 이어 붙인다
            spans[-1] = (spans[-1][0], e_, spans[-1][2])
        else:
            spans.append((s_, e_, "프리마켓" if k.startswith("pre") else lb))
    parts = [f"{lb} {s_ // 60:02d}:{s_ % 60:02d}–{e_ // 60:02d}:{e_ % 60:02d}"
             for s_, e_, lb in spans]
    return " · ".join(parts) + " KST"


def exclusive_venue(now: datetime | None = None) -> tuple[str, str]:
    """지금 **체결 창이 열려 있는 거래소가 하나뿐인가** — (거래소, 갈래 라벨).

    ⚠️ 왜 필요한가(사용자 2026-09-17 "이 NXT 랑 KRX 애프터랑 안겹치는것도
    많을텐데. NXT 에 등록안된 기업들도 많기 때문에"): 네이버 폴링 응답에는
    거래소를 이름으로 가르는 필드가 없다(#373b 실측). 그래서 시간외 체결이
    어느 거래소 것인지는 **필드로는 못 가른다**. 그런데 **창**으로는 갈린다 —
    KRX 는 체결 창이 애프터마켓(16:00~20:00)뿐이라, 그 밖의 NXT 체결 창
    (프리 08:00~09:00 · 애프터 15:40~16:00)에 붙는 시간외 체결은 **정의상
    NXT** 다. 그 구간에서 모은 종목은 'NXT 에서 거래되는 종목' 의 하한이다.

    ⚠️ 이건 **원천이 밝힌 창**(공지 153)에서 나온 연역이지 우리가 귀속을 잰
    것이 아니다 — 원천이 창을 또 바꾸면 이 판정도 같이 틀린다(#165). 그래서
    창 표와 **같은 술어**(`in_extended_window`)에서 파생시키고 거래소 이름을
    여기 열거하지 않는다(#24 — 거래소가 늘면 저절로 따라온다).

    갈래(#82 — 처방이 다르다): 'exclusive'=그 거래소 확정 · 'overlap'=둘 다
    열려 귀속 불가 · 'closed'=체결 창 밖(잴 것이 없다).
    """
    open_ = [v for v in VENUES if in_extended_window(v, now)]
    if len(open_) == 1:
        return (open_[0], "exclusive")
    return ("", "overlap" if open_ else "closed")


# ── 합집합(거래소 중립) ─────────────────────────────────────────────
# 사용자 2026-09-17 "합치기로 하자 … 미국처럼 장후로": KR 시간외 보드를 미국
# `usprepost` 처럼 **한 장**으로 합쳤다. 근거는 창이다 — KRX 체결 창
# (16:00–20:00)은 NXT 창(08:00–09:00 · 15:40–20:00)의 **진부분집합**이라
# KRX 보드가 한 행도 더 내놓을 수 없었고, 남은 고유 산출은 하루 80분의 빈
# 화면과 **재지 않은 거래소 라벨**뿐이었다(#373b 응답에 거래소를 이름으로
# 가르는 필드가 없다 · #165·#375 우리 구현의 결과를 시장 사실로 적지 말 것).
#
# ⚠️ 그래서 보드는 거래소를 고르지 않고 **두 창의 합집합**을 쓴다. 오늘은
# NXT 창과 같아 동작이 한 글자도 안 바뀌지만(부분집합이므로), 원천이 KRX
# 창을 20:00 너머로 늘리면 거기서 조용히 빠지지 않는다. 거래소 이름을 여기
# 열거하지 않으므로 거래소가 늘어도 저절로 따라온다(#24).

def union_extended_window(now: datetime | None = None) -> bool:
    """어느 거래소든 체결 창이 열려 있나 = 시간외 보드가 재스캔할 창."""
    return any(in_extended_window(v, now) for v in VENUES)


def union_session(now: datetime | None = None) -> str:
    """합집합 세션 — 'pre'·'post'·''(창 밖). 보드 라벨·행 필터가 쓴다.

    ⚠️ 프리와 애프터가 동시에 열린 거래소 조합은 오늘 없다(KRX 엔 프리마켓이
    없다). 그런 날이 오면 'pre' 가 이긴다 — 임의 선택이므로 여기 적어 둔다.
    """
    ph = [phase(v, now)[0] for v in VENUES]
    if any(k in ("pre", "pre_close") for k in ph):
        return "pre"
    return "post" if "after" in ph else ""


def _union_spans() -> list[tuple[int, int, set]]:
    """전 거래소 체결 구간을 분 단위로 합쳐 정렬 (시작, 끝, 국면종류들)."""
    raw: list[tuple[int, int, str]] = []
    for v in VENUES:
        for sh, sm, eh, em, k, _lb in _table(v):
            if k in ("pre", "pre_close", "after"):
                raw.append((sh * 60 + sm, eh * 60 + em, "pre"
                            if k.startswith("pre") else "after"))
    raw.sort()
    out: list[tuple[int, int, set]] = []
    for s_, e_, kind in raw:
        if out and s_ <= out[-1][1]:            # 겹치거나 맞닿으면 잇는다
            prev = out[-1]
            out[-1] = (prev[0], max(prev[1], e_), prev[2] | {kind})
        else:
            out.append((s_, e_, {kind}))
    return out


def union_window_label() -> str:
    """합집합 창을 사람이 읽는 문장으로 — 화면 각주가 창을 리터럴로 적으면
    이 표와 갈라진다(#55). `union_extended_window` 와 **같은 구간**을 말한다.

    ⚠️ 한 구간이 두 국면(프리·애프터)에 걸치면 이름을 고를 수 없으므로
    '시간외' 라고만 적는다 — 없는 구분을 지어내지 않는다(#34·#165).
    """
    parts = []
    for s_, e_, kinds in _union_spans():
        lb = ("프리마켓" if kinds == {"pre"} else
              "애프터마켓" if kinds == {"after"} else "시간외")
        parts.append(f"{lb} {s_ // 60:02d}:{s_ % 60:02d}–"
                     f"{e_ // 60:02d}:{e_ % 60:02d}")
    return " · ".join(parts) + " KST"
