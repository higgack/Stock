"""오늘 휴장 표시 — 메인 지수 카드 · 업종 등락 위젯(한국·미국·아시아) (사용자 2026-11-23).

일본 근로감사의 날에 니케이 카드가 ▼5.06%(11-20 등락)를, 일본 업종 위젯이 '2026-11-23 15:02'
(우리가 받아 온 시각)를 찍어 휴장인지 수집이 늦은 건지 못 갈랐다(#43·#52)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from bot import dashboard as d
from bot import market_holiday as mh
from bot.market_overview import CARD_ASIA, CARD_EU, CARD_FX, CARD_FUTURES, CARD_US, CARD_US2

K = timezone(timedelta(hours=9))
JP_HOLIDAY = datetime(2026, 11, 23, 19, 2, tzinfo=K)       # 일본 근로감사의 날(월)
MV = {"up": [{"name": "반도체", "pct": 1.0}], "down": [{"name": "조선", "pct": -1.0}],
      "ts": "2026-11-23 15:02", "source": "네이버 업종(랭킹)"}


@pytest.fixture
def at(monkeypatch):
    def _set(t):
        monkeypatch.setattr(mh, "_now", lambda: t)
    return _set


# ── 판정 ──────────────────────────────────────────────────────────────────────
def test_weekday_holiday_names_the_last_session():
    h = mh.holiday("XTKS", JP_HOLIDAY)
    assert h == {"date": date(2026, 11, 23), "prev": date(2026, 11, 20), "exchange": "도쿄거래소"}
    assert mh.holiday("XKRX", JP_HOLIDAY) is None


def test_holiday_is_judged_on_the_exchange_local_date():
    """미국 추수감사절(11-26 ET) — KST 13:00 은 아직 ET 11-25, 15:00 은 ET 11-26."""
    assert mh.holiday("XNYS", datetime(2026, 11, 26, 13, 0, tzinfo=K)) is None
    assert mh.holiday("XNYS", datetime(2026, 11, 26, 15, 0, tzinfo=K))["prev"] == date(2026, 11, 25)


def test_weekend_unknown_calendar_and_out_of_range_say_nothing():
    assert mh.holiday("XTKS", datetime(2026, 11, 22, 12, 0, tzinfo=K)) is None, "주말은 표시 안 함"
    assert mh.holiday("XSHG", datetime(2027, 3, 3, 12, 0, tzinfo=K)) is None, "달력 밖은 모른다"
    assert mh.holiday("XNOPE", JP_HOLIDAY) is None
    assert mh.holiday(None, JP_HOLIDAY) is None


def test_every_index_card_row_has_a_calendar_and_futures_fx_have_none():
    """지수 카드 행이 매핑에서 빠지면 그 행만 조용히 배지가 없다(#24) — 전수로 잰다."""
    for _, tk in CARD_ASIA + CARD_US + CARD_US2 + CARD_EU:
        assert mh.xcal_for_symbol(tk), tk
    for _, tk in CARD_FUTURES + CARD_FX:
        assert mh.xcal_for_symbol(tk) is None, tk


# ── 화면 배선 ─────────────────────────────────────────────────────────────────
def _row(html: str, label: str) -> str:
    i = html.index(label)
    return html[i:html.index("</tr>", i)]


def test_market_card_badges_only_the_closed_exchange(at):
    at(JP_HOLIDAY)
    yf = {"nvi:.N225": {"close": 56436.65, "change": -3000, "pct": -5.06},
          "nvd:KOSPI": {"close": 4708.54, "change": -160, "pct": -3.31}}
    h = d._render_market_card("한국 & 아시아", CARD_ASIA, yf)
    assert "hol-badge" in _row(h, "JP 니케이 225") and "마지막 거래일은 11-20" in _row(h, "JP 니케이 225")
    assert "hol-badge" not in _row(h, "KR 코스피")
    assert "hol-badge" not in _row(h, "TW 대만 가권")


def test_market_card_badges_rows_without_values_too(at):
    at(JP_HOLIDAY)
    h = d._render_market_card("한국 & 아시아", CARD_ASIA, {})
    assert "hol-badge" in _row(h, "JP 니케이 225")


@pytest.mark.parametrize("market,heading", [
    ("JP", "🇯🇵 일본 업종 등락 TOP 10"), ("CN", "🇨🇳 중국 업종 등락 TOP 10"),
    ("HK", "🇭🇰 홍콩 업종 등락 TOP 10"), ("TW", "🇹🇼 대만 업종 등락 TOP 10"),
])
def test_asia_page_notes_only_the_closed_market(at, market, heading):
    at(JP_HOLIDAY)
    page = d._render_asia_page({f"{m.lower()}_sector_movers": MV for m in ("JP", "CN", "HK", "TW")})
    i = page.index(heading)
    seg = page[i:page.index("sm-wrap", i)]
    note = "오늘(11-23) 휴장" in seg and "마지막 거래일 11-20" in seg
    assert note is (market == "JP"), seg


def test_kr_and_us_widgets_carry_the_note(at):
    at(datetime(2026, 12, 25, 18, 0, tzinfo=K))   # 성탄절 — KST 18:00 = ET 12-25 04:00, 둘 다 현지 휴장
    kr = d._render_sector_movers(MV)
    us = d._render_us_sector_movers(MV)
    assert "오늘(12-25) 휴장" in kr and "한국거래소 휴장" in kr
    assert "오늘(12-25) 휴장" in us and "미국 증시 휴장" in us
    at(JP_HOLIDAY)
    assert "hol-badge" not in d._render_sector_movers(MV)
    assert "hol-badge" not in d._render_us_sector_movers(MV)


def test_note_survives_an_empty_widget_with_a_reason(at):
    """원천이 막혀 사유만 보이는 날에도 휴장이면 말한다 — 휴장이 빈 이유일 수 있다."""
    at(datetime(2026, 12, 25, 12, 0, tzinfo=K))
    kr = d._render_sector_movers({"up": [], "down": [], "reason": "HTTP 404"})
    assert "오늘(12-25) 휴장" in kr


def test_badge_failure_does_not_kill_the_widget(at, monkeypatch):
    at(JP_HOLIDAY)
    monkeypatch.setattr(mh, "section_note", lambda m, now=None: 1 / 0)
    h = d._render_etf_sector_movers(MV, "🇯🇵 일본 업종 등락 TOP 10", market="JP")
    assert "반도체" in h and "hol-badge" not in h


def test_badge_classes_are_styled():
    css = d._MARKET_CSS
    assert ".hol-badge{" in css and ".hol-note{" in css


def test_taiwan_widget_is_wired_to_its_own_calendar(at):
    """대만 행헌기념일(12-25) — 일본·중국·홍콩은 개장. 시장 인자를 빼거나 바꾸면 갈린다."""
    at(datetime(2026, 12, 25, 18, 0, tzinfo=K))
    page = d._render_asia_page({f"{m.lower()}_sector_movers": MV for m in ("JP", "CN", "HK", "TW")})
    i = page.index("🇹🇼 대만 업종 등락 TOP 10")
    assert "대만거래소 휴장" in page[i:page.index("sm-wrap", i)]
    j = page.index("🇯🇵 일본 업종 등락 TOP 10")
    assert "hol-badge" not in page[j:page.index("sm-wrap", j)]


def test_out_of_range_is_said_once_not_every_render(caplog, monkeypatch):
    """달력 범위 밖(XSHG 는 2026-12-31 까지)은 배지 없이 모른다 — 조용히 사라지지 않게 한 번은
    경고하고(독립 리뷰: 2027 춘절이 말없이 빠진다), 렌더마다 되풀이하지 않는다."""
    import logging
    monkeypatch.setattr(mh, "_OUT_OF_RANGE", set())
    with caplog.at_level(logging.INFO, logger="bot.market_holiday"):
        assert mh.holiday("XSHG", datetime(2027, 3, 3, 12, 0, tzinfo=K)) is None
        assert mh.holiday("XSHG", datetime(2027, 3, 4, 12, 0, tzinfo=K)) is None
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "XSHG" in msgs[0] and "갱신" in msgs[0], msgs
