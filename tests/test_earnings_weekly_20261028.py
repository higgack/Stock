"""주간 어닝 캘린더(`bot.earnings_weekly`) — 사용자 2026-10-28.

원천은 월간 달력과 같은 함수(`earnings_calendar.us_month`·`fetch_month`)라
그 경계에서 스텁한다(#399 — 리졸버 한 단만 갈면 운영 상태가 단언을 뒤집는다).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date

import pytest

from bot import earnings_calendar as ec
from bot import earnings_weekly as ew


def _us(sym, d, hour="bmo", rev=None, eps=None):
    return {"symbol": sym, "name": "", "date": d, "hour": hour,
            "revenue_estimate": rev, "eps_estimate": eps}


@pytest.fixture
def stub(monkeypatch):
    state = {"us": {}, "reason": {}, "favs": (set(), set())}
    calls = []

    def us_month(y, m):
        calls.append((y, m))
        return state["us"].get((y, m), []), state["reason"].get((y, m), "")

    monkeypatch.setattr(ec, "us_month", us_month)
    monkeypatch.setattr(ec, "_us_name_map", lambda: {"AAPL": "Apple Inc."})
    monkeypatch.setattr(ew, "_favorites", lambda: state["favs"])
    monkeypatch.setattr(ew, "_asof", lambda mon: "")
    state["calls"] = calls
    return state


def test_week_start_weekday_and_weekend():
    assert ew.week_start(date(2026, 10, 28)) == date(2026, 10, 26)   # 수
    assert ew.week_start(date(2026, 10, 26)) == date(2026, 10, 26)   # 월
    assert ew.week_start(date(2026, 10, 30)) == date(2026, 10, 26)   # 금
    assert ew.week_start(date(2026, 10, 31)) == date(2026, 11, 2)    # 토 → 다음 주
    assert ew.week_start(date(2026, 11, 1)) == date(2026, 11, 2)     # 일 → 다음 주


def test_us_week_spanning_two_months_slots_and_order(stub):
    stub["us"][(2026, 9)] = [_us("OLD", "2026-09-25"),              # 지난주 — 빠져야 한다
                             _us("MSFT", "2026-09-30", "amc", rev=7e10)]
    stub["us"][(2026, 10)] = [_us("AAPL", "2026-10-01", "amc", rev=9e10, eps=1.5),
                              _us("TINY", "2026-10-01", "amc", rev=1e6),
                              _us("FAV", "2026-10-01", "amc", rev=5e6),
                              _us("STAR", "2026-10-01", "amc", rev=1e3),
                              _us("TBD", "2026-10-02", ""),
                              _us("NEXT", "2026-10-05")]
    stub["favs"] = ({"FAV", "STAR"}, {"STAR"})
    w = ew.build_week(date(2026, 9, 28))
    assert set(stub["calls"]) == {(2026, 9), (2026, 10)}
    assert w["total"] == 6 and w["n_fav"] == 2
    thu = w["days"][3]
    assert [x["key"] for x in thu["slots"]["post"]] == ["STAR", "FAV", "AAPL", "TINY"]
    assert thu["slots"]["post"][2]["name"] == "Apple Inc."
    assert [x["key"] for x in w["days"][4]["slots"]["tbd"]] == ["TBD"]
    assert [x["key"] for x in w["days"][2]["slots"]["post"]] == ["MSFT"]
    assert [x["key"] for x in thu["favs"]] == ["STAR", "FAV"]


def test_more_button_hides_beyond_show(stub):
    stub["us"][(2026, 10)] = [_us(f"S{i}", "2026-10-27", rev=100 - i) for i in range(ew._SHOW + 2)]
    html = ew.render_page(date(2026, 10, 27), today=date(2026, 10, 28))
    assert f"전체 {ew._SHOW + 2} · 표시 {ew._SHOW}" in html
    assert "+2건 더보기" in html
    assert html.count('data-extra="1"') == 2


def test_us_only_no_korea(stub):
    """사용자 2026-10-28 "한국은 실적발표일 공개를 안하면 그냥 빼줘" — 시장 토글·한국 경로 없음."""
    stub["us"][(2026, 10)] = [_us("AAPL", "2026-10-27")]
    html = ew.render_page(date(2026, 10, 27), today=date(2026, 10, 28))
    assert "🇰🇷" not in html and "KIND" not in html
    # 링크가 쓰는 market= 은 미국 하나뿐이다(월간 달력도 미국 탭으로 연다 — 독립 리뷰 #2)
    assert set(re.findall(r"market=(\w+)", html)) == {"us"}
    assert "🇺🇸 미국" in html


def test_empty_week_names_the_reason(stub):
    stub["reason"][(2026, 10)] = "key"
    html = ew.render_page(date(2026, 10, 27), today=date(2026, 10, 28))
    assert "이 주에 일정이 없습니다 — FINNHUB_API_KEY" in html


def test_escaping(stub):
    stub["us"][(2026, 10)] = [_us("<b>X", "2026-10-27")]
    html = ew.render_page(date(2026, 10, 27), today=date(2026, 10, 28))
    assert "<B>X" not in html and "&lt;B&gt;X" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_inline_js_parses(stub, tmp_path):
    html = ew.render_page(date(2026, 10, 27), today=date(2026, 10, 28))
    for i, js in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        f = tmp_path / f"s{i}.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_routes_and_links_wired():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    srv = (root / "bot" / "dashboard_server.py").read_text(encoding="utf-8")
    tree = ast.parse(srv)
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_handle_earnings_week" in names
    assert 'raw == "/earnings_week"' in srv
    assert 'href="earnings_week"' in (root / "bot" / "dashboard.py").read_text(encoding="utf-8")
    assert 'href="earnings_week"' in (root / "bot" / "earnings_calendar.py").read_text(encoding="utf-8")


# ── 독립 리뷰 2026-10-28 반영분 ─────────────────────────────────────────────

def test_mixed_month_reasons_prefer_the_actionable_one(stub):
    """9월 0건 + 10월 호출 실패 → '0건으로 응답' 이 아니라 호출 실패를 말한다(#82)."""
    stub["reason"][(2026, 9)] = "empty"
    stub["reason"][(2026, 10)] = "http"
    html = ew.render_page(date(2026, 9, 28), today=date(2026, 9, 30))
    assert "원천 호출이 실패했습니다" in html
    assert "0건으로 응답" not in html
    # 순서를 바꿔도 같다
    stub["reason"] = {(2026, 9): "http", (2026, 10): "empty"}
    assert "원천 호출이 실패했습니다" in ew.render_page(date(2026, 9, 28), today=date(2026, 9, 30))


def test_partial_failure_note_and_no_receipt_stamp(stub, monkeypatch):
    stub["us"][(2026, 10)] = [_us("AAPL", "2026-10-01")]
    stub["reason"][(2026, 9)] = "http"
    monkeypatch.setattr(ew, "_asof", lambda mon: "2026-09-01 00:00 KST")
    html = ew.render_page(date(2026, 9, 28), today=date(2026, 9, 30))
    assert "일부 일정이 빠졌을 수 있습니다" in html
    assert "수신" not in html                       # 못 받은 주에 옛 캐시 시각을 적지 않는다
    stub["reason"] = {}
    assert "2026-09-01 00:00 KST 수신" in ew.render_page(date(2026, 9, 28), today=date(2026, 9, 30))


def test_monthly_link_opens_the_us_month_of_that_week(stub):
    html = ew.render_page(date(2026, 9, 28), today=date(2026, 10, 28))
    assert 'href="earnings?month=2026-09&amp;market=us"' in html


@pytest.mark.parametrize("bad", [date(1, 1, 1), date(9999, 12, 31), date(1999, 12, 31)])
def test_out_of_range_week_falls_back_to_today(stub, bad):
    html = ew.render_page(bad, today=date(2026, 10, 28))
    assert "2026-10-26 ~ 2026-10-30" in html
    assert set(stub["calls"]) == {(2026, 10)}         # 엉뚱한 해를 원천에 묻지 않는다


def test_default_today_is_us_eastern(stub, monkeypatch):
    monkeypatch.setattr(ew, "et_today", lambda: date(2026, 10, 31))   # ET 토요일
    assert "2026-11-02 ~ 2026-11-06" in ew.render_page()


def test_non_numeric_revenue_does_not_break_the_page(stub):
    stub["us"][(2026, 10)] = [_us("AAA", "2026-10-27", rev="N/A"),
                              _us("BBB", "2026-10-27", rev=5e9)]
    w = ew.build_week(date(2026, 10, 26))
    assert [x["key"] for x in w["days"][1]["slots"]["pre"]] == ["BBB", "AAA"]


def test_watchlist_matches_dash_and_dot_tickers(stub):
    stub["us"][(2026, 10)] = [_us("BRK.B", "2026-10-27")]
    stub["favs"] = ({ew._norm("BRK-B")}, {ew._norm("brk-b")})
    x = ew.build_week(date(2026, 10, 26))["days"][1]["slots"]["pre"][0]
    assert x["fav"] and x["star"]


def test_checkpoint_cells_name_their_day(stub):
    stub["us"][(2026, 10)] = [_us("FAV", "2026-10-28", eps=1.1)]
    stub["favs"] = ({"FAV"}, set())
    html = ew.render_page(date(2026, 10, 26), today=date(2026, 10, 28))
    cks = re.findall(r'<div class="ew-ck">(.*?)</div></div>', html, re.S)
    assert len(cks) == 5
    assert all(f'class="ew-ckd">{d}' in c for c, d in zip(
        cks, ["월요일 10-26", "화요일 10-27", "수요일 10-28", "목요일 10-29", "금요일 10-30"]))
    assert "EPS 추정 1.1" in cks[2]


# ── 발표치(사용자 2026-10-29 "어닝발표한것들은 발표치도 같이") ─────────────────

def test_checkpoint_shows_actuals_next_to_estimates(stub):
    stub["us"][(2026, 10)] = [
        dict(_us("BEAT", "2026-10-27", "bmo", rev=4.4e9, eps=3.3079),
             eps_actual=3.42, revenue_actual=4.5e9),
        dict(_us("MISS", "2026-10-27", "amc", rev=4.2e9, eps=7.49),
             eps_actual=7.1, revenue_actual=4.0e9),
        _us("WAIT", "2026-10-27", "amc", rev=3.8e9, eps=4.17)]   # 발표 전 — null
    stub["favs"] = ({"BEAT", "MISS", "WAIT"}, set())
    html = ew.render_page(date(2026, 10, 26), today=date(2026, 10, 28))
    lines = {re.search(r"<b>(\w+)</b>", l).group(1): l
             for l in re.findall(r'<div class="ew-ckl">(.*?)</div>', html, re.S)}
    assert "EPS 추정 3.3079 · 발표 3.42" in lines["BEAT"]
    assert "매출 추정 $4.4B · 발표 $4.5B" in lines["BEAT"]
    assert lines["BEAT"].count('class="ew-up"') == 2 and "ew-dn" not in lines["BEAT"]
    assert lines["MISS"].count('class="ew-dn"') == 2 and "ew-up" not in lines["MISS"]
    assert "발표" not in lines["WAIT"] and "EPS 추정 4.17" in lines["WAIT"]


def test_us_month_keeps_actuals_and_refetches_old_cache(tmp_path, monkeypatch):
    """옛 판 캐시(발표치 칸 없음)를 6시간 믿으면 fix 가 화면에 안 닿는다(#18·#21b)."""
    import json
    monkeypatch.setattr(ec, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(ec, "_api_key", lambda: "k")
    monkeypatch.setattr(ec, "_us_name_map", lambda: {})
    (tmp_path / "2026-10.json").write_text(json.dumps(
        [{"symbol": "IQV", "date": "2026-10-27", "eps_estimate": 3.3}]))   # 옛 판 · 방금 씀
    calls = []

    class _Ok:
        def raise_for_status(self):
            return None

        def json(self):
            calls.append(1)
            return {"earningsCalendar": [{"symbol": "IQV", "date": "2026-10-27",
                                          "epsEstimate": 3.3, "epsActual": 3.42,
                                          "revenueEstimate": 4.4e9, "revenueActual": None}]}
    monkeypatch.setattr(ec.requests, "get", lambda *a, **k: _Ok())
    rows, why = ec.us_month(2026, 10)
    assert calls, "옛 판 캐시를 그대로 믿었다"
    assert rows[0]["eps_actual"] == 3.42 and rows[0]["revenue_actual"] is None
    calls.clear()
    ec.us_month(2026, 10)
    assert not calls, "새 판 캐시를 다시 부른다"


def test_live_month_ttl_is_one_hour(monkeypatch):
    monkeypatch.setattr(ec, "_utc_today", lambda: date(2026, 11, 3))
    assert ec._cache_ttl(2026, 11, [1]) == ec._LIVE_TTL_SEC == 3600
    assert ec._cache_ttl(2026, 10, [1]) == 3600          # 7일 전이 속한 달(지난주 발표치)
    assert ec._cache_ttl(2026, 12, [1]) == ec._CACHE_TTL_SEC
    assert ec._cache_ttl(2026, 12, []) == ec._EMPTY_TTL_SEC


def test_outage_after_schema_bump_serves_old_rows(tmp_path, monkeypatch):
    """판 교체 직후 원천 장애 — 옛 판 캐시(기한 안)를 빈칸 대신 쓰고 사유는 http 로 남긴다."""
    import json
    monkeypatch.setattr(ec, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(ec, "_api_key", lambda: "k")
    (tmp_path / "2026-10.json").write_text(json.dumps([{"symbol": "IQV", "date": "2026-10-27"}]))

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ec.requests, "get", boom)
    rows, why = ec.us_month(2026, 10)
    assert [r["symbol"] for r in rows] == ["IQV"] and why == "http"


def test_actual_without_estimate_is_labelled(stub):
    stub["us"][(2026, 10)] = [dict(_us("X", "2026-10-27"), eps_actual=1.2, revenue_actual=0)]
    stub["favs"] = ({"X"}, set())
    html = ew.render_page(date(2026, 10, 26), today=date(2026, 10, 28))
    line = re.findall(r'<div class="ew-ckl">(.*?)</div>', html, re.S)[0]
    assert "EPS 발표 1.2" in line and "매출 발표 $0" in line
    assert "ew-up" not in line and "ew-dn" not in line


# ── 발표 후 종가(발표일·다음 거래일) — 사용자 2026-10-28 ─────────────────────
from datetime import datetime  # noqa: E402


def _et(y, m, d, hh=18, mm=0):
    return datetime(y, m, d, hh, mm)


def _wk(favs_rows):
    """build_week 모양의 최소 재료 — 관심종목 줄만."""
    return {"days": [{"date": "x", "favs": favs_rows}]}


def _row(sym, d, eps_act=1.0, rev_act=None, fav=True):
    return {"sym": sym, "date": d, "eps": 0.9, "eps_act": eps_act, "rev": None, "rev_act": rev_act,
            "slot": "post", "star": False, "fav": fav}


@pytest.fixture
def closes(monkeypatch, tmp_path):
    monkeypatch.setattr(ew, "_closes_path", lambda: tmp_path / "closes.json")
    monkeypatch.setattr("bot.finviz_client.yf_paused", lambda: False)
    calls = []
    bars = {}

    def dl(syms, start, end):
        calls.append((list(syms), start, end))
        return {s: dict(bars[s]) for s in syms if s in bars}, ""

    return {"calls": calls, "bars": bars, "dl": dl}


def test_closes_skip_weekend_for_friday_report(closes):
    """금요일 발표 → 다음날 종가는 토요일이 아니라 다음 월요일 봉."""
    closes["bars"]["AAA"] = {"2026-10-29": 8.0, "2026-10-30": 10.0, "2026-11-02": 11.5}
    r = _row("AAA", "2026-10-30")
    ew._attach_closes(_wk([r]), now=_et(2026, 11, 2), download=closes["dl"])
    assert r["px"]["d0"]["d"] == "2026-10-30" and r["px"]["d0"]["px"] == 10.0
    assert r["px"]["d1"]["d"] == "2026-11-02" and r["px"]["d1"]["px"] == 11.5
    # 등락률: 발표일 = 직전 거래일 대비(8→10 = +25%) · 다음날 = 발표일 대비(10→11.5 = +15%)
    assert r["px"]["d0"]["chg"] == pytest.approx(25.0)
    assert r["px"]["d1"]["chg"] == pytest.approx(15.0)
    assert ew._px_bit(r) == ('발표일 10-30 $10.00 <span class="ew-up">(+25.00%)</span> · '
                             '다음날 11-02 $11.50 <span class="ew-up">(+15.00%)</span>')


def test_closes_today_bar_is_not_a_close_before_market_end(closes):
    """발표일이 오늘이고 마감 전이면 장중 값이라 '대기' — 디스크에도 굳히지 않는다."""
    closes["bars"]["AAA"] = {"2026-10-28": 99.0}
    r = _row("AAA", "2026-10-28")
    ew._attach_closes(_wk([r]), now=_et(2026, 10, 28, 10, 0), download=closes["dl"])
    assert r["px"]["d0"] == {"wait": True} and r["px"]["d1"] == {"wait": True}
    assert "종가 대기" in ew._px_bit(r)
    assert "AAA" not in (ew._load_closes().get("bars") or {})


def test_closes_d0_confirmed_d1_waits(closes):
    closes["bars"]["AAA"] = {"2026-10-27": 25.0, "2026-10-28": 20.0}
    r = _row("AAA", "2026-10-28")
    ew._attach_closes(_wk([r]), now=_et(2026, 10, 28, 17, 0), download=closes["dl"])
    assert r["px"]["d0"] == {"d": "2026-10-28", "px": 20.0, "chg": pytest.approx(-20.0)}
    assert r["px"]["d1"] == {"wait": True}


def test_closes_unreported_and_non_favorites_are_not_asked(closes):
    closes["bars"]["AAA"] = {"2026-10-27": 5.0}
    pending = _row("AAA", "2026-10-27", eps_act=None)          # 아직 발표치 없음
    future = _row("FUT", "2026-10-30")                          # 아직 안 온 날
    ew._attach_closes(_wk([pending, future]), now=_et(2026, 10, 28), download=closes["dl"])
    assert closes["calls"] == []
    assert "px" not in pending and "px" not in future


def test_closes_cached_after_first_fetch_and_missing_is_said(closes):
    closes["bars"]["AAA"] = {"2026-10-26": 5.0, "2026-10-27": 5.0, "2026-10-28": 6.0}
    r = _row("AAA", "2026-10-27")
    ew._attach_closes(_wk([r]), now=_et(2026, 10, 28, 18), download=closes["dl"])
    assert len(closes["calls"]) == 1 and closes["calls"][0][0] == ["AAA"]
    ew._attach_closes(_wk([_row("AAA", "2026-10-27")]), now=_et(2026, 10, 28, 18, 30), download=closes["dl"])
    assert len(closes["calls"]) == 1                            # 이미 굳은 종가는 다시 묻지 않는다
    # 원천에 없는 종목은 마감이 지났는데 비었으므로 '미수신' — 대기와 구별
    m = _row("NOPE", "2026-10-27")
    ew._attach_closes(_wk([m]), now=_et(2026, 10, 28, 19), download=closes["dl"])
    assert m["px"]["d0"] == {"miss": True} and "종가 미수신" in ew._px_bit(m)


def test_closes_failure_backs_off_and_does_not_raise(closes):
    n = []

    def boom(syms, start, end):
        n.append(1)
        raise RuntimeError("down")

    r = _row("AAA", "2026-10-27")
    ew._attach_closes(_wk([r]), now=_et(2026, 10, 28, 18, 0), download=boom)
    ew._attach_closes(_wk([_row("AAA", "2026-10-27")]), now=_et(2026, 10, 28, 18, 5), download=boom)
    assert len(n) == 1                                          # 10분 안엔 다시 안 묻는다
    ew._attach_closes(_wk([_row("AAA", "2026-10-27")]), now=_et(2026, 10, 28, 18, 11), download=boom)
    assert len(n) == 2
    assert r["px"]["d0"] == {"miss": True}


def test_closes_d1_far_bar_is_not_used(closes):
    """Yahoo 가 사이 봉을 빠뜨려 한참 뒤 봉이 첫 봉이면 다음날 종가로 쓰지 않는다."""
    closes["bars"]["AAA"] = {"2026-10-19": 1.0, "2026-10-20": 1.0, "2026-10-28": 9.0}
    r = _row("AAA", "2026-10-20")
    ew._attach_closes(_wk([r]), now=_et(2026, 10, 28, 18), download=closes["dl"])
    assert r["px"]["d1"] == {"miss": True}


def test_render_page_shows_post_report_closes(stub, closes, monkeypatch):
    stub["us"][(2026, 10)] = [dict(_us("AAA", "2026-10-27", "amc", rev=1e9, eps=1.0),
                                   eps_actual=1.2, revenue_actual=1.1e9)]
    stub["favs"] = ({"AAA"}, set())
    closes["bars"]["AAA"] = {"2026-10-26": 50.0, "2026-10-27": 50.0, "2026-10-28": 53.25}
    monkeypatch.setattr(ew, "_et_now", lambda: _et(2026, 10, 28, 18))
    monkeypatch.setattr("bot.holdings_news._yf_daily", closes["dl"])
    html = ew.render_page(date(2026, 10, 26), today=date(2026, 10, 28))
    assert "발표일 10-27 $50.00 (+0.00%) · 다음날 10-28 $53.25" in html
    assert '<span class="ew-up">(+6.50%)</span>' in html


def test_closes_change_needs_adjacent_prior_bar_and_falls_back_to_price_only(closes):
    """직전 봉이 멀거나(Yahoo 가 사이를 빠뜨림) 없으면 등락률만 비우고 종가는 남긴다 — 두 세션 등락을 하루로 적지 않게."""
    closes["bars"]["AAA"] = {"2026-10-14": 1.0, "2026-10-27": 10.0, "2026-10-28": 9.0}
    r = _row("AAA", "2026-10-27")
    ew._attach_closes(_wk([r]), now=_et(2026, 10, 28, 18), download=closes["dl"])
    assert r["px"]["dp"] == {"miss": True}
    assert "chg" not in r["px"]["d0"] and r["px"]["d1"]["chg"] == pytest.approx(-10.0)
    assert ew._px_bit(r) == '발표일 10-27 $10.00 · 다음날 10-28 $9.00 <span class="ew-dn">(-10.00%)</span>'
    # 직전 봉이 없으면 다시 묻는다(창 시작을 7일 앞으로 — 월요일 휴장이면 지난 금요일까지)
    assert closes["calls"] and closes["calls"][0][1] == "2026-10-20"


def test_closes_refetch_when_only_prior_close_is_missing(closes):
    """발표일·다음날 종가는 이미 디스크에 있고 **직전 거래일 종가만** 없으면(옛 판이 3일 창으로 받은 캐시) 다시 묻는다."""
    closes["bars"]["AAA"] = {"2026-10-27": 10.0, "2026-10-28": 11.0}
    ew._attach_closes(_wk([_row("AAA", "2026-10-27")]), now=_et(2026, 10, 28, 18), download=closes["dl"])
    assert len(closes["calls"]) == 1
    closes["bars"]["AAA"]["2026-10-26"] = 8.0
    r = _row("AAA", "2026-10-27")
    ew._attach_closes(_wk([r]), now=_et(2026, 10, 28, 18, 11), download=closes["dl"])
    assert len(closes["calls"]) == 2
    assert r["px"]["d0"]["chg"] == pytest.approx(25.0)
