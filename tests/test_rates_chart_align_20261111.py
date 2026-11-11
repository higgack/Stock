"""한·미 국채 금리 차트 — 같은 정의(월말값)·같은 달·띠 분리 (2026-11-11 사용자
"한미금리가 닿아있는데..그래프 스케일이 좀 이상한데").

옛 판: 미 10Y 스파크 = FRED 월평균, 한 10Y = ECOS 그 달 마지막 값 → 금리차가 두
정의를 섞었고, 두 배열을 위치로 짝지어 미 10월과 한 11월이 한 칸에 놓였으며,
우축 금리차 선이 좌축 금리 선과 겹쳐 없는 관계로 읽혔다."""
import re

from bot import macro_snapshot as ms


class _Rules:
    _FRED_DAILY_SIDS = frozenset({"DGS10"})
    _fred_fail: dict = {}

    @staticmethod
    def _fred_ttl_h(_sid):
        return 1.0

    @staticmethod
    def _fred_recently_failed(_k):
        return False


def test_splice_spot_replaces_same_month_and_appends_new(monkeypatch):
    monkeypatch.setattr(ms, "_fred_cache_rules", lambda: _Rules)
    sp = ms._SparkVals([4.1, 4.2, 5.16], ["2026-09-30", "2026-10-31", "2026-11-07"])
    out = ms._splice_spot(sp, {"value": 4.98, "time": "2026-11-10"}, "DGS10")
    assert list(out) == [4.1, 4.2, 4.98] and out.dates[-1] == "2026-11-10"
    sp2 = ms._SparkVals([4.1, 4.2], ["2026-09-30", "2026-10-31"])
    out2 = ms._splice_spot(sp2, {"value": 4.98, "time": "2026-11-10"}, "DGS10")
    assert list(out2) == [4.2, 4.98] and out2.dates == ["2026-10-31", "2026-11-10"]
    # 월간 계열은 건드리지 않는다.
    sp3 = ms._SparkVals([3.0], ["2026-10-01"])
    assert ms._splice_spot(sp3, {"value": 9.9, "time": "2026-11-10"}, "CPIAUCSL") is sp3


def test_eop_only_when_asked(monkeypatch, tmp_path):
    """월말값은 한·미 금리 차트 전용 — 카드 스파크는 월평균(사용자 2026-11-12 되돌림)."""
    monkeypatch.setattr(ms, "_fred_cache_rules", lambda: _Rules)
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr("bot.env_keys.env_key", lambda k: "x")
    seen = []

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"observations": [{"date": "2026-10-01", "value": "4.2"}]}

    def _get(url, params=None, timeout=None):
        seen.append(params.get("aggregation_method"))
        return _R()
    monkeypatch.setattr(ms.requests, "get", _get)
    ms._fred_monthly("DGS10")
    ms._fred_monthly("DGS10", eop=True)
    assert seen == [None, "eop"]                     # 사본 이름이 달라 둘 다 물었다


def test_rates_chart_pairs_by_month_not_position():
    us = ms._SparkVals([4.0, 4.1, 4.2], ["2026-08-01", "2026-09-01", "2026-10-01"])
    kr = ms._SparkVals([3.0, 3.1, 3.2], ["202609", "202610", "202611"])
    ch = ms._build_charts({"us_10y": us, "kr_10y": kr})["rates_fx"]
    assert ch["labels"] == ["2026-09", "2026-10"]
    assert ch["us_10y"] == [4.1, 4.2] and ch["kr_10y"] == [3.0, 3.1]
    assert ch["spread"] == [1.1, 1.1]


def test_split_puts_right_axis_series_in_lower_band():
    from bot.dashboard import _svg_line_chart
    lab = [f"2026-{m:02d}" for m in range(1, 7)]
    svg = _svg_line_chart(lab, [
        {"name": "a", "color": "#111111", "data": [4.0, 4.2, 4.4, 4.6, 4.8, 5.0], "axis": "L"},
        {"name": "s", "color": "#222222", "data": [0.2, 0.3, 0.4, 0.6, 1.0, 1.3], "axis": "R"},
    ], split=True)

    def ys(color):
        pts = re.search(rf'points="([^"]+)" fill="none" stroke="{color}"', svg).group(1)
        return [float(p.split(",")[1]) for p in pts.split()]
    top, bot = ys("#111111"), ys("#222222")
    # 띠가 갈린다: 좌축 선의 가장 낮은 점이 우축 선의 가장 높은 점보다 위(작은 y).
    assert max(top) < min(bot)
    # 기본(split 없음)은 옛 동작 그대로 — 두 선이 같은 높이대를 쓴다.
    svg0 = _svg_line_chart(lab, [
        {"name": "a", "color": "#111111", "data": [4.0, 4.2, 4.4, 4.6, 4.8, 5.0], "axis": "L"},
        {"name": "s", "color": "#222222", "data": [0.2, 0.3, 0.4, 0.6, 1.0, 1.3], "axis": "R"},
    ])
    assert svg0 != svg


def test_rates_card_uses_split():
    from bot.dashboard import _render_macro_charts_row
    html = _render_macro_charts_row({"charts": {"rates_fx": {
        "labels": ["2026-10", "2026-11"], "us_10y": [4.2, 5.0], "kr_10y": [3.0, 3.85],
        "spread": [1.2, 1.15], "kr_label": "한국 국고채 10년"}}})
    pts = re.findall(r'points="([^"]+)" fill="none" stroke="(#[0-9a-f]+)"', html)
    y = {c: [float(p.split(",")[1]) for p in ps.split()] for ps, c in pts}
    assert max(y["#42a5f5"] + y["#26c6da"]) < min(y["#ab47bc"])


def test_snapshot_e2e_card_end_equals_headline_and_chart_aligns(tmp_path, monkeypatch):
    """수집기를 통째로 태운다(#20) — 카드 스파크 끝 = 카드 값, 차트는 달로 짝짓는다."""
    import bot.market_overview as mo
    import bot.naver_marketindex as nm
    from tests.test_slow_cards_20260925 import _Resp
    monkeypatch.setenv("FRED_API_KEY", "x" * 8)
    monkeypatch.setattr(ms, "_CACHE_DIR", tmp_path / "snap")
    (tmp_path / "snap").mkdir()
    monkeypatch.setattr(ms, "_fetch_macro_naver_values", lambda sids: {})
    monkeypatch.setattr(ms, "_yf_monthly_batch", lambda tk: {})
    monkeypatch.setattr(ms, "_yf_daily_1mo_batch", lambda tk: {})
    for fn in ("fetch_commodity_spark", "fetch_naver_index_history",
               "fetch_naver_crypto_history", "fetch_naver_fx_history"):
        monkeypatch.setattr(nm, fn, lambda *a, **k: [])
    monkeypatch.setattr(ms, "_customs_series", lambda key: {"points": [], "src": ""})
    # 한국 창이 한 달 늦다(10월까지) — 위치로 짝지으면 미 11월이 한 10월 칸에 앉는다.
    kr = [("20260831", 3.60), ("20260930", 3.70), ("20261031", 3.95)]
    monkeypatch.setattr(ms, "_ecos_series", lambda key: kr if key == "kr10y" else [])
    seen = {}

    def fake_get(url, params=None, timeout=None):
        sid = (params or {}).get("series_id")
        if sid == "DGS10":
            seen.setdefault("aggs", []).append(params.get("aggregation_method"))
            # FRED 월말값(내림차순) — 11월은 진행 중이라 지난 관측(11-07)까지.
            return _Resp([{"date": "2026-11-01", "value": "5.16"},
                          {"date": "2026-10-01", "value": "4.90"},
                          {"date": "2026-09-01", "value": "4.70"},
                          {"date": "2026-08-01", "value": "4.60"}])
        return _Resp([])
    monkeypatch.setattr(ms.requests, "get", fake_get)
    monkeypatch.setattr(mo, "_fred_fetch_series",
                        lambda sid, lb: ({"value": 4.98, "time": "2026-11-10", "change": 0.1}
                                         if sid == "DGS10" else None))
    out = ms.fetch_macro_snapshot()
    rows = {r["key"]: r for r in list(out["domestic"]) + list(out["global"])}
    assert seen["aggs"] == [None, "eop"]             # 카드 = 월평균 · 차트 = 월말값
    # 카드 스파크는 월평균 그대로 — 끝점을 현재값으로 갈지 않는다(사용자 2026-11-12).
    assert rows["us_10y"]["value"] == 4.98 and rows["us_10y"]["spark"][-1] == 5.16
    ch = out["charts"]["rates_fx"]
    assert ch["labels"] == ["2026-08", "2026-09", "2026-10"]
    assert ch["us_10y"] == [4.60, 4.70, 4.90] and ch["kr_10y"] == [3.60, 3.70, 3.95]
    assert ch["spread"][-1] == round(4.90 - 3.95, 2)
