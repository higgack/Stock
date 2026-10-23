"""OECD·PortWatch 지연 규약을 실측 공표 시각에 맞춘다 (2026-10-23).

실측(원천이 말한 시각):
  · OECD 2026-09분 = 2026-10-07 (기간 말 +7일) — SDMX `updatedAfter` 이분 탐색
  · PortWatch 레이어 `dataLastEditDate` = 화요일 10:51 UTC, 관측 마지막 = 직전 일요일
판정은 규약 숫자가 아니라 `macro_cadence.judge` 의 동작으로 잰다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from bot import macro_cadence as mc

_OECD_IDS = ["OECD:CLI:G20", "OECD:CLI:KOR", "OECD:CLI:USA",
             "OECD:LTR:JPN", "OECD:LTR:DEU"]
_PW_IDS = ["PW:HORMUZ", "PW:MALACCA", "PW:KOREA", "PW:BABELMANDEB"]


@pytest.mark.parametrize("sid", _OECD_IDS)
def test_oecd_not_stale_before_and_around_the_measured_release(sid):
    # 9월분은 10-07 에 올라왔다 — 그 전(8월분만 있음)엔 지연이 아니다
    for d in (date(2026, 10, 1), date(2026, 10, 7), date(2026, 10, 10)):
        assert not mc.judge(sid, "2026-08", today=d)["stale"], d
    # 올라온 뒤(9월분)엔 언제든 정상
    for d in (date(2026, 10, 8), date(2026, 10, 25), date(2026, 11, 5)):
        assert not mc.judge(sid, "2026-09", today=d)["stale"], d


@pytest.mark.parametrize("sid", _OECD_IDS)
def test_oecd_missing_release_is_caught_within_three_weeks(sid):
    # 공표가 안 오면 기간 말 +3주 안에 잡는다 (옛 45·35일 규약은 +39~49일)
    assert mc.judge(sid, "2026-08", today=date(2026, 10, 21))["stale"]
    # 반대 증거: 같은 날 9월분이 있으면 정상
    assert not mc.judge(sid, "2026-09", today=date(2026, 10, 21))["stale"]


@pytest.mark.parametrize("sid", _PW_IDS)
def test_portwatch_weekly_cycle_never_false_alarms(sid):
    """관측 = 일요일까지, 게시 = 화요일(KST 저녁). 4주 동안 매일 정상이어야 한다."""
    start = date(2026, 10, 20)                      # 화요일
    for i in range(28):
        today = start + timedelta(days=i)
        d = today
        while d.weekday() != 1 or d >= today:       # 오늘 이전 가장 최근 화요일(당일 저녁 전은 미게시)
            d -= timedelta(days=1)
        actual = d - timedelta(days=2)              # 그 화요일에 게시된 일요일
        assert actual.weekday() == 6
        assert not mc.judge(sid, actual.isoformat(), today=today)["stale"], today


@pytest.mark.parametrize("sid", _PW_IDS)
def test_portwatch_two_missed_weekly_releases_are_caught(sid):
    # 반대 증거: 주 단위 판정이라 한 주 이상 벌어져야 잡힌다 — 10-13·10-20 게시를
    # 놓쳐 10-04 에 멈춘 채 10-31 이 되면 잡는다 (아무것도 안 재는 규약이 아님)
    assert mc.judge(sid, "2026-10-04", today=date(2026, 10, 31))["stale"]
    # 같은 날 정상 게시본(10-25 까지)이 있으면 정상
    assert not mc.judge(sid, "2026-10-25", today=date(2026, 10, 31))["stale"]
