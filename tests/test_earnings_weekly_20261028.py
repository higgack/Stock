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
