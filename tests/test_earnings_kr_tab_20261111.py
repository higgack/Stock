"""다가오는 실적 — 한국 탭이 말없이 사라지던 것(사용자 2026-11-11 "이것도 한국은 안나오네").

① yfinance `Earnings Date` 는 추정 **범위**(시작·끝)로 오기도 한다. 옛 판은 첫
   원소만 보고 지났으면 버려, 범위가 오늘을 걸치는 종목이 빠졌다.
② 0건이면 탭이 통째로 사라져 '일정이 없다' 와 '못 받았다' 가 같은 화면이었다.
"""
from __future__ import annotations

from datetime import date, datetime

import bot.market_overview as mo

T = date(2026, 11, 11)
CUT = date(2027, 2, 9)


def _cal(*ds):
    return {"Earnings Date": [datetime.strptime(d, "%Y-%m-%d").date() for d in ds]}


def test_range_straddling_today_is_kept():
    r = mo._earning_row_from_cal(_cal("2026-11-05", "2026-11-14"), "005930.KS",
                                 "삼성전자", T, CUT)
    assert r and r["date"] == "2026-11-14"


def test_first_in_window_date_is_used():
    r = mo._earning_row_from_cal(_cal("2026-12-01", "2026-12-05"), "X", "X", T, CUT)
    assert r["date"] == "2026-12-01"


def test_tally_names_each_branch():
    t: dict = {}
    mo._earning_row_from_cal(_cal("2026-10-28"), "a", "a", T, CUT, tally=t)
    mo._earning_row_from_cal(_cal("2027-04-01"), "b", "b", T, CUT, tally=t)
    mo._earning_row_from_cal({}, "c", "c", T, CUT, tally=t)
    mo._earning_row_from_cal(None, "d", "d", T, CUT, tally=t)
    mo._earning_row_from_cal({"Earnings Date": ["nope"]}, "e", "e", T, CUT, tally=t)
    mo._earning_row_from_cal(_cal("2026-11-20"), "f", "f", T, CUT, tally=t)
    assert t == {"past": 1, "beyond": 1, "no_cal": 2, "bad": 1, "ok": 1}


def test_note_text_says_why_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path)
    (tmp_path / "finnhub").mkdir()
    assert mo.earnings_scan_note("kr") == ""                 # 기록이 없으면 침묵
    mo._write_earn_note("kr", 450, {"past": 300, "no_cal": 150}, 90)
    n = mo.earnings_scan_note("kr")
    assert "450종목" in n and "지난 날짜만 300" in n and "일정 없음 150" in n


def test_kr_scan_writes_the_note(tmp_path, monkeypatch):
    """배선 — 스캔이 실제로 갈래를 세어 남긴다(#20 헬퍼만 재면 배선을 못 잡는다)."""
    import types
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(mo, "_kr_earnings_universe",
                        lambda: [("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스")])
    cals = {"005930.KS": _cal("2026-10-30"), "000660.KS": {}}
    monkeypatch.setattr(mo, "yf", types.SimpleNamespace(
        Ticker=lambda tk: types.SimpleNamespace(calendar=cals[tk])))
    import bot.finviz_client as fc
    monkeypatch.setattr(fc, "yf_paused", lambda: False)
    monkeypatch.setattr(mo, "date", types.SimpleNamespace(today=lambda: T))
    assert mo.fetch_earnings_calendar_kr(90) == []
    n = mo.earnings_scan_note("kr")
    assert "2종목" in n and "지난 날짜만 1" in n and "일정 없음 1" in n, n


def test_market_page_keeps_kr_tab_with_reason(tmp_path, monkeypatch):
    import bot.dashboard as d
    monkeypatch.setattr(mo, "_CACHE_DIR", tmp_path)
    (tmp_path / "finnhub").mkdir()
    us = [{"symbol": "AAPL", "date": "2026-11-12", "name": ""}]
    html = d._render_market_page({"earnings": us})
    assert 'data-etab="kr"' not in html                      # 사유가 없으면 종전대로
    mo._write_earn_note("kr", 450, {"past": 450}, 90)
    html = d._render_market_page({"earnings": us})
    assert 'data-etab="kr">한국 (0)' in html
    assert "한국 실적 예정일을 표시할 수 없습니다" in html and "지난 날짜만 450" in html
    assert 'data-etab="us">미국 (1)' in html
