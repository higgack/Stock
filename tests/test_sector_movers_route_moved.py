"""업종 등락 TOP 10 — 원천 주소가 옮겨 간 날(2026-11-11 사용자 캡처).

🇰🇷 위젯: `원천이 HTTP 404 — 원천: Route GET:/api/domestic/market/upjong/list?
pageSize=100 not found` 를 달고 5일째 저장분. 옛 판은 404(= 그 주소가 없다는
원천의 답)를 받고도 같은 주소만 두드렸다.
🇺🇸 위젯: 네이버·Finviz 가 둘 다 비자 **위젯이 통째로 사라졌다**(#335 의 미국판).

샌드박스는 네이버에 닿지 못하므로 전부 스텁으로 태운다(#312·#336).
"""
from __future__ import annotations

import json
import types

import pytest

import bot.naver_sector_client as nsc
from bot import naver_diag as nd

_OLD = nsc._UPJONG_API
_ORIG_MAYBE = nsc._maybe_discover_upjong      # iso 가 갈아 끼우기 전의 원본
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
    # 옛 주소가 답하면 랭킹 v2 는 묻지 않는다(404 일 때만 묻는다)
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


def test_upjong_like_paths_keeps_other_words_and_drops_templates():
    got = nsc.upjong_like_paths(["/api/domestic/industry/list", "/api/x/upjong/{c}/list",
                                 "/api/news/list", "/api/sector/top"])
    assert got == ["/api/domestic/industry/list", "/api/sector/top"]


def test_upjong_from_theme_derives_siblings():
    assert nsc.upjong_from_theme("https://stock.naver.com/api/a/theme/list") == [
        "https://stock.naver.com/api/a/upjong/list",
        "https://stock.naver.com/api/a/industry/list",
        "https://stock.naver.com/api/a/sector/list"]
    assert nsc.upjong_from_theme("") == []


def test_discovery_tries_theme_sibling_and_keeps_all_paths(iso, monkeypatch):
    from bot import naver_spa_discover as disc
    seen = {}
    monkeypatch.setattr(disc, "discover", lambda *a, **k: (
        seen.update(k) or (["/api/news/list"], "")))
    monkeypatch.setattr(nsc, "theme_memo_url",
                        lambda: "https://stock.naver.com/api/b/theme/list")
    sib = "https://stock.naver.com/api/b/industry/list"
    _route(monkeypatch, {sib: lambda: (_rows(79), "")})
    nsc._discover_upjong_endpoint()
    memo, _ = nsc._cache_read_any(nsc._UPJONG_MEMO)
    assert memo["url"] == sib and memo["all_paths"] == ["/api/news/list"]
    assert memo["theme_basis"].endswith("/theme/list")
    assert seen["chunk_cap"] == nsc._UPJONG_CHUNK_CAP and seen["prefer"] == "page"


def test_new_theme_basis_lifts_cooldown(iso, monkeypatch):
    """단서(테마 주소)가 같으면 6시간 냉각, 바뀌면 바로 다시 탐색한다."""
    import threading
    import bot.finviz_client as fc
    monkeypatch.setattr(fc, "naver_paused", lambda: False)
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(1))
    nsc._cache_write(nsc._UPJONG_MEMO, {"url": "", "theme_basis": "old"})
    monkeypatch.setattr(nsc, "theme_memo_url", lambda: "old")
    _ORIG_MAYBE()
    assert started == []
    monkeypatch.setattr(nsc, "theme_memo_url", lambda: "new")
    _ORIG_MAYBE()
    assert started == [1]
    nsc._BG_KEYS.discard(nsc._UPJONG_MEMO)                 # start 를 막았으므로 직접 치운다


def test_prefix_candidates_only_domestic_prefixes():
    got = nsc.prefix_candidates(["/api/stockSecurity/rankings/v2/domestic/",
                                 "/api/personal/users/favorite/groups/",
                                 "/api/domestic/market/", "/api/x/list"], ("upjong",))
    # 맨 형태가 먼저, `/list` 는 뒤 — 시도 상한 안에 맨 형태가 물리게(2026-11-11 2차 실측)
    assert got == ["/api/stockSecurity/rankings/v2/domestic/upjong",
                   "/api/domestic/market/upjong",
                   "/api/stockSecurity/rankings/v2/domestic/upjong/list",
                   "/api/domestic/market/upjong/list"]


def test_prefix_candidates_query_direct_prefix_goes_first():
    """VM 실측 원문: `"/api/stockSecurity/rankings/v2/domestic/".concat(t,"?")` —
    접두 바로 뒤에 쿼리가 오는 접두가 목록 주소다. 그 접두를 먼저 묻는다."""
    ctx = {"/api/domestic/market/": ['.concat(s,"/").concat(n,"/stocklist?")'],
           "/api/stockSecurity/rankings/v2/domestic/":
               ['function(t){ ⟦…⟧ .concat(t,"?").concat(E.toString())']}
    got = nsc.prefix_candidates(["/api/domestic/market/",
                                 "/api/stockSecurity/rankings/v2/domestic/"],
                                ("upjong", "theme"), ctx)
    assert got[:2] == ["/api/stockSecurity/rankings/v2/domestic/upjong",
                       "/api/stockSecurity/rankings/v2/domestic/theme"], got
    assert got[2] == "/api/domestic/market/upjong"


def test_discovery_retries_without_query_when_route_exists(iso, monkeypatch):
    """주소는 있는데(400) 우리 쿼리를 거절하면 쿼리 없이 한 번 더 — 새 쿼리 이름은
    모르므로 지어내지 않는다(#165)."""
    from bot import naver_spa_discover as disc
    monkeypatch.setattr(disc, "discover", lambda *a, **k: (
        ["/api/stockSecurity/rankings/v2/domestic/"], ""))
    first = "https://stock.naver.com/api/stockSecurity/rankings/v2/domestic/upjong"
    good = "https://m.stock.naver.com/api/stockSecurity/rankings/v2/domestic/upjong"
    seen = []

    def _get(url, **kw):
        seen.append((url, kw.get("params")))
        if url != good:
            return _404()
        if kw.get("params"):
            return None, nd.http_reason(400, 40, body={"message": "bad query"})
        return _rows(79), ""
    monkeypatch.setattr(nsc, "_get2_json", _get)
    nsc._discover_upjong_endpoint()
    memo, _ = nsc._cache_read_any(nsc._UPJONG_MEMO)
    assert memo["url"] == good, memo["tried"]
    assert (good, None) in seen
    assert seen[0][0] == first, seen            # 맨 형태가 `/list` 보다 먼저 물린다
    # 404 후보엔 쿼리 없는 재시도를 걸지 않는다(주소가 없다는 답이다)
    assert not any(u != good and p is None for u, p in seen), seen


def test_literal_contexts_shows_what_follows_a_prefix():
    from bot import naver_spa_discover as disc
    js = 'a="/api/stockSecurity/rankings/v2/domestic/".concat(t,"?pageSize=")'
    ctx = disc.literal_contexts(js, width=20)
    assert ctx["/api/stockSecurity/rankings/v2/domestic/"] == ['.concat(t,"?pageSize']
    # 앞쪽 원문 — 이어 붙는 변수가 무엇인지는 앞(함수 머리)에 있다
    ctx = disc.literal_contexts(js, width=10, before=2)
    assert ctx["/api/stockSecurity/rankings/v2/domestic/"] == ['a= ⟦…⟧ .concat(t,']


def test_discovery_validates_prefix_candidates_and_keeps_samples(iso, monkeypatch):
    from bot import naver_spa_discover as disc
    def _disc(*a, **k):
        k["ctx_out"]["/api/domestic/market/"] = ['"+x+"/list"']
        return ["/api/domestic/market/", "/api/news/list"], ""
    monkeypatch.setattr(disc, "discover", _disc)
    # 후보 순서는 맨 형태 먼저, `/list` 뒤 — industry/list 가 sector/list 보다 먼저 물린다
    good = "https://stock.naver.com/api/domestic/market/sector/list"
    wrong = "https://stock.naver.com/api/domestic/market/industry/list"
    _route(monkeypatch, {good: lambda: (_rows(79), ""),
                         wrong: lambda: ([{"foo": 1}], "")})
    nsc._discover_upjong_endpoint()
    memo, _ = nsc._cache_read_any(nsc._UPJONG_MEMO)
    assert memo["url"] == good, memo["tried"]
    assert any("표본" in t and "foo" in t for t in memo["tried"])
    assert memo["contexts"] == {"/api/domestic/market/": ['"+x+"/list"']}


# ── 2026-11-11 3차 VM 실측: 원천이 허용값을 적어 보냈다 ─────────────────────
# `rankings/v2/domestic/upjong` → 400 `category: Invalid enum value. Expected
# 'industries' | 'themes' | 'groups'`. 그 값으로 다시 묻고, `size`·`cursor` 로
# 쪽을 넘긴다(청크 원문). 이름은 원천이 말한 것만 쓴다(#165·#349).
_ENUM_WHY = ("원천이 HTTP 400 — 원천: category: Invalid enum value. Expected "
             "'industries' | 'themes' | 'groups', received 'upjong' (invalid_enum_value)")


def test_enum_values_reads_what_the_source_allows():
    assert nsc.enum_values(_ENUM_WHY) == ["industries", "themes", "groups"]
    assert nsc.enum_values("원천이 HTTP 404") == []
    got = nsc.enum_candidates("https://x/api/r/v2/domestic/upjong", _ENUM_WHY,
                              nsc._UPJONG_STEMS)
    assert got == ["https://x/api/r/v2/domestic/industries"]       # themes·groups 는 업종 아님


def test_discovery_learns_enum_and_follows_cursor(iso, monkeypatch):
    from bot import naver_spa_discover as disc
    monkeypatch.setattr(disc, "discover", lambda *a, **k: (
        ["/api/stockSecurity/rankings/v2/domestic/"], ""))
    base = "https://stock.naver.com/api/stockSecurity/rankings/v2/domestic/"
    good = base + "industries"
    # 새 가족의 필드 이름(옛 name/changeRate 가 아닐 수 있다) + 두 쪽
    rows = [{"industryName": f"업종{i}", "fluctuationsRatio": f"{i - 40}.0"}
            for i in range(79)]
    seen = []

    def _get(url, **kw):
        p = kw.get("params") or {}
        seen.append((url, dict(p)))
        if url == base + "upjong" or (url.startswith(base) and url != good):
            return None, nd.http_reason(400, 40, body={
                "detailCode": "invalid_enum_value",
                "message": "category: Invalid enum value. Expected 'industries' | "
                           "'themes' | 'groups', received 'x'"})
        if url != good:
            return _404()
        if "pageSize" in p:
            return None, nd.http_reason(400, 40, body={"message": "unknown key pageSize"})
        if p.get("cursor") == "c2":
            return {"items": rows[50:], "hasNext": False, "cursor": None}, ""
        return {"items": rows[:50], "hasNext": True, "cursor": "c2"}, ""
    monkeypatch.setattr(nsc, "_get2_json", _get)
    nsc._discover_upjong_endpoint()
    memo, _ = nsc._cache_read_any(nsc._UPJONG_MEMO)
    assert memo["url"] == good, memo["tried"]
    assert memo["params"] == {"size": nsc._UPJONG_PAGE_SIZE}
    assert (good, {"size": nsc._UPJONG_PAGE_SIZE, "cursor": "c2"}) in seen
    # 원천이 말하지 않은 값(themes·groups)은 업종 후보로 묻지 않는다
    assert not any(u.endswith(("/themes", "/groups")) for u, _ in seen)

    # 사다리가 메모의 주소·쿼리로 79개를 받는다(화면 경로, #35)
    raw, why, used = nsc._upjong_fetch_ladder()
    assert used == good and len(nsc.parse_upjong_json(raw)) == 79, why


# ── 4차: 랭킹 v2 를 사다리의 단으로(탐색을 기다리지 않는다) ───────────────────
def _v2_rows(n, name_key="name", rate_key="changeRate"):
    return [{name_key: f"항목{i}", rate_key: f"{i - n // 2}.0", "code": str(i)}
            for i in range(n)]


def test_upjong_ladder_reads_rank_v2_industries_without_a_memo(iso, monkeypatch):
    seen = []

    def _get(url, **kw):
        seen.append((url, dict(kw.get("params") or {})))
        if url == nsc._RANK_V2_INDUSTRIES:
            return {"items": _v2_rows(79), "hasNext": False}, ""
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)
    out = nsc.fetch_sector_movers()
    assert out["scanned"] == 79 and out["up"] and "reason" not in out
    # 옛 주소가 404(주소 없음)라고 답한 뒤에 묻는다 — 탐색은 기다리지 않는다
    assert [u for u, _ in seen] == [_OLD, nsc._RANK_V2_INDUSTRIES]
    assert seen[1][1] == {"size": nsc._UPJONG_PAGE_SIZE} and iso.kicked == []


def test_rank_v2_rows_it_cannot_read_are_named_with_a_sample(iso, monkeypatch):
    """답은 왔는데 필드 이름이 다르면 표본을 사유에 남긴다 — 다음 판정의 재료(#82)."""
    def _get(url, **kw):
        if url == nsc._RANK_V2_INDUSTRIES:
            return {"items": [{"zzName": "x", "zzRate": 1}] * 79, "hasNext": False}, ""
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)
    raw, why, used = nsc._upjong_fetch_ladder()
    assert not nsc.parse_upjong_json(raw)
    assert "랭킹 v2" in why and "zzName" in why and nd.status_from(why) == 404


def test_signed_rate_follows_the_direction_field():
    rows = [{"name": "가", "fluctuationsRatio": "2.5", "fluctuationsType": {"code": "FALLING"}},
            {"name": "나", "changeRate": "-1.0"},
            {"name": "다", "fluctuationsRatio": "3.0", "fluctuationsType": "RISING"}]
    assert [r["pct"] for r in nsc.parse_upjong_json(rows)] == [-2.5, -1.0, 3.0]


def test_themes_come_from_rank_v2_first(iso, monkeypatch):
    def _get(url, **kw):
        p = kw.get("params") or {}
        if url == nsc._RANK_V2_THEMES:
            if p.get("cursor") == "c":
                return {"items": _v2_rows(200)[100:], "hasNext": False}, ""
            return {"items": _v2_rows(200)[:100], "hasNext": True, "cursor": "c"}, ""
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)
    rows, marks, why, partial = nsc.collect_themes_json()
    assert len(rows) == 200 and not partial and not why
    assert marks[-1].startswith("랭킹v2/themes ✅ 200개") and len(marks) == 4


def test_a_short_rank_v2_theme_page_is_not_baked_as_the_full_list(iso, monkeypatch):
    def _get(url, **kw):
        if url == nsc._RANK_V2_THEMES:
            return {"items": _v2_rows(40), "hasNext": False}, ""
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)
    monkeypatch.setattr(nsc, "theme_memo_url", lambda: "")
    rows, marks, why, partial = nsc.collect_themes_json()
    assert rows == [] and "하한" in marks[-1] and marks[-1].startswith("랭킹v2/themes ❌")


# ── 4차: 해외(미국 …) 업종도 랭킹 v2 로 — 지역 조각은 실호출로만 채택 ────────────
import bot.naver_ranking_client as nrc
import bot.finviz_client as _fvc


@pytest.fixture
def intl(tmp_path, monkeypatch):
    monkeypatch.setattr(_fvc, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(_fvc, "naver_paused", lambda: False)
    return tmp_path


def test_enum_swap_replaces_the_rejected_segment_only():
    why = ("원천이 HTTP 400 — 원천: region: Invalid enum value. Expected 'domestic' | "
           "'us' | 'jp', received 'USA' (invalid_enum_value)")
    got = nsc.enum_swap("https://h/api/r/v2/USA/industries", why,
                        lambda v: v.lower() in {"usa", "us"})
    assert got == ["https://h/api/r/v2/us/industries"]
    assert nsc.enum_swap("https://h/api/r/v2/USA/industries", "원천이 HTTP 404", bool) == []


def test_us_sector_movers_learn_the_region_from_the_source(intl, monkeypatch):
    # 처음 묻는 후보(USA·usa·us)에 없는 값 — 원천이 적어 보낸 허용값으로만 닿는다
    good = nsc._RANK_V2 + "foreign/america/sectors?sortType=changeRate"
    seen = []

    def _get(url, **kw):
        seen.append(url)
        if url == good:
            return {"items": _v2_rows(40, "industryName", "fluctuationsRatio"),
                    "hasNext": False}, ""
        if url.startswith(nsc._RANK_V2 + "foreign/"):
            seg = url[len(nsc._RANK_V2 + "foreign/"):].split("/")[0]
            # 원천이 실제로 보내는 봉투 모양(2026-11-11 VM — message 가 JSON 문자열, #155)
            import json as _json
            return None, nd.http_reason(400, 40, body=_json.dumps({
                "detailCode": "invalid_enum_value",
                "message": _json.dumps({"formErrors": [], "fieldErrors": {"region": [
                    "Invalid enum value. Expected 'domestic' | 'america' | 'jp',"
                    f" received '{seg}'"]}})}))
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)
    out = nrc.fetch_intl_sector_movers_naver("US", top_n=10)
    assert len(out["up"]) == 10 and len(out["down"]) == 10 and "reason" not in out
    assert out["up"][0]["pct"] == 19.0 and out["down"][0]["pct"] == -20.0
    assert not any("/jp/" in u or "/domestic/" in u for u in seen)     # 남의 나라는 안 묻는다
    memo = _fvc._cached(nrc.intl_rank_memo("US"), ttl=10 ** 9)
    assert memo["url"] == good


def test_us_sector_movers_fall_through_with_the_reason_and_cool_down(intl, monkeypatch):
    calls = []

    def _get(url, **kw):
        calls.append(url)
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)

    class _R:
        status_code = 404
        content = b"{}"

        def json(self):
            return {"message": "Route GET:/api/foreign/market/USA/upjong/list not found"}
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    out = nrc.fetch_intl_sector_movers_naver("US", top_n=10)
    assert out["up"] == [] and nd.status_from(out["reason"]) == 404      # 옛 경로의 사유
    n = len(calls)
    assert n == 1                                    # 실측 주소 하나(foreign/USA/sectors)
    nrc.fetch_intl_sector_movers_naver("US", top_n=10)
    assert len(calls) == n                                                # 냉각 — 다시 안 두드린다
    memo = _fvc._cached(nrc.intl_rank_memo("US"), ttl=10 ** 9)
    assert memo["url"] == "" and len(memo["tried"]) == 1
    assert calls[0] == nsc._RANK_V2 + "foreign/USA/sectors?sortType=changeRate"


def test_a_handful_of_rows_is_not_adopted_as_the_us_industry_list(intl, monkeypatch):
    def _get(url, **kw):
        if url.startswith(nsc._RANK_V2):
            return {"items": _v2_rows(5), "hasNext": False}, ""
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)
    rows, why = nrc._intl_rank_v2("US")
    assert rows == [] and "못 찾았습니다" in why
    memo = _fvc._cached(nrc.intl_rank_memo("US"), ttl=10 ** 9)
    assert memo["url"] == "" and "하한" in memo["tried"][0] and "표본" in memo["tried"][0]


def test_rank_v2_is_not_asked_when_the_old_address_is_blocked_not_gone(iso, monkeypatch):
    """막힘(403)·한도(429)는 주소 이전이 아니다 — 새 주소를 두드리지 않는다(#82)."""
    seen = []

    def _get(url, **kw):
        seen.append(url)
        return None, nd.http_reason(403, 10)
    monkeypatch.setattr(nsc, "_get2_json", _get)
    monkeypatch.setattr(nsc, "theme_memo_url", lambda: "")
    nsc._upjong_fetch_ladder()
    rows, marks, why, partial = nsc.collect_themes_json()
    assert not any(u.startswith(nsc._RANK_V2) for u in seen)
    assert len(marks) == len(nsc._THEME_API_RUNGS)


class TestIntlRouteProbe:
    """③-d — 옛 해외 주소의 지금 답 + 입구 코드가 가리키는 '업종' 화면을 한 단계 따라가 읽는다."""

    def _fetch(self):
        home = ('<script src="/_next/static/chunks/app/home/page-1.js"></script>')
        home_js = ('x="/api/realtime/worldstock/stock/";'
                   'nav={href:"/worldstock/market/USA/industry"};t="/x/[nation]/industry"')
        ind = '<script src="/_next/static/chunks/app/ind/page-2.js"></script>'
        ind_js = ('f=async e=>get("/api/stockSecurity/rankings/v2/foreign/".concat(e,'
                  '"/industries?size=100"))')
        calls = []

        def fetch(u):
            calls.append(u)
            if "/USA/upjong/list" in u:
                return None, "HTTP 404 — 원천: Route not found"
            if "/JPN/upjong/list" in u:
                return '[{"no":1}]', ""
            if u == "https://m.stock.naver.com/worldstock/":
                return home, ""
            if u.endswith("page-1.js"):
                return home_js, ""
            if u == "https://stock.naver.com/worldstock/market/USA/industry":
                return ind, ""
            if u.endswith("page-2.js"):
                return ind_js, ""
            return None, "HTTP 500"
        return fetch, calls

    def test_follows_industry_route_and_reports_its_api(self):
        from bot import naver_ranking_client as nrc
        fetch, calls = self._fetch()
        out = "\n".join(nrc.intl_route_probe_lines(fetch))
        assert "[US]" in out and "HTTP 404" in out
        assert "[JP]" in out and "응답 10B" in out
        # 입구 코드의 업종 화면 경로를 찾고, 구멍 있는 템플릿은 따라가지 않는다.
        assert "화면 /worldstock/market/USA/industry" in out
        assert "https://stock.naver.com/worldstock/market/USA/industry" in calls
        assert not any("[nation]" in c for c in calls)
        # 따라간 화면의 원문이 이어 붙이는 코드를 보여 준다.
        fol = out.split("따라감 [https://stock.naver.com/worldstock/market/USA/industry]")[1]
        fol = fol.split("따라감 [")[0]
        assert "foreign/" in fol and "/industries" in fol
        # 업종 낱말이 없는 API(실시간 시세)는 목록에 안 싣는다.
        assert "API /api/realtime" not in out

    def test_no_route_says_so(self):
        from bot import naver_ranking_client as nrc
        out = "\n".join(nrc.intl_route_probe_lines(lambda u: (None, "HTTP 403")))
        assert "따라가지 않았습니다" in out and "HTTP 403" in out

    def test_nation_industry_routes_are_followed_first(self):
        """VM 3차: 이름순으로 `/briefing/sector/…` 부터 따라가 상한에 걸렸다."""
        from bot import naver_ranking_client as nrc
        noise = ";".join(f'a{i}="/briefing/sector/x{i}"' for i in range(10))
        home_js = noise + ';b="/market/stock/usa/industry"'
        page_js = 'g=async e=>f("/api/foreign/industry/".concat(e,"/list?size=100"))'
        calls = []

        def fetch(u):
            calls.append(u)
            if u == "https://m.stock.naver.com/worldstock/":
                return '<script src="/_next/static/chunks/app/h/page-1.js"></script>', ""
            if u.endswith("page-1.js"):
                return home_js, ""
            if u == "https://stock.naver.com/market/stock/usa/industry":
                return '<script src="/_next/static/chunks/app/i/page-2.js"></script>', ""
            if u.endswith("page-2.js"):
                return page_js, ""
            return None, "HTTP 404"
        out = "\n".join(nrc.intl_route_probe_lines(fetch))
        assert "https://stock.naver.com/market/stock/usa/industry" in calls
        fol = out.split("따라감 [https://stock.naver.com/market/stock/usa/industry]")[1]
        fol = fol.split("따라감 [")[0]
        # 이름에 업종 낱말이 없어도 따라간 화면의 API 는 전부 · 접두 뒤 원문까지.
        assert "API /api/foreign/industry/" in fol
        assert "접두 /api/foreign/industry/ 원문" in fol and "/list?size=100" in fol


def test_foreign_sectors_rows_with_sector_name_are_read(intl, monkeypatch):
    """VM 4차 실측 주소 — 행의 이름 칸이 `sectorName` 이어도 읽는다(필드명은 아직 못 쟀다)."""
    good = nsc._RANK_V2 + "foreign/JPN/sectors?sortType=changeRate"

    def _get(url, **kw):
        if url == good:
            return {"items": _v2_rows(30, "sectorName", "changeRate"), "hasNext": False}, ""
        return _404()
    monkeypatch.setattr(nsc, "_get2_json", _get)
    rows, why = nrc._intl_rank_v2("JP")
    assert len(rows) == 30 and why == ""
    assert _fvc._cached(nrc.intl_rank_memo("JP"), ttl=10 ** 9)["url"] == good


class TestWorldSectorItems:
    """해외 업종 구성 — 새 가족(sectors/v2/foreign/{nat}/{code}/items)으로 업종맵·시세맵을 세운다."""

    # 2026-11-11 VM 실측 허용값(sectors/v2/foreign/*/items sortType)
    VM_ALLOWED = ["changeRate", "tradingVolume", "tradingValue", "marketCap"]

    def _stub(self, monkeypatch, allowed=None):
        secs = [{"sectorName": f"업종{i}", "sectorCode": f"S{i}", "changeRate": "1.0"}
                for i in range(3)]
        seen = []

        def _get(url, **kw):
            params = dict(kw.get("params") or {})
            seen.append((url, params))
            if "/rankings/v2/foreign/JPN/sectors" in url:
                return {"items": secs, "hasNext": False}, ""
            if "/sectors/v2/foreign/JPN/S" in url:
                ok = allowed or self.VM_ALLOWED
                if params.get("sortType") not in ok:
                    return None, nd.http_reason(400, 10, body=json.dumps({
                        "message": json.dumps({"fieldErrors": {"sortType": [
                            "Invalid enum value. Expected "
                            + " | ".join(f"'{a}'" for a in ok)
                            + f", received '{params.get('sortType')}'"]}})}))
                code = url.rsplit("/", 2)[-2]
                rows = [{"symbolCode": f"{code[1:]}{j:03d}", "koreanCodeName": f"회사{j}",
                         "accumulatedTradingVolume": "1,000", "marketValue": "2e8"}
                        for j in range(30)]
                return {"items": rows, "hasNext": False}, ""
            return _404()
        monkeypatch.setattr(nsc, "_get2_json", _get)
        return seen

    def test_industry_and_quote_maps_from_new_family(self, intl, monkeypatch):
        self._stub(monkeypatch)
        m = nrc.world_industry_map("JP")
        assert len(m) == 90 and m["0000.T"] == "업종0" and m["2029.T"] == "업종2"
        names = _fvc._cached("naver_upjong_name_JP.json", ttl=10 ** 9)
        assert names["0000.T"] == "회사0"
        nrc._build_quote_map("JP")
        q = _fvc._cached("naver_quote_JP.json", ttl=10 ** 9)
        assert q["1005.T"] == {"vol": 1000, "value": None, "mcap": 2.0}

    def test_first_ask_uses_the_measured_sort_value(self, intl, monkeypatch):
        """2026-11-11 VM: marketValue 는 400 — 실측 허용값으로 첫 요청부터 받는다."""
        seen = self._stub(monkeypatch)
        pairs, _why = nrc.world_sector_items("JP")
        assert len(pairs) == 90
        item_sorts = [p.get("sortType") for u, p in seen if "/sectors/v2/" in u]
        assert item_sorts and set(item_sorts) == {"marketCap"}

    def test_item_sort_enum_is_learned_from_the_source(self, intl, monkeypatch):
        seen = self._stub(monkeypatch, allowed=["changeRate", "tradingValue"])
        pairs, why = nrc.world_sector_items("JP")
        assert len(pairs) == 90
        assert any(p.get("sortType") == "changeRate" for _u, p in seen)

    def test_probe_retries_with_the_sources_allowed_sort(self, intl, monkeypatch):
        self._stub(monkeypatch, allowed=["changeRate", "tradingValue"])
        out = "\n".join(nrc.world_sector_probe("JP"))
        assert "'changeRate' 로 다시" in out and "티커 '0000.T'" in out

    def test_unreadable_items_are_not_baked_and_say_why(self, intl, monkeypatch):
        def _get(url, **kw):
            if "/sectors?" in url or url.endswith("/sectors"):
                return {"items": [{"sectorName": "a", "sectorCode": "1"}], "hasNext": False}, ""
            return {"items": [{"weird": 1}], "hasNext": False}, ""
        monkeypatch.setattr(nsc, "_get2_json", _get)
        assert nrc.world_industry_map("HK") == {}
        assert _fvc._cached("naver_industry_kr_HK.json", ttl=10 ** 9) in (None, {})
        pairs, why = nrc.world_sector_items("HK")
        assert pairs == [] and "티커로 읽힌 것 0개" in why and "weird" in why

    def test_probe_names_the_columns(self, intl, monkeypatch):
        self._stub(monkeypatch)
        out = "\n".join(nrc.world_sector_probe("JP"))
        assert "코드='S0'" in out and "티커 '0000.T'" in out and "symbolCode" in out


def test_sort_retry_judges_by_the_value_this_request_sent():
    """병렬 업종이 이미 공유값을 바꾼 뒤 옛 값으로 거절당한 업종도 다시 묻는다."""
    allowed = ["marketCap", "up"]
    assert nrc._sort_retry("marketValue", allowed, "marketCap") == (True, "marketCap")
    assert nrc._sort_retry("marketValue", allowed, "marketValue") == (True, "marketCap")
    assert nrc._sort_retry("marketCap", allowed, "marketCap") == (False, "marketCap")
    assert nrc._sort_retry("x", [], "x") == (False, "x")
