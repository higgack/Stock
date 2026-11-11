"""업종 등락 TOP 10 — 원천 주소가 옮겨 간 날(2026-11-11 사용자 캡처).

🇰🇷 위젯: `원천이 HTTP 404 — 원천: Route GET:/api/domestic/market/upjong/list?
pageSize=100 not found` 를 달고 5일째 저장분. 옛 판은 404(= 그 주소가 없다는
원천의 답)를 받고도 같은 주소만 두드렸다.
🇺🇸 위젯: 네이버·Finviz 가 둘 다 비자 **위젯이 통째로 사라졌다**(#335 의 미국판).

샌드박스는 네이버에 닿지 못하므로 전부 스텁으로 태운다(#312·#336).
"""
from __future__ import annotations

import types

import pytest

import bot.naver_sector_client as nsc
from bot import naver_diag as nd

_OLD = nsc._UPJONG_API
_NEW = "https://stock.naver.com/api/domestic/upjong/list"


def _rows(n, kind="upjong"):
    return [{"name": f"업종{i}", "changeRate": f"{i - n // 2}.0", "type": kind,
             "thistime": "20261111101500"} for i in range(n)]


def _404():
    return None, nd.http_reason(404, 80, body={
        "message": "Route GET:/api/domestic/market/upjong/list?pageSize=100 not found"})


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(nsc, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(nsc, "_cached", lambda *a, **k: None)
    kicked = []
    monkeypatch.setattr(nsc, "_maybe_discover_upjong", lambda: kicked.append(1))
    return types.SimpleNamespace(tmp=tmp_path, kicked=kicked)


def _route(monkeypatch, table):
    calls = []

    def _get(url, **kw):
        calls.append(url)
        return table[url]() if callable(table.get(url)) else table.get(url, _404())
    monkeypatch.setattr(nsc, "_get2_json", _get)
    return calls


def test_404_kicks_discovery_and_says_so_on_screen(iso, monkeypatch):
    _route(monkeypatch, {_OLD: _404})
    out = nsc.fetch_sector_movers()
    assert out["up"] == [] and iso.kicked == [1]
    assert nd.status_from(out["reason"]) == 404          # 운영자 갈래는 그대로
    shown = nd.public_reason(out["reason"])
    assert "새 주소를 탐색" in shown and "Route GET" not in shown, shown


def test_403_does_not_kick_discovery(iso, monkeypatch):
    """막힌 것(403)은 주소가 옮겨 간 것이 아니다 — 탐색은 청크 수 MB 를 받는다."""
    _route(monkeypatch, {_OLD: lambda: (None, nd.http_reason(403, 10))})
    nsc.fetch_sector_movers()
    assert iso.kicked == []


def test_validated_memo_is_used_first(iso, monkeypatch):
    nsc._cache_write(nsc._UPJONG_MEMO, {"url": _NEW})
    calls = _route(monkeypatch, {_NEW: lambda: (_rows(79), ""), _OLD: _404})
    out = nsc.fetch_sector_movers()
    assert calls[0] == _NEW and _OLD not in calls
    assert out["up"] and "reason" not in out and out["scanned"] == 79
    assert iso.kicked == []
    assert (iso.tmp / "upjong.json").exists()             # 전수는 굽는다


def test_dead_memo_falls_back_to_old_address(iso, monkeypatch):
    nsc._cache_write(nsc._UPJONG_MEMO, {"url": _NEW})
    calls = _route(monkeypatch, {_NEW: _404, _OLD: lambda: (_rows(79), "")})
    out = nsc.fetch_sector_movers()
    assert calls == [_NEW, _OLD] and out["up"] and "reason" not in out


def test_discovery_memoizes_only_a_validated_full_list(iso, monkeypatch):
    from bot import naver_spa_discover as disc
    short = "https://stock.naver.com/api/a/upjong/list"
    theme = "https://stock.naver.com/api/b/upjong/list"
    good = "https://stock.naver.com/api/c/upjong/list"
    monkeypatch.setattr(disc, "discover", lambda *a, **k: (
        ["/api/a/upjong/list", "/api/b/upjong/list", "/api/c/upjong/list",
         "/api/x/upjong/{code}/list"], ""))
    calls = _route(monkeypatch, {
        short: lambda: (_rows(20), ""),                    # 기본 페이지 = 전수 아님
        theme: lambda: (_rows(79, "theme"), ""),           # 엉뚱한 자원(#96)
        good: lambda: ({"result": _rows(79)}, ""),         # dict 로 감싸 와도 전수면 받는다
    })
    nsc._discover_upjong_endpoint()
    memo, _ = nsc._cache_read_any(nsc._UPJONG_MEMO)
    assert memo["url"] == good, memo
    assert not any("{" in u for u in calls)                # 템플릿 구멍은 채우지 않는다
    assert any("하한 미달" in t for t in memo["tried"])
    assert nsc.upjong_memo_url() == good


def test_discovery_finding_nothing_is_said(iso, monkeypatch):
    from bot import naver_spa_discover as disc
    monkeypatch.setattr(disc, "discover", lambda *a, **k: ([], "청크 0개"))
    _route(monkeypatch, {_OLD: _404})
    nsc._discover_upjong_endpoint()
    out = nsc.fetch_sector_movers()
    assert "찾지 못했습니다" in nd.public_reason(out["reason"])


def test_single_row_dict_keeps_the_dict_branch():
    """1행짜리 dict 를 풀면 '목록이 아니라 dict' 가 '1행 못 읽음' 으로 바뀐다(#292)."""
    raw = {"result": [{"name": "반도체"}]}
    assert nsc._upjong_rows(raw) is raw


# ── 🇺🇸 ─────────────────────────────────────────────────────────────────

def _us(monkeypatch, naver, l3, etf):
    import bot.naver_ranking_client as nr
    import bot.finviz_client as fv
    monkeypatch.setattr(nr, "fetch_intl_sector_movers_naver", lambda m, top_n=10: naver)
    monkeypatch.setattr(fv, "top_l3_movers", lambda top_n=10: l3)
    monkeypatch.setattr(fv, "top_sector_movers", lambda top_n=10: etf)


_EMPTY = {"up": [], "down": [], "ts": ""}
_ETF = {"up": [{"name": "기술", "pct": 1.2}], "down": [], "ts": "11-11 01:00",
        "source": "yfinance"}


def test_us_ladder_reaches_sector_etf_and_names_the_fallback(monkeypatch):
    from bot import market_overview as mo
    _us(monkeypatch, dict(_EMPTY, reason=nd.http_reason(404, 50)), _EMPTY, _ETF)
    out = mo._fetch_us_sector_movers_safe()
    assert out["up"] == _ETF["up"]
    shown = nd.public_reason(out["reason"])
    assert "섹터 ETF(yfinance)로 대체" in shown and "Finviz" in shown, shown


def test_us_all_empty_keeps_the_widget_with_a_reason(monkeypatch):
    from bot import market_overview as mo
    import bot.dashboard as d
    _us(monkeypatch, _EMPTY, _EMPTY, _EMPTY)
    out = mo._fetch_us_sector_movers_safe()
    assert out["up"] == [] and "모두 못 받았습니다" in out["reason"]
    html = d._render_us_sector_movers(out)
    assert "미국 업종 등락 TOP 10" in html and "usindustry" in html   # 입구는 남는다
    assert "모두 못 받았습니다" in html
    assert d._render_us_sector_movers(_EMPTY) == ""          # 사유도 없으면 종전대로


def test_us_first_rung_success_has_no_reason(monkeypatch):
    from bot import market_overview as mo
    nv = {"up": [{"name": "반도체", "pct": 2.0}], "down": [], "ts": "x", "source": "네이버"}
    _us(monkeypatch, nv, _ETF, _ETF)
    out = mo._fetch_us_sector_movers_safe()
    assert out is nv and "reason" not in out


def test_naver_intl_404_carries_the_status(monkeypatch, tmp_path):
    import bot.naver_ranking_client as nr
    import bot.finviz_client as fv
    monkeypatch.setattr(fv, "_cached", lambda *a, **k: None)
    resp = types.SimpleNamespace(status_code=404, content=b"x" * 40,
                                 json=lambda: {"message": "Route not found"})
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
    out = nr.fetch_intl_sector_movers_naver("US")
    assert out["up"] == [] and nd.status_from(out["reason"]) == 404


def test_kr_widget_screen_hides_the_route_dump():
    import bot.dashboard as d
    why = nd.join_notes(["원천이 주소를 옮긴 것으로 보여 새 주소를 탐색합니다",
                         nd.http_reason(404, 80, body={"message": "Route GET:/api/x not found"})])
    html = d._render_sector_movers({"up": [], "down": [], "reason": why})
    assert "새 주소를 탐색" in html and "Route GET" not in html
