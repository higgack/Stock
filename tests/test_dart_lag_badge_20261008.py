"""DART 헤더 '⚠️ 지연' 배지 — 거래일 자정~첫 공시 사이의 매일 거짓 경보(실수 #440).

사용자 2026-10-08 00:50 화면: `최신 공시 2026-10-07(286건) ⚠️ 지연`. 옛 판은 기준을
'오늘 이하 마지막 KR 거래일' 로만 잡아, 목요일(거래일) 00:50 에 아직 한 건도 없는
목요일 공시를 요구했다 — 거래일마다 자정부터 첫 공시가 수집될 때까지 뜬다.
옛 코드에서 같은 시각·같은 데이터로 재현한 뒤 고쳤다(§Pre-commit 9).

달력은 테스트가 정한다 — 실물 달력(exchange_calendars)의 판·휴장 목록에
결과가 기대지 않게 한다(첫 판은 "샌드박스엔 exchange_calendars 가 없다" 고 적었는데
venv 엔 깔려 있었다 — 독립 리뷰, #286). 2026-10-09(금)은 한글날 휴장으로 둔다(실물
달력도 같다). 달력이 없는 갈래는 따로 잰다.

2026-10-08 독립 리뷰 반영: 기준일은 `kr_session.last_closed_session` 한 곳에서 오고
일일 감사(`dart_mcap_audit` 창 결측 거래일)도 같은 함수를 쓴다(#38·#147) — 감사만
옛 기준이면 같은 자정 거짓 경보가 결산에 남는다.
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

import html as _html

import bot.dashboard as d
import bot.kr_session as ks
import bot.market_calendar as mcal
import bot.scripts.dart_mcap_audit as audit

KST = dt.timezone(dt.timedelta(hours=9))
_HOLIDAYS = {"2026-10-09"}


def _last_session(_mkt, ds):
    # 한국 달력을 물어야 한다 — 다른 시장 달력으로 판정하면 휴장일이 갈린다.
    assert _mkt == "KR", _mkt
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


# ── 툴팁 사유 · 색 (2026-10-08 독립 리뷰) ─────────────────────────────────────

def _badge_span(hdr: str) -> str:
    m = re.search(r'<span[^>]*>⚠️ 지연[^<]*</span>', hdr)
    assert m, "지연 배지 span 을 못 찾았다"
    return m.group(0)


def _title(span: str) -> str:
    m = re.search(r'title="([^"]*)"', span)
    return _html.unescape(m.group(1)) if m else ""


def test_tooltip_on_a_trading_day_names_the_session_in_progress(cal):
    """거래일 정규장 마감 전 — 직전 거래일을 요구하는 이유가 '오늘은 진행 중' 이다."""
    span = _badge_span(_header(_fx("2026-10-06"), _kst(2026, 10, 8, 10, 0)))
    why = _title(span)
    assert "마지막 거래일(2026-10-07)" in why and "정규장 마감(15:30) 전까지 진행 중" in why, why


def test_tooltip_on_a_holiday_does_not_claim_a_session_in_progress(cal):
    """휴장일엔 '오늘 거래일은 마감 전까지 진행 중' 이 거짓이다 — 첫 판이 그렇게 적었다."""
    span = _badge_span(_header(_fx("2026-10-07"), _kst(2026, 10, 9, 12, 0)))
    why = _title(span)
    assert "오늘(2026-10-09)은 거래일이 아닙니다" in why, why
    assert "진행 중" not in why, why
    assert "거래일 2026-10-08" in _visible(span)


def test_tooltip_after_close_says_today_ended(cal):
    why = _title(_badge_span(_header(_fx("2026-10-07"), _kst(2026, 10, 8, 16, 0))))
    assert "오늘 거래일의 정규장이 끝났는데" in why, why


def test_badge_colour_is_the_palette_token_that_passes_aa(cal, monkeypatch):
    """리터럴 #f5a623 은 라이트 배경 대비 1.91:1(AA 미달, #355) — 팔레트 토큰을 쓴다.
    키 없음 줄도 같은 자리에 붙으므로 같이 잰다."""
    from bot import css_contrast as cc
    import bot.dart_feed as df
    monkeypatch.setattr(df, "_dart_api_key", lambda: "")
    hdr = _header(_fx("2026-10-07"), _kst(2026, 10, 8, 16, 0))
    assert "#f5a623" not in hdr, hdr
    assert hdr.count("color:var(--pending)") == 2, hdr      # 지연 + 키 없음
    blocks = dict(cc.palette_blocks(d._SCREENER_CSS))
    for sel, pal in cc.palette_blocks(d._SCREENER_CSS):
        if "--pending" in pal and "--card" in pal:
            r = cc.contrast_ratio(pal["--pending"], pal["--card"])
            assert r is not None and r >= 4.5, (sel, r)
    assert any("--pending" in pal for pal in blocks.values())


# ── 기준일 단일 출처 `last_closed_session` ───────────────────────────────────

@pytest.mark.parametrize("now,want", [
    (_kst(2026, 10, 8, 0, 50), "2026-10-07"),     # 사용자 화면 시각
    (_kst(2026, 10, 8, 15, 29), "2026-10-07"),
    (_kst(2026, 10, 8, 15, 30), "2026-10-08"),
    (_kst(2026, 10, 9, 12, 0), "2026-10-08"),     # 한글날(휴장)
    (_kst(2026, 10, 10, 9, 0), "2026-10-08"),     # 토
    (_kst(2026, 10, 12, 8, 0), "2026-10-08"),     # 월, 마감 전
    (_kst(2026, 10, 12, 16, 0), "2026-10-12"),
])
def test_last_closed_session(cal, now, want):
    assert ks.last_closed_session(now) == want


def test_last_closed_session_reads_the_clock_in_kst(cal):
    utc = dt.timezone.utc
    assert ks.last_closed_session(dt.datetime(2026, 10, 8, 6, 29, tzinfo=utc)) == "2026-10-07"
    assert ks.last_closed_session(dt.datetime(2026, 10, 8, 6, 30, tzinfo=utc)) == "2026-10-08"
    # naive 는 KST 로 본다(규칙 10a — 서버 로컬타임에 기대지 않는다)
    assert ks.last_closed_session(dt.datetime(2026, 10, 8, 15, 30)) == "2026-10-08"


def test_last_closed_session_uses_the_venue_table(cal):
    """NXT 정규장은 15:20 에 끝난다 — 경계는 그 거래소 표에서 온다(#38)."""
    t = _kst(2026, 10, 8, 15, 25)
    assert ks.last_closed_session(t, venue="NXT") == "2026-10-08"
    assert ks.last_closed_session(t) == "2026-10-07"


def test_last_closed_session_without_a_calendar_claims_nothing(monkeypatch):
    monkeypatch.setattr(mcal, "last_session_on_or_before", lambda *_a: None)
    assert ks.last_closed_session(_kst(2026, 10, 8, 16, 0)) is None


# ── 일일 감사: 창 결측 거래일이 화면 배지와 같은 기준 ───────────────────────

def _window(today: dt.date, skip=()) -> dict:
    out: dict = {}
    for i in range(30):
        day = (today - dt.timedelta(days=i)).isoformat()
        if _last_session("KR", day) == day and day not in skip:
            out.update(_fx(day))
    return out


def test_audit_does_not_require_today_before_close(cal):
    """목 08:00 — 오늘치가 아직 없는 건 수집 구멍이 아니다(배지와 같은 기준)."""
    by = _window(dt.date(2026, 10, 8), skip={"2026-10-08"})
    g = audit._window_gaps(by, _kst(2026, 10, 8, 8, 0))
    assert g["known"] and g["ref"] == "2026-10-07"
    assert g["miss_sess"] == [] and g["pending"] == ["2026-10-08"], g


def test_audit_requires_today_after_close(cal):
    by = _window(dt.date(2026, 10, 8), skip={"2026-10-08"})
    g = audit._window_gaps(by, _kst(2026, 10, 8, 16, 0))
    assert g["miss_sess"] == ["2026-10-08"] and g["pending"] == [], g


def test_audit_still_names_a_past_hole_and_skips_holidays(cal):
    by = _window(dt.date(2026, 10, 9), skip={"2026-10-06"})
    g = audit._window_gaps(by, _kst(2026, 10, 9, 12, 0))     # 한글날
    assert g["miss_sess"] == ["2026-10-06"] and g["pending"] == [], g
    assert "2026-10-09" in g["missing"] and g["nonsess"] >= 9, g   # 휴일 + 주말


def test_audit_without_a_calendar_does_not_split(monkeypatch):
    monkeypatch.setattr(mcal, "last_session_on_or_before", lambda *_a: None)
    g = audit._window_gaps({}, _kst(2026, 10, 8, 16, 0))
    assert g["known"] is False and g["miss_sess"] == [] and len(g["missing"]) == 30


def _run_audit(monkeypatch, capsys, by, now) -> list[str]:
    monkeypatch.setattr(audit, "_now", lambda: now)
    monkeypatch.setattr(d, "_load_dart_feed_data", lambda days_back=30: by)
    monkeypatch.setattr(audit, "_fullscan_age", lambda: "(스텁)")
    audit.audit_dart()
    return capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("hour,window_ok,badge_ok", [(8, True, True), (16, False, False)])
def test_audit_window_line_and_badge_line_agree(cal, monkeypatch, capsys,
                                                hour, window_ok, badge_ok):
    """배선 — 감사가 창 판정과 화면 렌더에 **같은 시각**을 넘긴다. 렌더가 실제
    시계를 쓰면(2026-10 이 아닌 날) 배지 줄이 창 줄과 갈라진다. 날짜는 실제 오늘과
    겹치지 않게 2027-03 으로 둔다(3/4 목)."""
    by = _window(dt.date(2027, 3, 4), skip={"2027-03-04"})
    lines = _run_audit(monkeypatch, capsys, by, _kst(2027, 3, 4, hour, 0))
    win = [ln for ln in lines if "창 결측 **거래일**" in ln]
    badge = [ln for ln in lines if "지연 배지 미표시" in ln]
    assert len(win) == 1 and len(badge) == 1, lines
    assert win[0].startswith("✅" if window_ok else "❌"), win[0]
    assert badge[0].startswith("✅" if badge_ok else "❌"), badge[0]
    if hour == 8:
        assert "아직 요구하지 않는 거래일 1일 ['2027-03-04']" in win[0], win[0]
    else:
        assert "1일 ['2027-03-04']" in win[0], win[0]


def test_page_stamp_is_written_in_kst(cal):
    """'페이지 생성' 도 KST 로 찍는다 — UTC 시각이 들어오면 옛 판은 UTC 날짜·시각을
    그대로 찍어 배지(KST 판정)와 한 줄에서 두 시계가 섞였다(규칙 10a, 독립 리뷰)."""
    utc = dt.timezone.utc
    hdr = _header(_fx("2026-10-07"), dt.datetime(2026, 10, 7, 15, 50, tzinfo=utc))
    assert "페이지 생성 2026-10-08 00:50" in _visible(hdr), _visible(hdr)
