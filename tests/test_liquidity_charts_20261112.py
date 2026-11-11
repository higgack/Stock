"""유동성 추이 차트 — 같은 6개월 창 · 출처 줄에 측정기간 · 한·미 CPI 첫 달=100 (사용자 2026-11-12)."""
import re
from datetime import date, timedelta

from bot import dashboard as d
from bot import macro_snapshot as ms


def _ser(start: date, days: int, step: int = 1):
    return [{"d": (start + timedelta(days=i)).strftime("%Y.%m.%d"), "v": 100.0 + i}
            for i in range(0, days, step)]


def _foot(html, title):
    i = html.index(title)
    return re.search(r'<div class="foot">([^<]*)</div>', html[i:]).group(1)


def test_every_chart_shares_one_six_month_window_and_states_it():
    end = date(2026, 11, 6)
    dep = {"source": "금융투자협회",
           "deposit_series": _ser(end - timedelta(days=300), 298),     # 11.04 끝
           "credit_series": _ser(end - timedelta(days=300), 298),
           "equity_fund_series": _ser(end - timedelta(days=60), 58),   # 원천이 짧게 줌
           "vkospi_series": _ser(end - timedelta(days=300), 301)}      # 11.06 끝(가장 늦음)
    html = d._render_deposit_charts(dep)
    cut = (end - timedelta(days=d._DEP_WINDOW_DAYS)).strftime("%Y.%m.%d")
    f_dep = _foot(html, "고객예탁금 추이")
    f_vk = _foot(html, "VKOSPI 추이")
    f_ef = _foot(html, "주식형펀드 추이")
    assert f_dep.startswith("출처: 금융투자협회 (") and f"({cut}~" in f_dep
    assert f_vk == f"출처: KIS ({cut}~2026.11.06)"
    # 짧게 온 계열은 늘리지 않고 실제 기간을 그대로 적는다.
    assert f_ef.startswith("출처: 금융투자협회 (2026.09.07~")


def test_window_does_not_touch_input():
    dep = {"deposit_series": _ser(date(2026, 1, 1), 300)}
    n = len(dep["deposit_series"])
    d._dep_window(dep)
    assert len(dep["deposit_series"]) == n


def test_cpi_is_rebased_on_one_axis_and_paired_by_month(monkeypatch):
    import bot.fear_greed_client as fg
    monkeypatch.setattr(fg, "fetch_fear_greed", lambda *a, **k: {})
    us = ms._SparkVals([290.0, 300.0, 303.0], ["2026-08-01", "2026-09-01", "2026-10-01"])
    kr = ms._SparkVals([120.0, 121.2, 122.4], ["202609", "202610", "202611"])
    ch = ms._build_charts({"us_cpi": us, "kr_cpi": kr})["inflation"]
    assert ch["labels"] == ["2026-09", "2026-10"] and ch["rebased"]
    assert ch["us_cpi"] == [100.0, 101.0] and ch["kr_cpi"] == [100.0, 101.0]
    html = d._render_macro_charts_row({"charts": {"inflation": ch}})
    assert "첫 달 = 100" in html and "(2026-09~2026-10)" in html
    assert "미국 CPI +1.0%" in html
    # 한 축 — 오른쪽 축 눈금이 없다(좌축 계열만).
    assert 'text-anchor="start"' not in html.split("한·미 소비자물가")[1].split("chart-card")[0]


def test_rates_chart_foot_states_definition_and_period():
    html = d._render_macro_charts_row({"charts": {"rates_fx": {
        "labels": ["2026-10", "2026-11"], "us_10y": [4.2, 5.0], "kr_10y": [3.0, 3.85],
        "spread": [1.2, 1.15], "kr_label": "한국 국고채 10년"}}})
    assert "월말값(이번 달은 최신) (2026-10~2026-11)" in html


def test_mixed_date_notations_are_compared_as_dates():
    """'YYYY.MM.DD' 와 'YYYY-MM-DD' 가 섞여도 같은 날짜 축으로 자른다('-' < '.' 문자 비교 함정)."""
    dep = {"deposit_series": [{"d": "2026.09.12", "v": 1.0}],
           "credit_kospi_series": [{"d": "2026-09-10", "v": 1.0}, {"d": "2026-09-11", "v": 2.0}]}
    assert len(d._dep_window(dep)["credit_kospi_series"]) == 2


def test_one_side_empty_cpi_draws_nothing(monkeypatch):
    import bot.fear_greed_client as fg
    monkeypatch.setattr(fg, "fetch_fear_greed", lambda *a, **k: {})
    us = ms._SparkVals([300.0, 303.0], ["2026-09-01", "2026-10-01"])
    assert "inflation" not in ms._build_charts({"us_cpi": us})
