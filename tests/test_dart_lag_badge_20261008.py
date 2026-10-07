"""DART 헤더 '⚠️ 지연' 배지 — 거래일 자정~첫 공시 사이의 매일 거짓 경보(실수 #440).

사용자 2026-10-08 00:50 화면: `최신 공시 2026-10-07(286건) ⚠️ 지연`. 옛 판은 기준을
'오늘 이하 마지막 KR 거래일' 로만 잡아, 목요일(거래일) 00:50 에 아직 한 건도 없는
목요일 공시를 요구했다 — 거래일마다 자정부터 첫 공시가 수집될 때까지 뜬다.
옛 코드에서 같은 시각·같은 데이터로 재현한 뒤 고쳤다(§Pre-commit 9).

달력은 테스트가 정한다(샌드박스엔 exchange_calendars 가 없어 실물 달력은
None — 그 갈래는 따로 잰다). 2026-10-09(금)은 한글날 휴장으로 둔다.
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

import bot.dashboard as d
import bot.kr_session as ks
import bot.market_calendar as mcal

KST = dt.timezone(dt.timedelta(hours=9))
_HOLIDAYS = {"2026-10-09"}


def _last_session(_mkt, ds):
    t = dt.date.fromisoformat(ds)
    while t.weekday() >= 5 or t.isoformat() in _HOLIDAYS:
        t -= dt.timedelta(days=1)
    return t.isoformat()


@pytest.fixture
def cal(monkeypatch):
    monkeypatch.setattr(mcal, "last_session_on_or_before", _last_session)


def _fx(day: str) -> dict:
    ymd = day.replace("-", "")
    return {day: [{"rcept_no": f"{ymd}000001", "date": ymd, "corp_name": "회사1",
                   "stock_code": "005930", "url": "#",
                   "report_nm": "단일판매ㆍ공급계약체결", "category": "계약",
                   "detail": ["계약금액: 1억"]}]}


def _header(by_date, now) -> str:
    html = d._render_dart_feed_page(by_date, now=now)[0]
    m = re.search(r'<p class="sub">출처 DART\(OpenDART\).*?</p>', html, re.S)
    assert m, "DART 헤더 줄을 못 찾았다"
    return m.group(0)


def _visible(fragment: str) -> str:
    """툴팁(title=)을 걷어낸 **보이는 글자**만 — 사유가 툴팁에만 있으면 안 된다(#228)."""
    return re.sub(r"<[^>]+>", "", re.sub(r'\stitle="[^"]*"', "", fragment))


def _kst(y, mo, da, h, mi):
    return dt.datetime(y, mo, da, h, mi, tzinfo=KST)


def test_dawn_on_a_trading_day_is_not_a_lag(cal):
    """사용자 화면 그대로 — 목(거래일) 00:50, 최신 공시 = 수. 지연이 아니다."""
    hdr = _header(_fx("2026-10-07"), _kst(2026, 10, 8, 0, 50))
    assert "⚠️ 지연" not in hdr, _visible(hdr)


def test_before_close_the_previous_session_is_enough(cal):
    hdr = _header(_fx("2026-10-07"), _kst(2026, 10, 8, 15, 29))
    assert "⚠️ 지연" not in hdr, _visible(hdr)


def test_after_close_today_is_required_and_said_visibly(cal):
    hdr = _header(_fx("2026-10-07"), _kst(2026, 10, 8, 15, 30))
    seen = _visible(hdr)
    assert "⚠️ 지연" in seen
    # 무엇이 늦었는지 **보이는 줄**이 말한다 — 툴팁만이면 사용자가 묻는다(#228).
    assert "오늘(2026-10-08)" in seen and "정규장 마감(15:30)" in seen, seen


def test_a_missing_completed_session_is_named(cal):
    """화 이후 수집이 죽은 경우 — 목 10:00 기준은 수(직전 거래일)다."""
    hdr = _header(_fx("2026-10-06"), _kst(2026, 10, 8, 10, 0))
    seen = _visible(hdr)
    assert "⚠️ 지연" in seen and "거래일 2026-10-07" in seen, seen


def test_monday_dawn_after_a_holiday_and_weekend(cal):
    """금(한글날)·주말을 건너 월 08:00 — 직전 거래일은 목이다."""
    hdr = _header(_fx("2026-10-08"), _kst(2026, 10, 12, 8, 0))
    assert "⚠️ 지연" not in hdr, _visible(hdr)


def test_on_a_holiday_the_last_session_is_the_reference(cal):
    noon = _kst(2026, 10, 9, 12, 0)
    assert "⚠️ 지연" not in _header(_fx("2026-10-08"), noon)
    seen = _visible(_header(_fx("2026-10-07"), noon))
    assert "⚠️ 지연" in seen and "거래일 2026-10-08" in seen, seen


def test_no_calendar_means_no_claim(monkeypatch):
    """달력이 없으면 판정 불가 — 지연이라고도 정상이라고도 단정하지 않는다(#54)."""
    monkeypatch.setattr(mcal, "last_session_on_or_before", lambda *_a: None)
    hdr = _header(_fx("2026-09-01"), _kst(2026, 10, 8, 16, 0))
    assert "⚠️ 지연" not in hdr


def test_a_utc_clock_is_judged_in_kst(cal):
    """UTC 로 들어온 시각도 KST 날짜·시각으로 판정한다(규칙 10a)."""
    utc = dt.timezone.utc
    dawn = dt.datetime(2026, 10, 7, 15, 50, tzinfo=utc)     # = 10-08 00:50 KST
    assert "⚠️ 지연" not in _header(_fx("2026-10-07"), dawn)
    after = dt.datetime(2026, 10, 8, 7, 0, tzinfo=utc)       # = 10-08 16:00 KST
    assert "오늘(2026-10-08)" in _visible(_header(_fx("2026-10-07"), after))


def test_the_close_boundary_comes_from_the_session_table(cal, monkeypatch):
    """경계는 `kr_session` 표에서 온다 — 대시보드에 15:30 을 리터럴로 박으면
    표와 갈라진다(#38). 표가 정오 마감이라고 말하면 12:00 부터 오늘을 요구한다."""
    monkeypatch.setattr(ks, "regular_close", lambda venue="KRX": (12, 0))
    assert "⚠️ 지연" not in _header(_fx("2026-10-07"), _kst(2026, 10, 8, 11, 59))
    seen = _visible(_header(_fx("2026-10-07"), _kst(2026, 10, 8, 12, 0)))
    assert "정규장 마감(12:00)" in seen, seen


def test_regular_close_reads_each_venue_table():
    assert ks.regular_close("KRX") == (15, 30)
    assert ks.regular_close("NXT") == (15, 20)
    with pytest.raises(ValueError):
        ks.regular_close("XYZ")


def test_no_items_at_all_draws_no_badge(cal):
    """빈 아카이브는 '최신 공시 —' 로 이미 말한다 — 배지를 덧붙이지 않는다."""
    hdr = _header({}, _kst(2026, 10, 8, 16, 0))
    assert "⚠️ 지연" not in hdr
