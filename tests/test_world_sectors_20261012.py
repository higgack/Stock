"""국가별 섹터 3보드(섹터혼합·심리지도·섹터종목) — 사용자 2026-10-12.

원천(TradingView 스캐너·State Street 엑셀·KRX ETF 구성종목)은 이 샌드박스에서 닿지 않아
**실측하지 못했다** — 그래서 여기선 (a) 요청 본문·응답 해석이 우리가 옮긴 형식과 같은지,
(b) 실패가 갈래를 이름으로 말하는지, (c) 판정 규칙(블로그 캡처의 숫자)이 맞는지, (d) 수집 →
저장 → 화면이 한 경로로 이어지는지를 값으로 잰다.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from bot import etf_holdings as eh
from bot import tv_scanner as tv
from bot import world_sector_pages as wp
from bot import world_sectors as ws
from tests import world_sectors_fixture as fx

K = timezone(timedelta(hours=9))


def _at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=K)


@pytest.fixture
def store(tmp_path, monkeypatch):
    with fx.patched(tmp_path, monkeypatch) as fake:
        yield fake


# ── TradingView 요청·응답 ─────────────────────────────────────────────────────
def test_query_is_common_or_dr_on_primary_listing_in_korean():
    q = tv.build_query("US", ["name", "close"], 2000, 2000)
    assert q["markets"] == ["america"] and q["options"] == {"lang": "ko"}
    assert q["range"] == [2000, 4000]
    assert {"left": "is_primary", "operation": "equal", "right": True} in q["filter"]
    assert any(f.get("left") == "exchange" and f["right"] == ["NYSE", "NASDAQ", "AMEX"]
               for f in q["filter"]), "미국은 OTC 를 빼야 한다"
    ors = q["filter2"]["operands"][0]["operation"]["operands"]
    exprs = [[o["expression"] for o in x["operation"]["operands"]] for x in ors]
    assert [{"left": "type", "operation": "equal", "right": "stock"},
            {"left": "typespecs", "operation": "has", "right": ["common"]}] in exprs
    assert [{"left": "type", "operation": "equal", "right": "dr"}] in exprs, \
        "블로그 미국 업종엔 ADR 이 들어 있다"
    assert not any("preferred" in json.dumps(e) for e in exprs), "우선주는 같은 회사를 두 번 센다"
    kr = tv.build_query("KR", ["name"], 0, 10)
    assert kr["markets"] == ["korea"]
    assert not any(f.get("left") == "exchange" for f in kr["filter"])
    assert [tv.MARKETS[m][0] for m in ws.MARKETS] == ["korea", "america", "japan", "taiwan", "china"]


def test_unknown_field_detection_needs_the_quoted_token():
    cols = ["sector", "sector.tr", "industry.tr"]
    assert tv.unknown_fields('{"error":"Unknown field \\"sector.tr\\""}', cols) == ["sector.tr"]
    assert tv.unknown_fields("unknown field 'industry.tr'", cols) == ["industry.tr"]
    assert tv.unknown_fields("Unknown field sector.tr", cols) == ["sector.tr"]
    assert tv.unknown_fields("bad value for sectorx", cols) == [], "낱말이 아니면 안 센다"
    assert tv.unknown_fields('{"error":"Unknown field \\"sector\\""}', cols) == ["sector"], \
        "sector.tr 안의 sector 와 갈라야 한다"


class _Resp:
    def __init__(self, status, js=None, text=""):
        self.status_code, self._js, self.text = status, js, text
        self.content = text.encode() if text else json.dumps(js or {}).encode()

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js


class _Sess:
    def __init__(self, responses):
        self.responses, self.bodies = list(responses), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.bodies.append(json)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _page(cols, n, start=0, total=None):
    return {"totalCount": total if total is not None else n,
            "data": [{"s": f"KRX:{i:06d}", "d": [f"v{i}"] * len(cols)} for i in range(start, start + n)]}


def test_scan_pages_until_total_and_maps_columns(monkeypatch):
    monkeypatch.setattr(tv, "PAGE", 3)
    cols = [*tv.CORE, *tv.OPTIONAL]
    s = _Sess([_Resp(200, _page(cols, 3, 0, 5)), _Resp(200, _page(cols, 2, 3, 5))])
    got = tv.scan("KR", session=s, sleep=lambda x: None)
    assert got["total"] == 5 and len(got["rows"]) == 5 and got["pages"] == 2
    assert [b["range"] for b in s.bodies] == [[0, 3], [3, 6]]
    assert got["rows"][0]["symbol"] == "KRX:000000" and got["rows"][0]["sector"] == "v0"


def test_scan_drops_an_unknown_optional_column_once_and_says_so():
    s = _Sess([_Resp(400, text='{"error":"Unknown field \\"industry.tr\\""}'),
               _Resp(200, _page([c for c in [*tv.CORE, *tv.OPTIONAL] if c != "industry.tr"], 2))])
    got = tv.scan("KR", session=s, sleep=lambda x: None)
    assert got["dropped"] == ["industry.tr"]
    assert "industry.tr" not in s.bodies[1]["columns"] and "industry" in s.bodies[1]["columns"]


@pytest.mark.parametrize("resp, kind, word", [
    (_Resp(400, text='{"error":"Unknown field \\"change\\""}'), "format", "필수 열"),
    (_Resp(403, text="blocked"), "http", "403"),
    (ConnectionError("down"), "network", "닿지 못함"),
    (_Resp(200, {"totalCount": 0, "data": []}), "empty", "하나도"),
    (_Resp(200, {"oops": 1}), "format", "data 목록"),
    (_Resp(200, None, text="<html>"), "format", "JSON"),
])
def test_scan_failures_name_their_branch(resp, kind, word):
    with pytest.raises(tv.ScanError) as ei:
        tv.scan("JP", session=_Sess([resp]), sleep=lambda x: None)
    assert ei.value.kind == kind and word in ei.value.reason


def test_scan_skips_rows_whose_width_does_not_match():
    cols = [*tv.CORE, *tv.OPTIONAL]
    pg = _page(cols, 2)
    pg["data"].append({"s": "KRX:BAD", "d": ["x"]})
    got = tv.scan("KR", session=_Sess([_Resp(200, pg)]), sleep=lambda x: None)
    assert len(got["rows"]) == 2


# ── 정규화·집계·판정(순수) ─────────────────────────────────────────────────────
def test_normalize_flags_a_market_cap_in_another_currency():
    r = ws.normalize({"symbol": "TWSE:6415", "description": "A", "sector": "S", "industry": "I",
                      "change": 1.5, "Value.Traded": 10.0, "market_cap_basic": 100.0, "close": 2.0,
                      "average_volume_30d_calc": 3.0, "currency": "TWD",
                      "fundamental_currency_code": "USD"})
    assert r == ["TWSE:6415", "A", "S", "I", 1.5, 10.0, 100.0, 6.0, 2.0, 1]
    assert ws.normalize({"description": "x"}) is None
    same = ws.normalize({"symbol": "X:1", "currency": "TWD", "fundamental_currency_code": "TWD"})
    assert same[-1] == 0 and same[4] is None


def test_aggregate_is_prior_cap_weighted_and_skips_foreign_currency_caps():
    rows = [["A:1", "a", "S", "I", 10.0, 100.0, 110.0, None, 1, 0],    # 전일 100
            ["A:2", "b", "S", "I", -50.0, 50.0, 50.0, None, 1, 0],     # 전일 100
            ["A:3", "c", "S", "J", 99.0, 5.0, 1e9, None, 1, 1],        # 통화 다름 — 가중 제외
            ["A:4", "d", "T", None, 1.0, 7.0, None, None, 1, 0]]       # 시총 없음
    a = ws.aggregate(rows)
    assert a["rows"] == 4 and a["fx_skip"] == 1 and a["no_cap"] == 1
    assert ws.ret(a["sec"]["S"]) == pytest.approx((100 * 10 + 100 * -50) / 200)
    assert a["sec"]["S"]["n"] == 3 and a["sec"]["S"]["val"] == 155.0
    assert ws.ret(a["ind"]["J"]) is None, "통화가 다른 종목만 있는 업종은 등락을 못 만든다"
    assert ws.ret(a["mkt"]) == pytest.approx(-20.0)
    assert "T" in a["sec"] and None not in a["ind"]


@pytest.mark.parametrize("rel, rv, want", [
    (1.0, 1.1, "hot_up"), (1.0, 1.09, "quiet_up"), (0.99, 1.1, "churn"), (-0.99, 1.0, "calm"),
    (-1.0, 1.0, "fade"), (-1.0, 1.1, "hot_down"), (2.0, None, "up"), (0.0, None, "flat"),
    (None, 2.0, None),
])
def test_classify_six_ways_with_inclusive_thresholds(rel, rv, want):
    assert ws.classify(rel, rv, 1.0, 1.1) == want


@pytest.mark.parametrize("cells, want", [
    ({"US": -0.86, "JP": -4.35, "KR": -4.05}, ("k_down", "2국 약세")),       # 블로그 '주요 은행'
    ({"US": -1.04, "JP": -1.16, "KR": -1.38}, ("all_down", "3국 공통 약세")),  # '도매 유통업체'
    ({"US": 4.82, "JP": 1.45, "KR": 2.12}, ("all_up", "3국 공통 강세")),
    ({"US": -5.44, "JP": 1.05, "KR": 0.32}, ("mixed", "엇갈림")),              # '세미컨덕터'
    ({"US": -4.88, "JP": 7.79, "KR": -3.71}, ("mixed", "엇갈림")),
    ({"US": 3.55, "JP": 3.18, "KR": -0.17}, ("k_up", "2국 강세")),             # '패키지 소프트웨어'
    ({"US": 3.0, "TW": 1.2}, ("all_up", "2국 공통 강세")),
    ({"US": 0.5, "JP": -0.2, "KR": 1.5}, ("none", "뚜렷하지 않음")),
    ({"US": -3.0, "JP": None}, ("na", "")),
])
def test_mix_verdict_follows_the_blog_table(cells, want):
    assert ws.mix_verdict(cells, 1.0) == want


def test_mix_sort_puts_common_before_partial_before_mixed():
    rows = {"a": {"US": -1.5, "JP": -1.2}, "b": {"US": -8.0, "JP": -3.0, "KR": 0.1},
            "c": {"US": -9.0, "JP": 9.0}, "d": {"US": -5.0, "JP": -5.0, "KR": -5.0}}
    keyed = sorted(rows, key=lambda k: ws.mix_sort_key(ws.mix_verdict(rows[k], 1.0)[0], rows[k], 1.0))
    assert keyed == ["d", "a", "b", "c"]


def test_week_cell_compounds_each_side_then_subtracts_and_counts_wins():
    def agg(rg, rm, vg=100.0, vm=1000.0):
        return {"mkt": {"w": 1.0, "wr": rm, "val": vm, "vb": vm, "base": 1000.0},
                "ind": {"X": {"w": 1.0, "wr": rg, "val": vg, "vb": vg, "base": 100.0, "n": 5}}}
    items = [("d1", agg(2.0, 1.0), None), ("d2", agg(-1.0, 0.0), None), ("d3", agg(3.0, 1.0), None)]
    c = ws.week_cell(items, "ind", "X")
    want = ((1.02 * 0.99 * 1.03) - (1.01 * 1.0 * 1.01)) * 100
    assert c["rel"] == pytest.approx(want) and c["wins"] == 2 and c["N"] == 3
    assert c["rv"] == pytest.approx(1.0) and c["basis"] == "approx"


def test_value_parts_uses_20_session_history_only_when_15_exist():
    base = [{"mkt": {"val": 1000.0}, "sec": {"S": {"val": 100.0}}} for _ in range(15)]
    agg = {"mkt": {"val": 2000.0, "vb": 2000.0, "base": 4000.0},
           "sec": {"S": {"val": 300.0, "vb": 300.0, "base": 150.0}}}
    p = ws.value_parts(agg, "sec", "S", base)
    assert p[4] == "20d" and ws.rv_of(p) == pytest.approx((300 / 100) / (2000 / 1000))
    p2 = ws.value_parts(agg, "sec", "S", None)
    assert p2[4] == "approx" and ws.rv_of(p2) == pytest.approx((300 / 150) / (2000 / 4000))


def test_base_aggs_counts_calendar_sessions_not_files(store):
    d = "2026-10-12"
    prev = ws.prev_sessions("KR", d, 20)
    assert len(prev) == 20 and "2026-10-09" not in prev, "한글날은 세션이 아니다"
    aggs = {x: {"mkt": {"val": 1.0}} for x in prev[:14]}
    assert ws.base_aggs("KR", d, aggs) is None
    aggs[prev[14]] = {"mkt": {"val": 1.0}}
    assert len(ws.base_aggs("KR", d, aggs)) == 15


def test_validate_rejects_small_partial_and_stale():
    rows = [[f"A:{i}", "n", "S", "I", 1.0 + i, 10.0, 100.0, None, 1, 0] for i in range(400)]
    assert ws.validate(rows[:100], None)[2] == "small"
    part = [r[:4] + [None] + r[5:] for r in rows[:60]] + rows[60:]
    assert ws.validate(part, None)[2] == "partial"
    assert ws.validate(rows, {"date": "2026-10-08", "rows": rows})[2] == "stale"
    moved = [r[:4] + [r[4] + 0.5] + r[5:] for r in rows]
    assert ws.validate(moved, {"date": "2026-10-08", "rows": rows}) == (True, "", "")
    assert ws.validate(rows[:300], {"date": "x", "rows": rows + rows})[2] == "small"


# ── 세션·창 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("market, now, want", [
    ("KR", _at(2026, 10, 12, 18, 31), "2026-10-12"),   # 15:30 + 3h
    ("KR", _at(2026, 10, 12, 18, 29), "2026-10-08"),   # 10-09 한글날 휴장
    ("JP", _at(2026, 10, 12, 20, 0), "2026-10-09"),    # 10-12 체육의 날
    ("US", _at(2026, 10, 13, 8, 1), "2026-10-12"),     # 16:00 ET(DST) = 05:00 KST + 3h
    ("US", _at(2026, 10, 13, 7, 59), "2026-10-09"),
    ("TW", _at(2026, 10, 12, 17, 31), "2026-10-12"),   # 13:30 현지 = 14:30 KST + 3h
    ("CN_A", _at(2026, 10, 12, 19, 1), "2026-10-12"),  # 15:00 현지 = 16:00 KST + 3h
])
def test_due_session_is_close_plus_ready_on_the_exchange_calendar(market, now, want):
    assert ws.due_session(market, now) == want


def test_calendar_out_of_range_falls_back_to_weekdays():
    assert ws.session_times("CN_A", "2027-03-03") is not None      # XSHG 달력 밖 수요일
    assert ws.session_times("CN_A", "2027-03-06") is None          # 토요일


def test_status_is_pending_until_the_window_closes(store):
    now = _at(2026, 10, 12, 23, 0)
    assert ws.market_day_status("KR", "2026-10-12", now) == "pending", "창이 남았다"
    assert ws.market_day_status("KR", "2026-10-12", _at(2026, 10, 13, 9, 1)) == "missing"
    assert ws.market_day_status("KR", "2026-10-09", now) == "closed"
    assert ws.market_day_status("US", "2026-10-12", now) == "pending"


# ── 수집 경로 ───────────────────────────────────────────────────────────────
def test_collect_stores_once_then_stops_asking(store):
    store.now = _at(2026, 10, 12, 18, 40)
    res = ws.tick(store.now)
    assert "KR:2026-10-12" in res["stored"] and ws.agg_path("KR", "2026-10-12").exists()
    assert ws.snap_path("KR", "2026-10-12").exists()
    n = store.calls.count("KR")
    ws.tick(store.now + timedelta(minutes=15))
    assert store.calls.count("KR") == n, "받은 세션을 다시 물었다"
    a = ws.load_agg("KR", "2026-10-12")
    assert a["rows"] == fx.N_STOCKS and a["labels"]["ind"]["Semiconductors"] == "세미컨덕터"


def test_stale_answer_is_not_baked_and_backs_off(store, monkeypatch):
    store.now = _at(2026, 10, 8, 19, 0)
    ws.tick(store.now)
    assert ws.agg_path("KR", "2026-10-08").exists()
    same = fx.fake_rows("KR", "2026-10-08")
    monkeypatch.setattr(tv, "scan", lambda m, **k: {"rows": same, "total": 1, "dropped": [], "pages": 1})
    t = _at(2026, 10, 12, 18, 40)
    ws.tick(t)
    ms = ws.load_state()["markets"]["KR"]
    assert not ws.agg_path("KR", "2026-10-12").exists(), "직전 세션과 같은 값을 굳혔다"
    assert ms["kind"] == "stale" and ms["date"] == "2026-10-12" and ms["n"] == 1
    assert ws._parse(ms["next_try"]) == t + timedelta(minutes=30)
    kind, line = ws.market_verdict("KR", ws.load_state(), t)
    assert kind == "stale" and "직전 세션" in line


def test_failure_ladder_and_missed_window(store):
    store.fail["TW"] = "원천이 HTTP 403"

    def tick(t):
        store.now = t
        ws.tick(t)
    t = _at(2026, 10, 12, 17, 40)
    tick(t)
    ms = ws.load_state()["markets"]["TW"]
    assert ms["kind"] == "http" and ms["n"] == 1
    tick(t + timedelta(minutes=15))
    assert store.calls.count("TW") == 1, "백오프 중에 다시 물었다"
    tick(t + timedelta(minutes=31))
    assert ws.load_state()["markets"]["TW"]["n"] == 2
    kind, _ = ws.market_verdict("TW", ws.load_state(), t + timedelta(minutes=31))
    assert kind == "fail"
    # 대만 개장 09:00 현지 = 10:00 KST → 창 끝 = 개장 90분 전 08:30 KST(단일가·시험매매 전)
    tick(_at(2026, 10, 13, 8, 25))
    assert ws.load_state()["markets"]["TW"]["n"] == 3 and not ws.load_state()["markets"]["TW"].get("missed")
    n = store.calls.count("TW")
    tick(_at(2026, 10, 13, 8, 35))
    ms = ws.load_state()["markets"]["TW"]
    assert ms.get("missed") == "2026-10-12" and store.calls.count("TW") == n, \
        "장전 거래가 시작되면 그 세션을 묻지 않는다(지금 값은 다음 세션 값일 수 있다)"
    kind, line = ws.market_verdict("TW", ws.load_state(), _at(2026, 10, 13, 8, 35))
    assert kind == "missed_fail" and ws.VERDICT_MARK[kind] == "❌", \
        "우리 실패(403)로 창을 놓쳤으면 ⚠️ 가 아니라 ❌ — 매일 깨져도 결산에 안 오르면 안 된다"
    assert "3회 실패" in line and "HTTP 403" in line, line
    assert ms["misses"]["2026-10-12"] == {"kind": "http", "reason": "원천이 HTTP 403", "n": 3}


def test_unexpected_exception_in_one_market_does_not_stop_the_others(store, monkeypatch):
    real = tv.scan

    def boom(m, **k):
        if m == "JP":
            raise RuntimeError("x")
        return real(m, **k)
    monkeypatch.setattr(tv, "scan", boom)
    store.now = _at(2026, 10, 9, 23, 0)
    res = ws.tick(store.now)
    assert "JP" in res["failed"] and any(s.startswith("KR:") for s in res["stored"])
    assert "RuntimeError" in ws.load_state()["markets"]["JP"]["reason"]


# ── 섹터혼합 ────────────────────────────────────────────────────────────────
def test_mix_record_covers_industries_in_two_or_more_markets_and_finalizes(store):
    fx.simulate(store, _at(2026, 10, 5, 0), 9)
    now = _at(2026, 10, 13, 9, 0)
    rec = ws.mix_record("2026-10-12", now)
    assert rec["final"], rec["markets"]
    assert rec["markets"]["JP"]["day"] == "closed"
    assert rec["markets"]["KR"]["sessions"] == 4, "10-06~10-12 한국 세션(10-09 휴장)"
    row = next(r for r in rec["rows"] if r["key"] == "Semiconductors")
    assert set(row["cells"]) == set(ws.MARKETS)
    assert row["cells"]["JP"]["day"] is None and row["cells"]["JP"]["week"] is not None
    c = row["cells"]["KR"]
    assert c["N"] == 4 and 0 <= c["wins"] <= 4 and c["n"] == fx.N_STOCKS // 8
    # 받는 주기(`tick`)가 이미 굳혔다 — 다시 불러도 같은 날을 또 쓰지 않는다(멱등)
    assert ws.load_mix_record("2026-10-12")["final"]
    assert "2026-10-12" not in ws.finalize_records(now)


def test_mix_record_waits_for_the_us_session(store):
    fx.simulate(store, _at(2026, 10, 5, 0), 8)
    t = _at(2026, 10, 12, 23, 40)
    store.now = t
    ws.tick(t)
    rec = ws.mix_record("2026-10-12", t)
    assert rec["markets"]["US"]["day"] == "pending" and not rec["final"]
    assert ws.load_mix_record("2026-10-12") is None


def test_single_stock_industries_are_not_present(store):
    rows = fx.fake_rows("KR", "2026-10-12")
    rows[0]["industry"] = "Tobacco"
    store.now = _at(2026, 10, 12, 18, 40)
    tv_scan = tv.scan
    tv.scan = lambda m, **k: ({"rows": rows, "total": 1, "dropped": [], "pages": 1}
                              if m == "KR" else tv_scan(m, **k))
    try:
        ws.tick(store.now)
    finally:
        tv.scan = tv_scan
    rec = ws.mix_record("2026-10-12", store.now)
    assert not any(r["key"] == "Tobacco" for r in rec["rows"])


# ── 종목 주간 표 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("chg, val, upshare", [
    ([None, 10.4, 3.5, -0.7, None], [None, 100.0, 50.0, 58.0, None], 150 / 208 * 100),   # 코스맥스
    ([None, 0.9, 5.2, 2.0, None], [None, 1.0, 2.0, 3.0, None], 100.0),                 # 영원무역홀딩스
    ([None, -4.5, 0.0, -0.6, None], [None, 1.0, 2.0, 3.0, None], 0.0),                 # 동서 — 0.0 은 오른 날이 아니다
])
def test_stock_week_row_matches_the_blog_columns(chg, val, upshare):
    it = {"chg": chg, "val": val, "cap": 3.48e12}
    r = ws.stock_week_row(it, [None, 1000.0, 1000.0, 1000.0, None])
    cum = 1.0
    for c in chg:
        if c is not None:
            cum *= 1 + c / 100
    assert r["week"] == pytest.approx((cum - 1) * 100)
    assert r["upshare"] == pytest.approx(upshare)
    assert r["share"] == pytest.approx(sum(v for v in val if v) / 3000 * 100)
    assert r["dcap"] == pytest.approx(3.48e12 - 3.48e12 / cum)


def test_kosmax_market_cap_change_is_the_blog_order_of_magnitude():
    r = ws.stock_week_row({"chg": [None, 10.4, 3.5, -0.7, None], "val": [None, 1, 1, 1, None],
                           "cap": 3.48e12}, [None, 1, 1, 1, None])
    assert 4.0e11 < r["dcap"] < 4.3e11, "블로그 +4,143억(표시 등락 반올림 차이만)"


@pytest.mark.parametrize("sym, want", [
    ("KRX:005930", "005930"), ("TSE:7203", "7203.T"), ("TWSE:2330", "2330.TW"),
    ("TPEX:6488", "6488.TWO"), ("SSE:600519", "600519.SS"), ("SZSE:000001", "000001.SZ"),
    ("NYSE:BRK.B", "BRK-B"), ("NASDAQ:AAPL", "AAPL"),
])
def test_lookup_ticker_maps_exchange_prefixes(sym, want):
    assert wp.lookup_ticker(sym) == want


# ── 세 페이지(공용 픽스처 — 실제 수집 경로로 쌓은 디스크) ──────────────────────
@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    return fx.build_pages(tmp_path_factory.mktemp("wspages"))


def test_mix_page_shows_verdicts_wins_and_hides_quiet_rows(pages):
    h = pages["mix"]
    assert "<title>국가별 섹터혼합</title>" in h
    assert "이긴 날 " in h and "%p" in h
    assert re.search(r"class='ws-pill (up|dn)?'>(\d국 (공통 )?(강세|약세)|엇갈림)<", h)
    for m in ws.MARKETS:
        assert wp.market_name(m) in h
    rec = pages["recs"][0]
    quiet = [r for r in rec["rows"] if r.get("v_week") == "none"]
    if quiet:
        assert "js-ws-q" in h and "신호 약한 업종" in h
    assert "휴장" in h, "일본 10-12 는 휴장으로 적혀야 한다"
    assert "주간 ±1%p · 하루 ±0.5%p" in h


def test_mix_day_tab_comes_first_and_opens_by_default(pages):
    """사용자 2026-10-12 "하루랑 일주일의 위치를 바꿔 — 처음 들어가면 하루로". 날짜마다 탭 묶음의 첫
    탭이 '하루' 이고 그 탭·그 창이 켜져 있다. 머리줄 계수도 처음 보이는 창(하루)부터 말한다(#45) —
    그 수는 그 기록의 `v_day` 로 다시 센 값과 같아야 한다(라벨만 바꾸고 주간 수를 두는 변형을 잡는다)."""
    h = pages["mix"]
    boxes = re.findall(r"<div class='ws-tabs' data-tabs='(mix\d+)'>(.*?)</div>", h)
    assert boxes, "날짜마다 하루·주간 탭 묶음이 있다"
    for g, inner in boxes:
        tabs = re.findall(r"<span class='ws-tab( active)?' data-tab='(\w+)'>([^<]+)</span>", inner)
        assert [t[1] for t in tabs] == ["day", "week"], tabs
        assert tabs[0][0] == " active" and tabs[1][0] == "", "처음 켜지는 탭은 하루"
        assert tabs[0][2] == "하루" and tabs[1][2].startswith("주간")
        on = re.findall(rf"<div class='ws-pane( active)?' data-group='{g}' data-tab='(\w+)'>", h)
        assert on == [(" active", "day"), ("", "week")], on
    rec = pages["recs"][0]
    cnt = lambda win, ks: sum(1 for r in rec["rows"] if r.get(f"v_{win}") in ks)
    day_line = (f"하루 공통 {cnt('day', ('all_up', 'all_down'))} · 2국 이상 "
                f"{cnt('day', ('k_up', 'k_down'))} · 엇갈림 {cnt('day', ('mixed',))}")
    week_line = (f"주간 공통 {cnt('week', ('all_up', 'all_down'))} · 2국 이상 "
                 f"{cnt('week', ('k_up', 'k_down'))} · 엇갈림 {cnt('week', ('mixed',))}")
    meta = re.search(r"<span class='ws-meta'>([^<]*하루 공통[^<]*)</span>", h).group(1)
    assert meta.index(day_line) < meta.index(week_line), meta


def test_map_page_cells_tooltips_and_lazy_json(pages):
    h = pages["map"]
    assert "<title>국가별 심리지도</title>" in h
    assert "data-src='ws_KR.json'" in h, "칸을 누르면 상대 경로로 그 나라 종목을 받는다(토큰 경로)"
    assert "fetch(td.getAttribute('data-src'))" in h
    assert "ws-approx" in h, "자체 이력이 20거래일 전이면 근사 칸을 점선으로 밝힌다"
    assert re.search(r"title='[^']*주간: [^']+ — [^']+, 시장 대비 [+-]?\d+\.\d\d%p, 상대 거래대금 \d+\.\d\d배"
                     r"\([^)]+\), 상승일 거래 \d+%'", h), "블로그 툴팁처럼 설명·%p·배·상승일 거래"
    assert ">휴장<" in h
    for k in ("hot_up", "quiet_up", "calm", "churn", "fade", "hot_down"):
        assert ws.SENTIMENT[k][0] in h
    assert "규모와 쏠림" in h and "쏠림(상위 10종목 거래대금 몫)" in h
    js = pages["jsons"]["KR"]
    wk = js["weeks"][0]
    assert js["cur"] == "원" and wk["rows"] and len(wk["rows"][0]) == 11
    assert wk["rows"][0][4] >= wk["rows"][-1][4], "시총 큰 순"
    assert wk["rows"][0][10].startswith("lookup/")


def test_map_rows_are_sorted_by_weekly_relative_and_count_stocks(pages):
    wk = pages["map_payload"]["KR"]["weeks"][0]
    rels = [r["week"]["rel"] for r in wk["groups"]["sec"]]
    assert rels == sorted(rels, reverse=True)
    assert {r["n"] for r in wk["groups"]["sec"]} == {fx.N_STOCKS // 4}
    assert len(wk["groups"]["ind"]) == 8


def test_stocks_page_embeds_escaped_etf_tables(pages):
    h = pages["stocks"]
    assert "<title>국가별 섹터종목</title>" in h
    assert "XLK 기술" in h and "091160 반도체" in h
    tags = re.findall(r"<script type='application/json' id='(ws-etf-[^']+)'>(.*?)</script>", h, re.S)
    assert tags and all("</" not in body for _i, body in tags), "JSON 안의 </ 가 script 를 닫았다"
    us = pages["stocks_payload"]["US"]["etfs"][0]["tables"][0]
    assert len(us["rows"]) == 15 and us["rows"][0]["w"] == 10
    kr = pages["stocks_payload"]["KR"]["etfs"][0]["tables"][0]
    miss = next(r for r in kr["rows"] if r["c"] == "999999")
    assert miss["cap"] is None and miss["nm"] == "수집 안 된 종목", "못 찾은 보유 종목도 이름·비중은 남긴다"
    assert pages["stocks_payload"]["KR"]["weeks"] == ["10/12(월) 주 — 이번 주", "10/5(월) 주 — 지난주"]


def test_pages_escape_names(pages):
    """이름은 HTML 바깥(본문)에 날것으로 나오지 않는다 — 종목 이름은 JSON(스크립트 태그·별도
    파일)에만 실리고 JS 가 escape 해서 그린다."""
    for key in ("stocks", "map", "mix"):
        outside = re.sub(r"<script.*?</script>", "", pages[key], flags=re.S)
        leaked = "<회사>" in outside             # 큰 페이지 `not in` 단언은 실패 시 pytest diff 가 멈춘다
        assert not leaked, key
    assert any("<회사>" in r[1] for r in pages["jsons"]["KR"]["weeks"][0]["rows"]), \
        "픽스처 이름에 '<' 가 실려야 이 검사가 무언가를 잰다"


def test_regenerate_writes_only_when_content_changes(tmp_path, monkeypatch):
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "archive")
    with fx.patched(tmp_path) as fake:
        fx.simulate(fake, _at(2026, 10, 6, 0), 6)
        now = _at(2026, 10, 12, 9, 0)
        first = wp.regenerate(now)
        assert {"sector_mix.html", "sentiment_map.html", "sector_stocks.html",
                "ws_KR.json"} <= set(first)
        assert wp.regenerate(now + timedelta(minutes=15)) == [], \
            "생성 시각만 바뀐 같은 페이지를 다시 썼다(업데이트 배너 상시 점등)"


def test_one_page_failure_does_not_stop_the_others(tmp_path, monkeypatch):
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr(wp, "render_mix", lambda *a, **k: 1 / 0)
    with fx.patched(tmp_path) as fake:
        fx.simulate(fake, _at(2026, 10, 6, 0), 3)
        wrote = wp.regenerate(_at(2026, 10, 9, 9, 0))
    assert "sentiment_map.html" in wrote and "sector_mix.html" not in wrote


# ── 화면 JS(실행해서 잰다) ──────────────────────────────────────────────────────
_NODE = shutil.which("node")


def _js_eval(script: str) -> str:
    return subprocess.run([_NODE, "-e", script], capture_output=True, text=True, timeout=30).stdout


@pytest.mark.skipif(not _NODE, reason="node 없음")
def test_page_js_parses():
    p = subprocess.run([_NODE, "--check", "-"], input=wp._WS_JS, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.mark.skipif(not _NODE, reason="node 없음")
def test_stock_table_formats_money_sorts_and_marks_off_days():
    harness = r"""
    var made={};
    function El(){this.innerHTML='';this.textContent='';this.value='';}
    El.prototype.querySelector=function(s){return made[s]||(made[s]=new El());};
    El.prototype.querySelectorAll=function(){return [];};
    El.prototype.addEventListener=function(){};
    global.document={querySelectorAll:function(){return [];},getElementById:function(){return null;}};
    global.window={};
    """ + wp._WS_JS + r"""
    var el=new El();
    window.wsTable(el,{days:['a','b'],dayLabels:['월 10/5','화 10/6'],status:['closed','ok'],cur:'원',
      rows:[{c:'1',nm:'작은',ind:'x',href:'',cap:8.843e11,chg:[null,0.04],wk:-0.04,up:30,sh:0.1,dcap:5.19e10},
            {c:'2',nm:'큰<b>',ind:'y',href:'lookup/2',cap:1.84e13,chg:[null,-1.2],wk:13.46,up:100,sh:0.41,dcap:-2.9e9}]},{});
    console.log(made['.js-ws-body'].innerHTML);
    """
    out = _js_eval(harness)
    assert out.index("큰&lt;b&gt;") < out.index("작은"), "시총 큰 순 · 이름은 escape"
    assert "18.4조 원" in out and "8,843억 원" in out and "+519억 원" in out and "-29억 원" in out
    assert ">0.0<" in out and "-0.0" not in out, "반올림해 0 이면 부호를 붙이지 않는다"
    assert "ws-off" in out and "+13.5%" in out and "30%" in out


# ── ETF 보유 목록 ───────────────────────────────────────────────────────────
def _ssga_xlsx(rows, weight="Weight") -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws_ = wb.active
    ws_.append(["Fund Name:", "The Technology Select Sector SPDR Fund"])
    ws_.append(["Ticker Symbol:", "XLK"])
    ws_.append(["Holdings:", "As of 09-Oct-2026"])
    ws_.append([])
    ws_.append(["Name", "Ticker", "Identifier", "SEDOL", weight, "Sector", "Shares Held",
                "Local Currency"])
    for r in rows:
        ws_.append(r)
    ws_.append([])
    ws_.append(["Past performance is no guarantee"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_ssga_holdings_parse_finds_the_header_and_drops_cash():
    rows = [[f"Co{i}", f"T{i}", "x", "y", 20 - i, "Information Technology", 1, "USD"] for i in range(18)]
    rows.insert(3, ["US DOLLAR", "-", "x", "y", 50.0, "Unassigned", 1, "USD"])
    rows.insert(5, ["SSI MONEY", "CASH_USD", "x", "y", 40.0, "Unassigned", 1, "USD"])
    top, asof = eh.parse_ssga_xlsx(_ssga_xlsx(rows))
    assert len(top) == 15 and top[0] == {"code": "T0", "name": "Co0", "weight": 20}
    assert all(t["code"] not in ("-", "CASH_USD") for t in top)
    assert asof == "09-Oct-2026"
    with pytest.raises(eh.HoldingsError):
        eh.parse_ssga_xlsx(_ssga_xlsx(rows, weight="Percent"))


def _shrink_dimension(data: bytes, ref: str) -> bytes:
    """엑셀의 '사용 범위'(dimension) 만 바꾼 사본 — 그 값이 틀린 파일을 흉내 낸다."""
    import re as _re
    import zipfile
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for it in zin.infolist():
            b = zin.read(it.filename)
            if it.filename.startswith("xl/worksheets/sheet"):
                b, n = _re.subn(rb'<dimension ref="[^"]+"/>', f'<dimension ref="{ref}"/>'.encode(), b)
                assert n == 1
            zout.writestr(it, b)
    return buf.getvalue()


def test_ssga_rows_with_a_dash_sector_are_holdings_not_cash():
    """2026-10-12 미국 11개 ETF 가 전부 '엑셀에 보유 종목 행이 없음' — 섹터 칸 '-' 를 현금으로 읽어
    전 행을 버리는 필터가 그 증상을 낸다(원인 후보 하나, VM `--probe` 가 확정). 현금은 티커로 가른다."""
    rows = [[f"Co{i}", f"T{i}", "x", "y", 20 - i, "-", 1, "USD"] for i in range(18)]
    rows.append(["US DOLLAR", "-", "x", "y", 50.0, "-", 1, "USD"])
    rows.append(["CASH", "CASH_USD", "x", "y", 40.0, "-", 1, "USD"])
    top, _ = eh.parse_ssga_xlsx(_ssga_xlsx(rows))
    assert len(top) == 15 and top[0]["code"] == "T0"
    assert all(t["code"] not in ("-", "CASH_USD") for t in top), "티커 '-'·CASH_ 는 여전히 현금"


def test_ssga_weight_written_as_text_with_percent_or_comma_is_read():
    rows = [["A", "AAA", "x", "y", "14.48%", "IT", 1, "USD"],
            ["B", "BBB", "x", "y", "1,203.5", "IT", 1, "USD"],
            ["C", "CCC", "x", "y", " 3.1 ", "IT", 1, "USD"],
            ["D", "DDD", "x", "y", "n/a", "IT", 1, "USD"]]
    top, _ = eh.parse_ssga_xlsx(_ssga_xlsx(rows))
    assert [(t["code"], t["weight"]) for t in top] == [("BBB", 1203.5), ("AAA", 14.48), ("CCC", 3.1)]


def test_ssga_reads_past_a_wrong_used_range():
    """읽기 전용 모드는 파일이 적은 사용 범위만 읽는다 — 머리행까지만 적힌 파일은 보유 0행이 된다
    (같은 증상의 다른 원인 후보). 다시 재서 끝까지 읽고, 진단은 그 갈래를 말한다."""
    rows = [[f"Co{i}", f"T{i}", "x", "y", 20 - i, "IT", 1, "USD"] for i in range(5)]
    data = _shrink_dimension(_ssga_xlsx(rows), "A1:H5")
    top, _ = eh.parse_ssga_xlsx(data)
    assert [t["code"] for t in top] == [f"T{i}" for i in range(5)]
    r = eh.scan_ssga_xlsx(data)
    assert r["declared_rows"] == 5 and r["rows"] > 5


def test_ssga_zero_rows_says_why():
    """0행이면 '없음' 이 아니라 읽은 행 수와 버린 갈래를 사유로(#82) — 화면 ETF 칸에 실린다."""
    rows = [[f"Co{i}", "-", "x", "y", 20 - i, "IT", 1, "USD"] for i in range(3)]
    rows += [[f"M{i}", f"M{i}", "x", "y", "?", "IT", 1, "USD"] for i in range(2)]
    rows += [["CASH", "CASH_USD", "x", "y", 1.0, "IT", 1, "USD"],
             ["FUT", "ESZ6", "x", "y", 1.0, "Unassigned", 1, "USD"]]
    with pytest.raises(eh.HoldingsError) as ei:
        eh.parse_ssga_xlsx(_ssga_xlsx(rows))
    why = ei.value.reason
    assert "읽은 행 7" in why and "티커 없음 3" in why and "비중 못 읽음 2" in why
    assert "현금(티커 CASH_·섹터 Unassigned) 2" in why


class _GetSess:
    def __init__(self, bodies):
        self.bodies, self.urls = bodies, []

    def get(self, url, **k):
        self.urls.append(url)
        body = self.bodies[len(self.urls) - 1]

        class R:
            status_code, content = (200, body) if isinstance(body, bytes) else (body, b"")
        return R()


def test_probe_reads_only_and_says_which_branch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    good = _ssga_xlsx([[f"Co{i}", f"T{i}", "x", "y", 20 - i, "-", 1, "USD"] for i in range(4)])
    short = _shrink_dimension(good, "A1:H5")
    s = _GetSess([good, short, 503])
    lines = []
    rc = eh.probe(["xlk", "XLB", "XLE"], session=s, out=lines.append)
    assert rc == 1 and [u.rsplit("-", 1)[-1] for u in s.urls] == ["xlk.xlsx", "xlb.xlsx", "xle.xlsx"]
    txt = "\n".join(lines)
    assert "상위: T0 20.00 · T1 19.00 · T2 18.00" in txt
    assert txt.count("⚠️ 파일이 적은 사용 범위") == 1 and "사용 범위 5행" in txt, "XLB 만 범위가 틀렸다"
    assert "❌ XLE 받기 실패" in txt and lines[-1] == "❌ 2/3개 ETF 보유 목록을 읽음"
    assert "── XLK ·" in txt, "티커는 대문자로 찍는다"
    assert not (tmp_path / "h").exists(), "진단이 보유 캐시를 썼다"
    lines.clear()
    assert eh.probe(["XLK"], session=_GetSess([good]), out=lines.append) == 0
    assert lines[-1] == "✅ 1/1개 ETF 보유 목록을 읽음"


def test_probe_counts_a_zero_row_file_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    cash_only = _ssga_xlsx([["US DOLLAR", "-", "x", "y", 99.0, "-", 1, "USD"]])
    lines = []
    assert eh.probe(["XLU"], session=_GetSess([cash_only]), out=lines.append) == 1
    assert "❌ XLU 보유 0행" in lines and lines[-1] == "❌ 0/1개 ETF 보유 목록을 읽음"


def test_ssga_boolean_weight_cell_is_not_a_weight():
    rows = [["A", "AAA", "x", "y", True, "IT", 1, "USD"], ["B", "BBB", "x", "y", 3.0, "IT", 1, "USD"]]
    assert [t["code"] for t in eh.parse_ssga_xlsx(_ssga_xlsx(rows))[0]] == ["BBB"]


def test_ssga_double_dash_ticker_and_empty_name(caplog):
    rows = [["A", "--", "x", "y", 9.0, "IT", 1, "USD"], ["", "BBB", "x", "y", 3.0, "IT", 1, "USD"]]
    top, _ = eh.parse_ssga_xlsx(_ssga_xlsx(rows))
    assert top == [{"code": "BBB", "name": "BBB", "weight": 3.0}], "이름이 비면 티커로"
    import logging
    with caplog.at_level(logging.WARNING, logger="bot.etf_holdings"):
        with pytest.raises(eh.HoldingsError):
            eh.parse_ssga_xlsx(_ssga_xlsx([["A", "--", "x", "y", 9.0, "IT", 1, "USD"]]))
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "SSGA 보유 0행" in msg and "'--'" in msg, "0행이면 머리행·첫 행을 로그에(VM 에서 원인을 본다)"


def test_probe_prints_the_first_raw_row(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    data = _ssga_xlsx([["Apple Inc", "AAPL", "x", "y", 9.0, "-", 1, "USD"]])
    lines = []
    eh.probe(["XLK"], session=_GetSess([data]), out=lines.append)
    assert any("첫 행 원문: ['Apple Inc', 'AAPL'" in ln for ln in lines)
    assert not any("⚠️ 파일이 적은 사용 범위" in ln for ln in lines), "범위가 맞는 파일엔 경고 없음"


def test_ssga_scan_counts_every_row_to_the_end():
    """'적힌 범위 < 실제 행' 은 실제 행을 **끝까지** 세야 잰다 — 표 끝에서 멈추면 고지문 줄이 빠져
    범위가 맞는 파일과 틀린 파일이 같아 보인다(델타 리뷰)."""
    rows = [[f"Co{i}", f"T{i}", "x", "y", 20 - i, "IT", 1, "USD"] for i in range(3)]
    r = eh.scan_ssga_xlsx(_ssga_xlsx(rows))
    assert r["rows"] == 5 + 3 + 2 and r["read"] == 3, "머리 5줄 + 보유 3 + 빈 줄 + 고지문"


def test_kr_parse_strips_names_and_warns_on_a_big_unlisted_stock(caplog, monkeypatch):
    import logging
    df = _kr_pdf(["005930", "005931", "010010"], [" 삼성전자 ", "삼성전자우", "원화예금"], [50.0, 30.0, 20.0])
    df["비중"] = [50.0, 30.0, 20.0]
    drops: dict = {}
    top = eh.parse_kr_pdf(df, listed=frozenset({"005930"}), drops=drops)
    assert top[0]["name"] == "삼성전자", "이름 앞뒤 공백"
    assert drops["big_unlisted"] == ["삼성전자우(005931 30.0%)"], "현금처럼 안 보이는 큰 행만"


def test_fetch_kr_logs_the_big_unlisted_rows_and_the_fallback(monkeypatch, caplog):
    import logging
    import sys
    import types
    import bot.pykrx_client as pk
    monkeypatch.setattr(pk, "krx_login_ready", lambda: True)
    df = _kr_pdf(["005930", "005931"], ["삼성전자", "삼성전자우"], [50.0, 30.0])
    df["비중"] = [60.0, 40.0]
    fake = types.ModuleType("pykrx")
    fake.stock = types.SimpleNamespace(get_etf_portfolio_deposit_file=lambda c, d: df)
    monkeypatch.setitem(sys.modules, "pykrx", fake)
    monkeypatch.setattr(eh, "kr_listed", lambda d: (frozenset({"005930"}), "목록 1종목"))
    with caplog.at_level(logging.WARNING, logger="bot.etf_holdings"):
        rows, _ = eh.fetch_kr("091160.KS", "2026-10-12")
    assert [r["code"] for r in rows] == ["005930"]
    assert "삼성전자우(005931 40.0%)" in caplog.text and "목록 1종목" in caplog.text
    caplog.clear()
    monkeypatch.setattr(eh, "kr_listed", lambda d: (None, "KRX 상장 목록을 못 구함(x)"))
    with caplog.at_level(logging.WARNING, logger="bot.etf_holdings"):
        eh.fetch_kr("091160.KS", "2026-10-12")
    assert "상장 목록 없이 코드 모양·이름으로 거른다" in caplog.text


def test_kr_listed_keeps_a_success_and_bounds_its_memory(monkeypatch):
    from bot import daily_inst as di
    monkeypatch.setattr(eh, "_LISTED", {})
    calls = []
    monkeypatch.setattr(di, "cached_mcap", lambda d: None)
    monkeypatch.setattr(di, "fetch_mcap", lambda d: calls.append(d) or ({"005930": 1.0}, "", "ok"))
    eh.kr_listed("2026-10-12", now=0)
    eh.kr_listed("2026-10-12", now=10 * eh._LISTED_FAIL_SEC)
    assert calls == ["2026-10-12"], "받은 목록은 다시 안 묻는다(그날 상장 목록은 안 바뀐다)"
    for i in range(eh._LISTED_KEEP + 5):
        eh.kr_listed(f"2026-09-{i + 1:02d}", now=0)
    assert len(eh._LISTED) == eh._LISTED_KEEP


def test_probe_cli_dispatch(monkeypatch):
    seen = []
    monkeypatch.setattr(eh, "probe", lambda t=None, **k: seen.append(t) or 0)
    assert eh.main(["--probe"]) == 0 and eh.main(["--probe", "XLK"]) == 0
    assert seen == [None, ["XLK"]]
    assert eh.main([]) == 2


def _kr_pdf(codes, names, amounts):
    import pandas as pd
    return pd.DataFrame({"구성종목명": names, "금액": amounts, "비중": [0.0] * len(codes)},
                        index=codes)


def test_kr_pdf_parse_ranks_by_weight_or_value_and_drops_non_stock_rows():
    """pykrx 는 KRX 가 ISIN 으로 준 코드를 `[3:9]` 로 자른다 — 원화예금 `KRD010010001` 은
    **`010010`** 으로 와 '6자리 숫자' 규칙을 통과한다(옛 픽스처는 자르기 전 모양이라 이걸 못 봤다,
    #155). 종목인지는 KRX 상장 목록으로 가른다."""
    import pandas as pd
    df = _kr_pdf(["005930", "010010", "000660", "042700", "101V30"],
                 ["삼성전자", "원화예금", "SK하이닉스", "한미반도체", "코스피200 F 202612"],
                 [500.0, 900.0, 300.0, 200.0, 50.0])
    listed = frozenset({"005930", "000660", "042700"})
    drops: dict = {}
    top = eh.parse_kr_pdf(df, listed=listed, drops=drops)
    assert [t["code"] for t in top] == ["005930", "000660", "042700"]
    assert top[0]["weight"] == pytest.approx(50.0), "비중이 0 으로 오면 금액으로 다시 만든다"
    assert drops["not_listed"] == 2
    # 상장 목록을 못 구했을 때 — 코드 모양 + 이름으로 물러선다(잘린 현금 코드는 이름이 거른다)
    drops = {}
    top = eh.parse_kr_pdf(df, drops=drops)
    assert [t["code"] for t in top] == ["005930", "000660", "042700"]
    assert drops == {"not_listed": 0, "code_shape": 1, "cash_name": 1}
    assert eh.parse_kr_pdf(pd.DataFrame()) == []


def test_kr_listed_prefers_the_daily_inst_cache_then_asks_krx_once(monkeypatch):
    from bot import daily_inst as di
    monkeypatch.setattr(eh, "_LISTED", {})
    cache = {"2026-10-08": {"005930": 1.0, "000660": 2.0}}
    monkeypatch.setattr(di, "cached_mcap", lambda d: cache.get(d))
    calls = []
    monkeypatch.setattr(di, "fetch_mcap", lambda d: calls.append(d) or (None, "응답이 비었다", "empty"))
    got, src = eh.kr_listed("2026-10-12", now=0)
    assert got == {"005930", "000660"} and "2026-10-08" in src and calls == [], "7일 안 캐시 — 네트워크 0"
    got, src = eh.kr_listed("2026-10-20", now=0)        # 10-13~10-20 캐시 없음 → KRX 1콜
    assert got is None and calls == ["2026-10-20"] and "응답이 비었다" in src
    eh.kr_listed("2026-10-20", now=60)
    assert calls == ["2026-10-20"], "못 구한 결과는 잠시 기억한다(ETF 13개가 13번 두드리지 않게)"
    monkeypatch.setattr(di, "fetch_mcap", lambda d: calls.append(d) or ({"005930": 1.0}, "", "ok"))
    got, src = eh.kr_listed("2026-10-20", now=eh._LISTED_FAIL_SEC + 1)
    assert got == {"005930"} and calls[-1] == "2026-10-20" and "KRX 2026-10-20" in src, "영구히 굳지 않는다"


def test_fetch_kr_zero_rows_says_why(monkeypatch):
    import sys
    import types
    import bot.pykrx_client as pk
    monkeypatch.setattr(pk, "krx_login_ready", lambda: True)
    df = _kr_pdf(["010010", "101V30"], ["원화예금", "코스피200 F"], [1.0, 1.0])
    fake = types.ModuleType("pykrx")
    fake.stock = types.SimpleNamespace(get_etf_portfolio_deposit_file=lambda c, d: df)
    monkeypatch.setitem(sys.modules, "pykrx", fake)
    monkeypatch.setattr(eh, "kr_listed", lambda d: (frozenset({"005930"}), "시가총액 목록 2종목"))
    with pytest.raises(eh.HoldingsError) as ei:
        eh.fetch_kr("091160.KS", "2026-10-12")
    why = ei.value.reason
    assert "구성종목 2행 중 종목이 없음" in why and "KRX 상장 목록에 없음(현금·선물·채권 등) 2" in why
    assert "대조: 시가총액 목록 2종목" in why
    fake.stock = types.SimpleNamespace(get_etf_portfolio_deposit_file=lambda c, d: df.iloc[0:0])
    with pytest.raises(eh.HoldingsError) as ei:
        eh.fetch_kr("091160.KS", "2026-10-12")
    assert "빈 표" in ei.value.reason, "원천이 빈 표를 준 것과 우리가 다 버린 것은 다른 사실(#82)"


def test_holdings_refresh_once_a_day_and_keep_the_last_list_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술"})
    calls = []

    def ok(t, session=None):
        calls.append(t)
        return [{"code": "AAPL", "name": "Apple", "weight": 20.0}], "As of x"
    monkeypatch.setattr(eh, "fetch_us", ok)
    now = _at(2026, 10, 12, 8, 0)
    assert eh.refresh("US", now)["ok"] == ["XLK"]
    eh.refresh("US", now + timedelta(hours=3))
    assert calls == ["XLK"], "하루 한 번"

    def bad(t, session=None):
        raise eh.HoldingsError("원천이 HTTP 503 — 원천 장애")
    monkeypatch.setattr(eh, "fetch_us", bad)
    eh.refresh("US", now + timedelta(days=1))
    h = eh.load("XLK")
    assert h["rows"][0]["code"] == "AAPL" and "503" in h["error"]
    assert h["fetched_day"] == "2026-10-12" and h["tried_day"] == "2026-10-13"


def test_fetch_us_rejects_a_non_excel_body(monkeypatch):
    class R:
        status_code, content = 200, b"<html>login</html>"

    class S:
        def get(self, *a, **k):
            return R()
    with pytest.raises(eh.HoldingsError) as ei:
        eh.fetch_us("XLK", session=S())
    assert "엑셀이 아닌" in ei.value.reason


def test_etf_list_is_the_breadth_strategy_list():
    from bot.market_timing import _BREADTH_SECTORS
    assert eh.etfs("US") == _BREADTH_SECTORS["US"] and eh.etfs("KR") == _BREADTH_SECTORS["KR"]


# ── 감사·진단 ──────────────────────────────────────────────────────────────
def test_audit_lines_use_the_screen_verdict(store):
    from bot.scripts.board_audit import _world_sectors_lines
    store.now = _at(2026, 10, 12, 18, 40)
    ws.tick(store.now)
    lines = _world_sectors_lines(now=store.now)
    assert len(lines) == len(ws.MARKETS) + 2, "나라별 수집 + 한·미 섹터 ETF 보유 목록"
    assert lines[0].startswith("   ✅ KR") and "2026-10-12 받음" in lines[0]
    page = wp.status_lines(ws.load_state(), store.now)
    assert page[0].endswith("2026-10-12 받음"), "감사와 화면이 같은 줄을 말한다"


def test_why_is_read_only_and_reports_each_market(store, capsys):
    store.now = _at(2026, 10, 12, 18, 40)
    ws.tick(store.now)
    n = len(store.calls)
    rc = ws.why(store.now)
    out = capsys.readouterr().out
    assert len(store.calls) == n, "진단이 원천을 불렀다"
    assert "지문" in out and "② KR" in out and "② CN_A" in out
    assert rc == 0


def test_probe_writes_nothing(store, capsys):
    store.now = _at(2026, 10, 12, 18, 40)
    rc = ws.probe("KR")
    out = capsys.readouterr().out
    assert rc == 0 and "✅ KR" in out and "표본" in out
    assert not ws.agg_path("KR", "2026-10-12").exists() and not ws._STATE.exists()


# ── 후속(2026-10-12): 메모리·재생성 주기·한글 종목명·보유 목록 재시도 ─────────────
def test_snapshot_cache_keeps_only_a_few_and_is_lru(store):
    """하루치 원본은 미국 한 장이 수 MB — 캐시가 64장을 들고 있으면 수백 MB 가 상주한다."""
    fx.simulate(store, _at(2026, 9, 28, 0), 12)
    dates = ws.stored_dates("KR")
    assert len(dates) >= 8, dates
    for d in dates:
        assert ws.load_snapshot("KR", d) is not None
    assert len(ws._CACHE["snap"]) <= 6, "하루치 원본 캐시는 몇 장만"
    # LRU — 다시 읽은 장은 맨 뒤로 가서 다음 축출에서 살아남는다
    keep = dates[-6]
    ws.load_snapshot("KR", keep)
    ws.load_snapshot("US", ws.stored_dates("US")[0])
    assert any(k.endswith(f"{keep}.json.gz") for k in ws._CACHE["snap"])
    for d in dates:
        ws.load_agg("KR", d)
    assert len(ws._CACHE["agg"]) >= len(dates), "업종 합계는 작아서 넉넉히 둔다"


def test_regenerate_runs_only_when_something_changed_or_an_hour_passed(store, monkeypatch):
    fx.simulate(store, _at(2026, 9, 28, 0), 15)
    calls = []
    monkeypatch.setattr(wp, "regenerate", lambda now=None: calls.append(now) or ["x.html"])
    t0 = _at(2026, 10, 13, 9, 30)
    store.now = t0
    r1 = ws.tick_and_render(t0)                    # 첫 호출 = 기록이 없으니 그린다
    assert r1["regenerated"] and len(calls) == 1
    store.now = t0 + timedelta(minutes=15)
    r2 = ws.tick_and_render(store.now)            # 받은 것도 보유 목록 변화도 없음
    assert r2["regenerated"] is False and r2["wrote"] == [] and len(calls) == 1
    store.now = t0 + timedelta(minutes=61)
    assert ws.tick_and_render(store.now)["regenerated"] and len(calls) == 2, "한 시간마다는 그린다"
    store.now = t0 + timedelta(minutes=70)
    assert ws.maybe_regenerate(store.now, changed=True) == ["x.html"] and len(calls) == 3


def test_regenerate_follows_a_status_line_change(store, monkeypatch):
    """대기 → 놓침 같은 시각 전이는 새 데이터 없이도 화면 상태 줄을 바꾼다 — 한 시간 기다리지 않는다."""
    calls = []
    monkeypatch.setattr(wp, "regenerate", lambda now=None: calls.append(now) or [])
    seq = iter([("wait", "a")] * 5 + [("missed", "b")] + [("wait", "a")] * 4)
    monkeypatch.setattr(ws, "market_verdict", lambda m, st, now=None: next(seq))
    t0 = _at(2026, 10, 13, 9, 30)
    assert ws.maybe_regenerate(t0, changed=False) == [] and len(calls) == 1
    assert ws.maybe_regenerate(t0 + timedelta(minutes=15), changed=False) is not None
    assert len(calls) == 2


def test_busy_cycle_does_not_regenerate_every_time(store, monkeypatch):
    calls = []
    monkeypatch.setattr(wp, "regenerate", lambda now=None: calls.append(now) or [])
    t0 = _at(2026, 10, 13, 9, 30)
    assert ws._TICK_LOCK.acquire(blocking=False)
    try:
        assert ws.tick_and_render(t0)["status"] == "busy" and len(calls) == 1
        assert ws.tick_and_render(t0 + timedelta(minutes=15))["regenerated"] is False
        assert len(calls) == 1
    finally:
        ws._TICK_LOCK.release()


def test_regenerate_skips_while_another_thread_draws(store, monkeypatch):
    calls = []
    monkeypatch.setattr(wp, "regenerate", lambda now=None: calls.append(now) or [])
    assert ws._REGEN_LOCK.acquire(blocking=False)
    try:
        assert ws.maybe_regenerate(_at(2026, 10, 13, 9, 30), changed=True) is None
    finally:
        ws._REGEN_LOCK.release()
    assert calls == []


def test_holdings_retry_every_two_hours_up_to_four_times(tmp_path, monkeypatch):
    """일시 장애 한 번이 하루를 비우지 않게 — 그러나 죽은 원천을 15분마다 두드리지도 않게."""
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술"})
    calls = []

    def bad(t, session=None):
        calls.append(t)
        raise eh.HoldingsError("원천이 HTTP 503 — 원천 장애")
    monkeypatch.setattr(eh, "fetch_us", bad)
    t0 = _at(2026, 10, 12, 8, 0)
    for mins, want in ((0, 1), (60, 1), (119, 1), (120, 2), (240, 3), (360, 4), (480, 4), (600, 4)):
        eh.refresh("US", t0 + timedelta(minutes=mins))
        assert len(calls) == want, (mins, calls)
    h = eh.load("XLK")
    assert h["tries"] == 4
    assert eh.retry_note(h, t0 + timedelta(hours=11)) == "오늘 4회 실패 — 내일 다시 묻습니다"
    eh.refresh("US", _at(2026, 10, 13, 8, 0))
    assert len(calls) == 5 and eh.load("XLK")["tries"] == 1, "다음 날은 처음부터"


def test_holdings_retry_note_says_when_and_how_many(tmp_path, monkeypatch):
    h = {"tried_day": "2026-10-12", "tries": 2, "tried_at": "2026-10-12T10:00:00+09:00"}
    now = _at(2026, 10, 12, 10, 30)
    assert eh.retry_note(h, now) == "오늘 2/4회 실패 · 다음 시도 12:00"
    assert eh.retry_note(h, _at(2026, 10, 13, 0, 5)) == "다음 주기에 다시 묻습니다"
    assert eh.retry_note({**h, "tried_at": "?"}, now) == "오늘 2/4회 실패"


def test_holdings_failure_shows_retry_on_the_page(store):
    """화면이 '오늘 실패' 만 적으면 기다리면 되는지 모른다(#380) — 다음 시도를 같이."""
    def bad(t, session=None):
        raise eh.HoldingsError("원천이 HTTP 503 — 원천 장애")
    fx.simulate(store, _at(2026, 9, 28, 0), 15)
    now = _at(2026, 10, 13, 9, 30)
    store.now = now
    eh.fetch_us = bad                       # patched() 가 끝나면 되돌린다
    ws._refresh_holdings(now)
    sp = wp.stocks_payloads(now)
    meta = sp["US"]["etfs"][0]["meta"]
    assert "보유 목록 갱신 실패(원천이 HTTP 503" in meta and "다음 시도 11:30" in meta, meta


# ── 앞선 판 코드가 남긴 실패(2026-10-12 VM: 19:27 배포 뒤에도 18:52 옛 판 실패 때문에 20:52 까지
#    미국 11개가 '보유 종목 행이 없음') — 지문이 다르거나 없는 실패는 다음 주기에 바로 다시 묻는다.
def _code_placed(monkeypatch, now, minutes):
    """이 코드(`etf_holdings.py`)가 디스크에 놓인 지 `minutes` 분 — 실제 mtime 은 실행 날짜에 달려
    고정 `now` 와 섞으면 결과가 흔들린다(#249). None 이면 못 잼."""
    monkeypatch.setattr(eh, "_code_mtime",
                        (lambda: None) if minutes is None else (lambda: now.timestamp() - minutes * 60))


def test_prior_code_failure_is_retried_at_once_and_counted_afresh(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술", "XLF": "금융"})
    old = {"tried_day": "2026-10-12", "tries": 2, "error": "엑셀에 보유 종목 행이 없음",
           "tried_at": "2026-10-12T18:52:00+09:00"}           # 지문 없음 = 이 기능 전의 판
    eh._save("XLK", old)
    eh._save("XLF", {**old, "sig": "0ldc0de000"})              # 다른 지문 = 다른 판
    calls = []

    def bad(t, session=None):
        calls.append(t)
        raise eh.HoldingsError("원천이 HTTP 503 — 원천 장애")
    monkeypatch.setattr(eh, "fetch_us", bad)
    now = _at(2026, 10, 12, 19, 30)                            # 옛 판의 2시간 간격(20:52) 전
    eh.refresh("US", now)
    assert sorted(calls) == ["XLF", "XLK"], "앞선 판의 실패는 간격을 기다리지 않는다"
    for t in ("XLK", "XLF"):
        h = eh.load(t)
        assert h["tries"] == 1 and h["sig"] == eh.CODE_SIG and "503" in h["error"], h
        assert not eh.prior_failure(h)
        assert eh.retry_note(h, now) == "오늘 1/4회 실패 · 다음 시도 21:30", "지금 판의 실패는 간격을 지킨다"
    eh.refresh("US", now + timedelta(minutes=15))
    assert len(calls) == 2, "지금 판의 실패는 바로 다시 묻지 않는다"


def test_prior_code_failure_at_the_daily_cap_is_still_retried(tmp_path, monkeypatch):
    """하루 상한(`MAX_TRIES`)을 다 쓴 옛 판 기록도 — 상한은 '같은 코드로 몇 번 물었나' 다."""
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술"})
    eh._save("XLK", {"tried_day": "2026-10-12", "tries": eh.MAX_TRIES, "error": "옛 사유",
                     "tried_at": "2026-10-12T18:52:00+09:00", "sig": "0ldc0de000"})
    monkeypatch.setattr(eh, "fetch_us",
                        lambda t, session=None: ([{"code": "AAPL", "name": "Apple", "weight": 20.0}], "As of x"))
    assert eh.refresh("US", _at(2026, 10, 12, 19, 30))["ok"] == ["XLK"]
    h = eh.load("XLK")
    assert h["error"] == "" and h["sig"] == eh.CODE_SIG and h["fetched_day"] == "2026-10-12"
    assert not eh.prior_failure(h), "성공 기록은 앞선 판 실패가 아니다"


def test_prior_failure_needs_an_error_and_a_different_signature():
    assert not eh.prior_failure({})
    assert not eh.prior_failure({"error": "", "sig": "x"}), "실패가 아니면 판을 묻지 않는다"
    assert eh.prior_failure({"error": "e"}), "지문이 없는 실패 = 이 기능 전의 판"
    assert eh.prior_failure({"error": "e", "sig": "x" + eh.CODE_SIG})
    assert not eh.prior_failure({"error": "e", "sig": eh.CODE_SIG})


def test_code_sig_is_the_source_fingerprint_and_reacts_to_edits(tmp_path, monkeypatch):
    """지문은 **이 모듈 소스**에서 나와야 배포가 그것을 바꾼다 — 상수면 옛 판의 실패를 영영 못
    알아본다(뮤테이션 'CODE_SIG = 상수' 가 이 파일의 다른 테스트를 전부 통과했다, #365·#91b).
    레포 파일엔 쓰지 않는다 — mtime 도 부작용이다(#365a)."""
    assert eh.CODE_SIG == eh._source_sig(), "import 시점 지문 = 지금 디스크의 이 모듈 소스"
    f = tmp_path / "etf_holdings.py"
    f.write_text("a = 1\n")
    monkeypatch.setattr(eh, "__file__", str(f))
    assert not eh.prior_failure({"error": "e", "sig": eh.CODE_SIG}), \
        "판정은 import 시점 지문으로 — 디스크를 다시 재면 재시작 못 한 옛 봇이 새 지문인 척한다(#426)"
    s1 = eh._source_sig()
    f.write_text("a = 2\n")
    s2 = eh._source_sig()
    assert s1 != s2 and len(s1) == 10, "소스가 바뀌면 지문도 바뀐다"
    f.unlink()
    assert eh._source_sig() == "nosig", "못 읽으면 지어내지 않고 그렇다고 적는다"


def test_prior_failure_shows_as_retry_on_screen_why_and_audit(tmp_path, monkeypatch, capsys):
    """화면·`--why`·감사가 같은 갈래를 말한다 — 옛 판의 사유를 '지금 실패(❌)' 로 적지 않고
    '지금 코드로 다시 묻는다(⏳)' 로. 지금 판의 실패가 하나라도 있으면 그쪽(❌)이 이긴다."""
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술", "XLF": "금융"})
    now = _at(2026, 10, 12, 20, 6)
    _code_placed(monkeypatch, now, 10)
    old = {"rows": [], "tried_day": "2026-10-12", "tries": 2, "error": "엑셀에 보유 종목 행이 없음",
           "tried_at": "2026-10-12T18:52:00+09:00"}
    eh._save("XLK", old)
    eh._save("XLF", old)
    kind, line = eh.holdings_verdict("US", now)
    assert kind == "retry" and ws.VERDICT_MARK[kind] == "⏳", (kind, line)
    assert "2/2" in line and "이전 판 코드" in line and "엑셀에 보유 종목 행이 없음" in line, line
    assert "그중 보유 목록이 없는 것 2개" in line, "목록이 없다는 사실을 ⏳ 줄도 센다(#45)"
    assert eh.retry_note(old, now) == "이전 판 코드가 남긴 실패 기록 — 다음 주기에 지금 코드로 다시 묻습니다"
    from bot.scripts import board_audit as ba
    monkeypatch.setattr(ws, "HOLD_MARKETS", ("US",))
    monkeypatch.setattr(ws, "MARKETS", ())
    lines = ba._world_sectors_lines(state={}, now=now)
    assert any(x.strip().startswith("⏳ US 섹터 ETF 보유 목록") for x in lines), lines
    assert not any("❌" in x for x in lines), "감사가 옛 판의 실패를 ❌ 로 세지 않는다"
    eh._save("XLF", {**old, "sig": eh.CODE_SIG, "error": "원천이 HTTP 503"})
    kind, line = eh.holdings_verdict("US", now)
    assert kind == "fail" and "XLF 외 1개: 원천이 HTTP 503" in line, "지금 판의 실패가 머리다"
    assert "보유 목록이 없는 ETF 2/2개" in line, \
        "수는 목록이 없는 ETF 전부 — 옛 판 기록을 빼고 세면 11개 중 1개로 보인다(독립 리뷰, #45)"
    assert line.endswith("이전 판 코드의 기록 1개는 다음 주기에 지금 코드로 다시 묻는다(그중 보유 목록이 없는 것 1개)"), line


def test_prior_failure_is_named_on_the_page_and_in_why(store, monkeypatch, capsys):
    """같은 사실을 화면 머리줄·표 본문과 `--why` 줄이 같이 말한다(`prior_note` 한 곳, #38) — 그리고
    기한 안의 옛 판 기록은 `--why` 의 rc 를 올리지 않는다(기다리면 되는 것, ⏳)."""
    fx.simulate(store, _at(2026, 9, 28, 0), 15)
    now = _at(2026, 10, 13, 9, 30)
    store.now = now
    _code_placed(monkeypatch, now, 10)
    t = list(eh.etfs("US"))[0]
    rc0 = ws.why(now)                                          # 같은 상태에서 그 ETF 만 아직 안 물음
    capsys.readouterr()
    eh._save(t, {"rows": [], "tried_day": "2026-10-13", "tries": 2, "error": "엑셀에 보유 종목 행이 없음",
                 "tried_at": "2026-10-13T08:52:00+09:00"})
    e0 = wp.stocks_payloads(now)["US"]["etfs"][0]
    assert "보유 목록 갱신 실패(엑셀에 보유 종목 행이 없음) — 이전 판 코드가 남긴 실패 기록" in e0["meta"], e0
    assert e0["empty"].startswith("엑셀에 보유 종목 행이 없음 (이전 판 코드가 남긴 실패 기록"), \
        "표 본문도 옛 판의 사유를 '지금 실패' 처럼 적지 않는다(독립 리뷰)"
    eh._save(t, {"rows": [{"code": "AAPL", "name": "Apple", "weight": 20.0}], "fetched_day": "2026-10-12",
                 "tried_day": "2026-10-13", "tries": 1, "error": "엑셀에 보유 종목 행이 없음",
                 "tried_at": "2026-10-13T08:52:00+09:00"})
    e1 = wp.stocks_payloads(now)["US"]["etfs"][0]
    assert e1["empty"] == "해당 종목 없음", "목록이 있으면 표 본문은 목록 이야기 — 옛 사유를 끼우지 않는다"
    eh._save(t, {"rows": [], "tried_day": "2026-10-13", "tries": 2, "error": "엑셀에 보유 종목 행이 없음",
                 "tried_at": "2026-10-13T08:52:00+09:00"})
    rc = ws.why(now)
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if x.strip().startswith(f"{t}:"))
    assert line.endswith("이전 판 코드가 남긴 실패 기록 — 다음 주기에 지금 코드로 다시 묻습니다"), line
    assert any(x.startswith("③ US ETF 보유 목록 ⏳") for x in out.splitlines()), out
    assert rc == rc0, "기한 안의 옛 판 기록은 고칠 실패가 아니다 — rc 를 올리지 않는다"


def test_prior_failure_past_the_deadline_is_a_failure_not_a_promise(tmp_path, monkeypatch, capsys):
    """⏳ '다음 주기에 다시 묻는다' 는 지금 코드가 돌고 있을 때만 참이다. `--why` 는 별도 프로세스라
    지문을 디스크로 잰다 — 배포 뒤 봇 재시작이 실패해 옛 봇이 옛 지문으로 계속 실패를 쓰면 그 약속은
    영영 안 지켜진다(독립 리뷰 · #380). 기한(이 파일이 놓인 뒤 `prior_deadline_sec`)이 지나면 ❌.
    기준은 실패 시각이 아니다 — 배포 직전 하루 상한에 걸린 실패(몇 시간 전)도 배포 직후엔 ⏳ 다(#435)."""
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술", "XLF": "금융"})
    now = _at(2026, 10, 12, 23, 0)
    rec = {"rows": [], "tried_day": "2026-10-12", "tries": 4, "error": "엑셀에 보유 종목 행이 없음",
           "tried_at": "2026-10-12T15:00:00+09:00"}      # 8시간 전 · 하루 상한
    eh._save("XLK", rec)
    eh._save("XLF", rec)
    dl = eh.prior_deadline_sec() // 60
    _code_placed(monkeypatch, now, dl - 1)
    assert eh.holdings_verdict("US", now)[0] == "retry", "실패가 8시간 전이어도 코드가 놓인 지 기한 안이면 ⏳"
    _code_placed(monkeypatch, now, dl + 1)
    kind, line = eh.holdings_verdict("US", now)
    assert kind == "fail" and ws.VERDICT_MARK[kind] == "❌", (kind, line)
    assert "보유 목록이 없는 ETF 2/2개" in line and f"놓인 지 {dl + 1}분" in line, line
    assert "봇이 옛 코드로 돌거나 보유 목록 갱신이 멈춤" in line, line
    assert "다음 주기에" not in line, "지켜지지 않는 약속을 남기지 않는다"
    assert eh.retry_note(rec, now).startswith("이전 판 코드의 기록인데 지금 코드가 놓인 지"), \
        eh.retry_note(rec, now)
    _code_placed(monkeypatch, now, None)
    kind, line = eh.holdings_verdict("US", now)
    assert kind == "retry" and line.endswith("(코드 변경 시각을 못 읽어 기한은 재지 못함)"), line
    assert eh.prior_note(rec, now).endswith("다시 묻습니다 (코드 변경 시각을 못 읽어 기한은 재지 못함)"), \
        "화면·`--why` 의 한 마디도 기한을 못 쟀다고 말한다"
    _code_placed(monkeypatch, now, dl + 1)
    eh._save("XLF", {**rec, "sig": eh.CODE_SIG, "error": "원천이 HTTP 503"})   # 지금 판 실패가 둘째 ETF
    kind, line = eh.holdings_verdict("US", now)
    assert line.startswith("보유 목록이 없는 ETF 2/2개 — XLF 외 1개: 원천이 HTTP 503"), \
        "지금 판의 실패가 머리다 — 옛 판 기록은 꼬리에(#275)"


def test_prior_overdue_reads_the_real_file_time(tmp_path, monkeypatch):
    """`_code_mtime` 을 스텁하지 않고 잰다 — 다른 테스트는 전부 스텁해서 `return time.time()`(기한이
    영영 안 지남)·`return None`(늘 '못 잼') 변형이 통과했다(델타 리뷰). 레포 파일 대신 tmp 파일(#365a)."""
    import os
    f = tmp_path / "etf_holdings.py"
    f.write_text("x = 1\n")
    monkeypatch.setattr(eh, "__file__", str(f))
    now = _at(2026, 10, 12, 22, 0)
    dl = eh.prior_deadline_sec()
    os.utime(f, (now.timestamp() - dl + 60, now.timestamp() - dl + 60))
    assert eh.prior_overdue(now) == (False, dl // 60 - 1)
    os.utime(f, (now.timestamp() - dl - 60, now.timestamp() - dl - 60))
    assert eh.prior_overdue(now) == (True, dl // 60 + 1)
    assert eh.prior_overdue(now.replace(tzinfo=None)) == (True, dl // 60 + 1), "시각대 없는 now 는 KST"
    f.unlink()
    assert eh.prior_overdue(now) == (None, None), "못 읽으면 지어내지 않는다"


def test_old_code_still_writing_after_the_deploy_window_is_stuck_at_once(tmp_path, monkeypatch):
    """`etf_holdings.py` 를 바꾸는 배포가 기한 안쪽으로 이어지면 기한 시계가 매번 다시 시작한다(#435) —
    그래서 직접 증거를 같이 본다: 옛 지문의 실패가 배포 창(`GRACE_SEC`)이 닫힌 뒤에 쓰였으면 옛 판
    프로세스가 아직 도는 것이다(델타 리뷰). 창 안에 쓰인 것은 정상 배포 과정이라 ⏳."""
    from bot.code_freshness import GRACE_SEC
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술"})
    now = _at(2026, 10, 12, 22, 0)
    _code_placed(monkeypatch, now, 30)                          # 기한(62분) 안
    placed = now - timedelta(minutes=30)
    late = placed + timedelta(seconds=GRACE_SEC + 60)
    rec = {"rows": [], "tried_day": "2026-10-12", "tries": 1, "error": "옛 사유",
           "tried_at": late.isoformat(timespec="seconds")}
    eh._save("XLK", rec)
    kind, line = eh.holdings_verdict("US", now)
    assert kind == "fail" and "지금 코드가 놓인 뒤(30분 전)에도 이전 판 코드가 실패를 썼음" in line, (kind, line)
    eh._save("XLK", {**rec, "tried_at": (placed + timedelta(seconds=GRACE_SEC - 60)).isoformat()})
    assert eh.holdings_verdict("US", now)[0] == "retry", "배포 창 안에 쓰인 옛 판 실패는 정상 과정"


def test_stuck_prior_failure_on_an_etf_with_a_list_is_a_failure(tmp_path, monkeypatch):
    """목록이 있는 ETF(한국처럼)라도 지금 코드가 다시 묻지 않는 옛 판 실패는 ❌ 다 — ⚠️ '오늘 갱신
    실패' 로 두면 봇이 멈췄다는 말이 요약에서 빠지고, 같은 출력의 ETF 줄과 두 말을 한다(델타 리뷰)."""
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"091160.KS": "반도체", "091170.KS": "은행"})
    now = _at(2026, 10, 12, 23, 0)
    _code_placed(monkeypatch, now, eh.prior_deadline_sec() // 60 + 5)
    rec = {"rows": [{"code": "005930", "name": "삼성전자", "weight": 25.0}], "fetched_day": "2026-10-11",
           "tried_day": "2026-10-12", "tries": 1, "error": "KRX 응답이 비었음",
           "tried_at": "2026-10-12T18:52:00+09:00"}
    eh._save("091160.KS", rec)
    eh._save("091170.KS", rec)
    kind, line = eh.holdings_verdict("KR", now)
    assert kind == "fail" and line.startswith("오늘 갱신에 실패한 ETF 2/2개 — 091160.KS 외 1개: KRX 응답이 비었음"), \
        (kind, line)
    assert "봇이 옛 코드로 돌거나 보유 목록 갱신이 멈춤" in line and "보유 목록이 없는 ETF" not in line, line


def test_prior_failure_on_an_etf_that_still_has_a_list_is_a_retry_not_stale(tmp_path, monkeypatch):
    """목록이 이미 있는 ETF(한국처럼)의 오늘 실패가 옛 판 기록이면 ⚠️ '오늘 갱신 실패' 가 아니라 ⏳ 다 —
    ETF 줄은 '다시 묻는다' 인데 요약이 '확인할 것' 이면 한 출력이 두 말을 한다(독립 리뷰). 지금 판의
    실패가 섞이면 그쪽(⚠️)이 머리이고 옛 판 수는 꼬리에(#275)."""
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"091160.KS": "반도체", "091170.KS": "은행"})
    now = _at(2026, 10, 12, 20, 6)
    _code_placed(monkeypatch, now, 10)
    rec = {"rows": [{"code": "005930", "name": "삼성전자", "weight": 25.0}], "fetched_day": "2026-10-11",
           "tried_day": "2026-10-12", "tries": 1, "error": "KRX 응답이 비었음",
           "tried_at": "2026-10-12T18:52:00+09:00"}
    eh._save("091160.KS", rec)
    eh._save("091170.KS", rec)
    kind, line = eh.holdings_verdict("KR", now)
    assert kind == "retry" and "ETF 2/2개의 실패가 이전 판 코드가 남긴 기록" in line, (kind, line)
    assert "그중 보유 목록이 없는 것" not in line, "목록이 있는 ETF 를 '없다' 로 세지 않는다"
    eh._save("091170.KS", {**rec, "sig": eh.CODE_SIG})
    kind, line = eh.holdings_verdict("KR", now)
    assert kind == "stale" and line.startswith("1/2개 ETF 오늘 갱신 실패"), (kind, line)
    assert line.endswith("이전 판 코드의 기록 1개는 다음 주기에 지금 코드로 다시 묻는다"), line
    eh._save("091160.KS", {**rec, "rows": []})               # 옛 판 기록이 목록 없는 ETF
    kind, line = eh.holdings_verdict("KR", now)
    assert kind == "stale" and line.endswith("다시 묻는다(그중 보유 목록이 없는 것 1개)"), \
        "⚠️ 줄도 목록이 없는 ETF 가 있다는 사실을 센다 — 머리말만 보면 전부 목록이 있는 것으로 읽힌다(#45)"


def test_prior_deadline_is_derived_from_the_bot_loop_and_the_deploy_window():
    """기한은 지어낸 수가 아니라 경로의 합이다 — 배포 창(`code_freshness.GRACE_SEC`) + 봇 첫 바퀴 대기 +
    수집 상한 + 주기 둘. 봇 쪽 두 값은 `telegram_bot` 을 import 하지 않고 소스에서 읽어 대조한다
    (봇이 주기를 바꾸면 여기서 빨간불)."""
    import ast
    from pathlib import Path
    from bot.code_freshness import GRACE_SEC
    tree = ast.parse((Path(__file__).resolve().parents[1] / "bot" / "telegram_bot.py").read_text())
    tick = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "_WORLD_SECTORS_TICK_SEC" for t in n.targets))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_periodic_world_sectors")
    first = next(c.value.args[0].value for c in ast.walk(fn) if isinstance(c, ast.Await)
                 and isinstance(c.value, ast.Call) and getattr(c.value.func, "attr", "") == "sleep"
                 and isinstance(c.value.args[0], ast.Constant))
    assert (eh._BOT_TICK_SEC, eh._BOT_FIRST_WAIT_SEC) == (tick, first)
    assert eh.prior_deadline_sec() == GRACE_SEC + first + ws.TICK_TIMEOUT_SEC + 2 * tick == 3720


def test_why_summary_treats_a_prior_code_retry_as_waiting():
    line = ws.why_summary({"ok": list(ws.MARKETS), "h_ok": ["KR"], "h_retry": ["US"]}, 0)
    assert line == "⏳ 아직 다 받지 않음 — 보유 목록 지금 코드로 다시 묻는 중(이전 판 실패 기록) US", line


@pytest.mark.parametrize("tv_name, hold, want", [
    ("Samsung Electronics Co., Ltd.", "삼성전자", "삼성전자"),
    ("삼성전자보통주", "삼성전자", "삼성전자"),            # VM 실측: 원천은 법인 정식명 — KRX 약칭이 먼저
    ("에스케이하이닉스보통주", None, "에스케이하이닉스"),   # 보유 목록 밖 종목 — 꼬리 '보통주' 만 뗀다
    ("보통주", None, "보통주"),
    ("오리온", "ORION", "오리온"),
    ("NVIDIA Corporation", "NVIDIA CORP", "NVIDIA Corporation"),
    (None, "애플", "애플"),
    ("", "", ""),
])
def test_display_name_prefers_hangul(tv_name, hold, want):
    assert wp.display_name(tv_name, hold) == want


def test_holding_rows_use_the_korean_name_from_the_etf_list(monkeypatch):
    monkeypatch.setattr(ws, "stock_week_row", lambda it, mval: {
        "week": 1.0, "upshare": 50.0, "share": 0.1, "dcap": 1.0})
    st = {"stocks": {"005930": {"name": "Samsung Electronics", "sym": "KRX:005930",
                                "industry": "Semiconductors", "chg": [1.0] * 5, "cap": 1e14}},
          "mval": [1.0] * 5, "labels": {"ind": {"Semiconductors": "세미컨덕터"}}}
    rows = wp._holding_rows({"rows": [{"code": "005930", "name": "삼성전자", "weight": 20}]}, st, "KR")
    assert rows[0]["nm"] == "삼성전자" and rows[0]["ind"] == "세미컨덕터"
    assert rows[0]["href"] == "lookup/005930"


@pytest.mark.parametrize("m,code,want", [
    ("KR", "005930", "lookup/005930"),
    ("US", "AAPL", "lookup/AAPL"),
    ("US", "BRK.B", "lookup/BRK-B"),          # 미국 클래스 주식 — 분석 화면은 yfinance 꼴
    ("US", "brk.b", "lookup/BRK-B"),
    ("KR", "AAPL", ""),                        # 그 나라 꼴이 아니면 링크를 걸지 않는다(#400)
    ("KR", "00593", ""),
    ("US", "005930", ""),                      # 맨 숫자는 국내 리졸버로 풀려 남의 회사 화면을 연다
    ("US", "CASH_USD", ""),
    ("US", "", ""),
    ("JP", "7203", ""),                        # 보유 목록 거래소를 모르는 나라
])
def test_holding_href_maps_only_the_markets_code_shape(m, code, want):
    assert wp.holding_href(m, code) == want


def test_holding_rows_missing_from_the_week_still_link(store):
    """그 주 하루치에 없는 보유종목(사용자 2026-10-13 "모든 종목들도 클릭하면") — 숫자는 비어도
    이름은 종목분석으로 간다. 옛 판은 `href: ""` 라 평문이었다."""
    fx.simulate(store, _at(2026, 10, 6, 0), 6)
    now = _at(2026, 10, 12, 9, 0)
    store.now = now
    eh._save("XLK", {"rows": [{"code": "ZQXW", "name": "Not In Snapshot", "weight": 5.0},
                              {"code": "CASH_USD", "name": "Cash", "weight": 1.0}],
                     "fetched_day": "2026-10-12"})
    t = next(x for x in list(eh.etfs("KR")))
    eh._save(t, {"rows": [{"code": "999998", "name": "없는전자", "weight": 5.0}], "fetched_day": "2026-10-12"})
    sp = wp.stocks_payloads(now)
    rows = {r["c"]: r for e in sp["US"]["etfs"] + sp["KR"]["etfs"] for tb in e["tables"] for r in tb["rows"]}
    assert rows["ZQXW"]["cap"] is None, "하루치에 없는 행이어야 이 갈래를 잰다"
    assert rows["ZQXW"]["href"] == "lookup/ZQXW"
    assert rows["999998"]["href"] == "lookup/999998"
    assert rows["CASH_USD"]["href"] == ""
    h = wp.render_stocks(sp, ws.load_state(), now)
    assert '"href": "lookup/ZQXW"' in h or '"href":"lookup/ZQXW"' in h, "화면 JSON 에 링크가 실린다"


def test_probe_reports_how_many_names_are_korean(store, capsys):
    store.now = _at(2026, 10, 12, 18, 40)
    ws.probe("KR")
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if "종목 이름" in x)
    assert f"한글 {fx.N_STOCKS}/{fx.N_STOCKS}" in line, line


def test_kr_holdings_without_login_names_the_branch(monkeypatch):
    """'미설정' 만 적으면 이미 있는 키를 넣으러 간다(#82·#294) — `env_diag` 갈래를 같이."""
    import bot.pykrx_client as pk
    import bot.env_keys as ek
    monkeypatch.setattr(pk, "krx_login_ready", lambda: False)
    monkeypatch.setattr(ek, "env_diag", lambda *n: "KRX_ID: 키 없음")
    with pytest.raises(eh.HoldingsError) as ei:
        eh.fetch_kr("069500.KS", "2026-10-12")
    assert "미설정" in ei.value.reason and "KRX_ID: 키 없음" in ei.value.reason


@pytest.mark.parametrize("tick_res, hold_res", [
    ({"stored": ["KR:2026-10-13"], "failed": [], "status": "ok"}, {}),
    ({"stored": [], "failed": ["JP"], "status": "fail"}, {}),
    ({"stored": [], "failed": [], "status": "idle"}, {"US": {"ok": [], "failed": ["XLK"]}}),
    ({"stored": [], "failed": [], "status": "idle"}, {"KR": {"error": "RuntimeError: x"}}),
])
def test_new_data_or_holdings_change_forces_a_redraw(store, monkeypatch, tick_res, hold_res):
    calls = []
    monkeypatch.setattr(wp, "regenerate", lambda now=None: calls.append(now) or [])
    monkeypatch.setattr(ws, "market_verdict", lambda m, st, now=None: ("ok", "same"))
    idle = {"stored": [], "failed": [], "status": "idle"}
    seq = iter([idle, idle, tick_res])
    holds = iter([{}, {}, hold_res])
    monkeypatch.setattr(ws, "tick", lambda now=None: dict(next(seq)))
    monkeypatch.setattr(ws, "_refresh_holdings", lambda now: next(holds))
    t0 = _at(2026, 10, 13, 9, 30)
    ws.tick_and_render(t0)
    assert ws.tick_and_render(t0 + timedelta(minutes=15))["regenerated"] is False
    assert ws.tick_and_render(t0 + timedelta(minutes=30))["regenerated"] is True
    assert len(calls) == 2


# ── 독립 리뷰 반영(2026-10-12) — High 4 · Medium · Low · 살아남은 뮤테이션 ──────────────
def _cols():
    return [*tv.CORE, *tv.OPTIONAL]


def test_scan_continues_after_a_short_page_when_total_says_more(monkeypatch):
    """원천이 쪽을 줄여 줘도(요청 2000 · 응답 1000) '마지막 쪽' 으로 읽지 않고 받은 만큼 뒤에서
    이어 묻는다 — 건너뛰면 1000~2000 이 빠진다."""
    monkeypatch.setattr(tv, "PAGE", 2000)
    c = _cols()
    s = _Sess([_Resp(200, _page(c, 1000, 0, 3000)), _Resp(200, _page(c, 1000, 1000, 3000)),
               _Resp(200, _page(c, 1000, 2000, 3000))])
    got = tv.scan("KR", session=s, sleep=lambda x: None)
    assert len(got["rows"]) == 3000 and got["pages"] == 3
    assert [b["range"][0] for b in s.bodies] == [0, 1000, 2000]


def test_scan_partial_answer_fails_instead_of_baking(monkeypatch):
    monkeypatch.setattr(tv, "PAGE", 2000)
    c = _cols()
    s = _Sess([_Resp(200, _page(c, 1000, 0, 6000)), _Resp(200, {"totalCount": 6000, "data": []})])
    with pytest.raises(tv.ScanError) as ei:
        tv.scan("US", session=s, sleep=lambda x: None)
    assert ei.value.kind == "partial" and "6,000 중 1,000행" in ei.value.reason


def test_scan_stops_exactly_at_total(monkeypatch):
    """두 쪽이 꽉 차도 totalCount 를 다 받았으면 세 번째를 묻지 않는다."""
    monkeypatch.setattr(tv, "PAGE", 3)
    c = _cols()
    s = _Sess([_Resp(200, _page(c, 3, 0, 6)), _Resp(200, _page(c, 3, 3, 6))])
    got = tv.scan("KR", session=s, sleep=lambda x: None)
    assert len(s.bodies) == 2 and len(got["rows"]) == 6


def test_scan_counts_dropped_and_duplicate_rows(monkeypatch):
    monkeypatch.setattr(tv, "PAGE", 3)
    c = _cols()
    p1 = _page(c, 3, 0, 100)
    p2 = _page(c, 3, 2, 100)                     # 2 가 쪽 경계에서 또 온다(시총 동률)
    p2["data"].append({"s": "KRX:BAD", "d": ["x"]})
    p2["totalCount"] = 100
    s = _Sess([_Resp(200, {"totalCount": 7, "data": p1["data"]}),
               _Resp(200, {"totalCount": 7, "data": p2["data"]}),
               _Resp(200, {"totalCount": 7, "data": []})])
    with pytest.raises(tv.ScanError) as ei:
        tv.scan("KR", session=s, sleep=lambda x: None)
    assert "버린 행 1" in ei.value.reason and "겹친 행 1" in ei.value.reason
    s = _Sess([_Resp(200, {"totalCount": 4, "data": p1["data"]}),
               _Resp(200, {"totalCount": 4, "data": p2["data"][:2]})])
    got = tv.scan("KR", session=s, sleep=lambda x: None)
    assert [r["symbol"] for r in got["rows"]] == ["KRX:000000", "KRX:000001", "KRX:000002",
                                                    "KRX:000003"]
    assert got["dup_rows"] == 1


def test_exception_while_storing_takes_the_failure_path_with_backoff(store, monkeypatch):
    """예외만 따로 적으면 날짜가 안 바뀌어 '⏳ 대기' 로 남고 15분마다 전 종목을 다시 받는다."""
    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(ws, "save_snapshot", full)
    t = _at(2026, 10, 12, 18, 40)
    for mins in (0, 15, 30):
        store.now = t + timedelta(minutes=mins)
        ws.tick(store.now)
    assert store.calls.count("KR") == 2, store.calls       # 0분 · 30분(백오프 30분)
    ms = ws.load_state()["markets"]["KR"]
    assert ms["kind"] == "error" and ms["date"] == "2026-10-12" and ms["n"] == 2
    kind, line = ws.market_verdict("KR", ws.load_state(), t + timedelta(minutes=30))
    assert kind == "fail" and "OSError" in line


def test_exception_before_the_source_call_still_backs_off(store, monkeypatch):
    real = ws.window_end
    calls = []

    def boom(m, d):
        calls.append(m)
        if m == "KR":
            raise RuntimeError("달력 고장")
        return real(m, d)
    monkeypatch.setattr(ws, "window_end", boom)
    t = _at(2026, 10, 12, 18, 40)
    ws.tick(t)
    ms = ws.load_state()["markets"]["KR"]
    assert ms["kind"] == "error" and ms["date"] == "2026-10-12" and ms["n"] == 1
    monkeypatch.setattr(ws, "window_end", real)          # 판정 줄도 같은 달력을 쓴다
    assert ws.market_verdict("KR", ws.load_state(), t)[0] == "fail"


def test_stale_for_the_whole_window_reads_as_a_holiday(store, monkeypatch):
    """달력에 없는 휴장(대만 태풍 휴장)을 '수집 못 함' 이라 적으면 거짓 단정이다(#165)."""
    store.now = _at(2026, 10, 8, 18, 0)
    ws.tick(store.now)
    assert ws.agg_path("TW", "2026-10-08").exists()
    same = fx.fake_rows("TW", "2026-10-08")
    real = tv.scan
    monkeypatch.setattr(tv, "scan", lambda m, **k: ({"rows": same, "total": fx.N_STOCKS,
                                                    "dropped": [], "pages": 1}
                                                   if m == "TW" else real(m, **k)))
    t = _at(2026, 10, 12, 17, 40)
    while t <= _at(2026, 10, 13, 8, 40):          # 창 끝 08:30 KST 를 넘긴 주기까지
        store.now = t
        ws.tick(t)
        t += timedelta(minutes=15)
    st = ws.load_state()
    kind, line = ws.market_verdict("TW", st, t)
    assert kind == "holiday" and ws.VERDICT_MARK[kind] == "⚠️" and "휴장 추정" in line, line
    assert ws.market_day_status("TW", "2026-10-12", t) == "holiday"
    rec = ws.mix_record("2026-10-12", t)
    assert rec["markets"]["TW"]["day"] == "holiday"
    cell = wp._mix_cell("TW", None, "day", rec)
    assert ">휴장 추정<" in cell


def test_a_window_with_no_attempt_after_collecting_is_our_failure(store):
    store.now = _at(2026, 10, 8, 18, 40)
    ws.tick(store.now)                       # KR 10-08 받음
    t = _at(2026, 10, 13, 8, 0)              # 10-12 창(18:30 ~ 10-13 07:30) 내내 봇이 없었다
    store.now = t
    ws.tick(t)
    kind, line = ws.market_verdict("KR", ws.load_state(), t)
    assert kind == "missed_fail" and "한 번도 묻지 않았다" in line, line
    assert ws.market_day_status("KR", "2026-10-12", t) == "missing"


def test_missing_before_the_first_collection_is_not_an_alarm(store):
    t = _at(2026, 10, 13, 8, 0)              # 배포 첫 주기 — KR 10-12 창은 이미 지났다
    store.now = t
    ws.tick(t)
    kind, line = ws.market_verdict("KR", ws.load_state(), t)
    assert kind == "missed" and ws.VERDICT_MARK[kind] == "⚠️" and "수집을 시작하기 전" in line


def test_why_counts_a_missed_failure_as_bad(store, capsys):
    store.fail["KR"] = "원천이 HTTP 403"
    t = _at(2026, 10, 12, 18, 40)
    while t < _at(2026, 10, 13, 8, 0):
        store.now = t
        ws.tick(t)
        t += timedelta(minutes=15)
    assert ws.why(t) == 1
    assert "❌" in capsys.readouterr().out


def test_window_closes_before_pre_market_trading():
    assert ws.window_end("KR", "2026-10-12") == datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)
    assert ws.window_end("US", "2026-10-12") == datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc), \
        "미국은 프리마켓 04:00 ET(= 08:00 UTC 서머타임) 전에 닫는다"


def test_fallback_hours_follow_daylight_saving():
    summer = ws._fallback_times("US", datetime(2027, 7, 6).date())
    winter = ws._fallback_times("US", datetime(2027, 1, 6).date())
    assert summer[0].hour == 13 and summer[0].minute == 30 and summer[1].hour == 20
    assert winter[0].hour == 14 and winter[1].hour == 21


def test_one_etf_exception_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술", "XLB": "소재"})
    calls = []

    def fetch(t, session=None):
        calls.append(t)
        if t == "XLK":
            raise KeyError("xl/workbook.xml")      # 'PK 로 시작하지만 xlsx 가 아닌 zip'
        return [{"code": "LIN", "name": "Linde", "weight": 15.0}], "x"
    monkeypatch.setattr(eh, "fetch_us", fetch)
    t0 = _at(2026, 10, 12, 8, 0)
    res = eh.refresh("US", t0)
    assert res == {"ok": ["XLB"], "failed": ["XLK"]}
    h = eh.load("XLK")
    assert "KeyError" in h["error"] and h["tries"] == 1
    eh.refresh("US", t0 + timedelta(minutes=15))
    assert calls == ["XLK", "XLB"], "백오프 없이 15분마다 다시 받았다"


def test_holdings_drop_non_finite_weights():
    rows = [[f"Co{i}", f"T{i}", "x", "y", 20 - i, "Information Technology", 1, "USD"] for i in range(5)]
    rows.insert(1, ["Bad", "NAN1", "x", "y", "NaN", "Information Technology", 1, "USD"])   # 글자 'NaN' — float() 가 받는다
    top, _asof = eh.parse_ssga_xlsx(_ssga_xlsx(rows))
    assert "NAN1" not in [t["code"] for t in top] and len(top) == 5
    json.dumps(top, allow_nan=False)


def test_holdings_verdict_branches(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"A": "a", "B": "b"})
    now = _at(2026, 10, 12, 10, 0)
    assert eh.holdings_verdict("KR", now)[0] == "none"
    eh._save("A", {"rows": [{"code": "1"}], "fetched_day": "2026-10-12", "tried_day": "2026-10-12"})
    assert eh.holdings_verdict("KR", now)[0] == "ok"
    eh._save("B", {"rows": [], "error": "KRX_ID/KRX_PW 미설정", "tried_day": "2026-10-12",
                   "sig": eh.CODE_SIG})
    kind, line = eh.holdings_verdict("KR", now)
    assert kind == "fail" and "KRX_ID" in line and "1/2" in line
    eh._save("B", {"rows": [{"code": "2"}], "fetched_day": "2026-10-11", "tried_day": "2026-10-12",
                   "error": "HTTP 503", "sig": eh.CODE_SIG})     # 지금 판의 실패(옛 판이면 ⏳ — 아래 별도 테스트)
    assert eh.holdings_verdict("KR", now)[0] == "stale"


def test_validate_checks_each_exchange_and_skips_untraded_pairs():
    def row(ex, i, chg, val):
        return [f"{ex}:{i}", "n", "S", "I", chg, val, 100.0, None, 1, 0]
    prev = [row("TWSE", i, 1.0 + i, 10.0) for i in range(250)] + \
           [row("TPEX", i, 2.0 + i, 20.0) for i in range(100)]
    cur = [row("TWSE", i, 9.0 + i, 11.0) for i in range(250)] + \
          [row("TPEX", i, 2.0 + i, 20.0) for i in range(100)]          # TPEX 만 어제 값
    ok, why, kind = ws.validate(cur, {"date": "d0", "rows": prev})
    assert kind == "stale" and "TPEX" in why, why
    quiet_prev = [row("NAG", i, 0.0, 0.0) for i in range(60)] + [row("TSE", i, 1.0, 5.0 + i) for i in range(300)]
    quiet_cur = [row("NAG", i, 0.0, 0.0) for i in range(60)] + [row("TSE", i, 2.0, 6.0 + i) for i in range(300)]
    assert ws.validate(quiet_cur, {"date": "d0", "rows": quiet_prev}) == (True, "", ""), \
        "이틀 다 거래가 없던 종목(0,0)은 '아직 안 실음' 의 증거가 아니다"


@pytest.mark.parametrize("same_n, want", [(368, "stale"), (352, "")])
def test_validate_stale_threshold_is_ninety_percent(same_n, want):
    prev = [[f"A:{i}", "n", "S", "I", 1.0 + i, 10.0, 100.0, None, 1, 0] for i in range(400)]
    cur = [r[:4] + ([r[4]] if i < same_n else [r[4] + 0.5]) + r[5:] for i, r in enumerate(prev)]
    assert ws.validate(cur, {"date": "d0", "rows": prev})[2] == want    # 92% · 88%


def test_validate_rejects_a_zero_reset():
    rows = [[f"A:{i}", "n", "S", "I", 0.0 if i < 250 else 1.0, 0.0 if i < 250 else 5.0, 100.0,
             None, 1, 0] for i in range(400)]
    assert ws.validate(rows, None)[2] == "reset"


def test_size_check_uses_the_recent_median_and_can_accept_a_new_size():
    rows = [[f"A:{i}", "n", "S", "I", 1.0 + i, 10.0, 100.0, None, 1, 0] for i in range(400)]
    assert ws.validate(rows, None, base_n=600)[2] == "small"
    assert ws.validate(rows, None, base_n=500)[0], "500 의 70% = 350 < 400"
    assert ws.validate(rows, None, base_n=600, accept_size=True)[0]
    ms = {"small_seen": {"2026-10-06": 401, "2026-10-07": 399}}
    assert ws._accepted_size("KR", ms, "2026-10-08", rows)
    assert not ws._accepted_size("KR", {"small_seen": {"2026-10-06": 401}}, "2026-10-08", rows)
    assert not ws._accepted_size("KR", {"small_seen": {"2026-10-08": 400, "2026-10-07": 401}},
                                 "2026-10-08", rows), "같은 세션 재시도는 세지 않는다(다른 세션 하나뿐)"


def test_size_check_baseline_is_the_median_of_recent_sessions(store):
    fx.simulate(store, _at(2026, 10, 5, 0), 4)
    have = ws.stored_dates("KR")
    assert ws._base_rows("KR", "2026-10-30") == fx.N_STOCKS and len(have) >= 3


def test_week_cell_tie_is_not_a_win():
    agg = {"mkt": {"w": 1.0, "wr": 1.0}, "ind": {"X": {"w": 1.0, "wr": 1.0}}}
    assert ws.week_cell([("d", agg, None)], "ind", "X")["wins"] == 0


def test_mix_record_finalizes_when_a_market_missed_its_window(store, monkeypatch):
    fx.simulate(store, _at(2026, 10, 5, 0), 8)
    monkeypatch.setattr(ws, "market_day_status",
                        lambda m, d, now, state=None: "missing" if m == "TW" else "ok")
    assert ws.mix_record("2026-10-12", _at(2026, 10, 13, 9, 0))["final"]


def test_two_stock_industries_are_absent_from_mix_and_map(pages):
    rec = pages["recs"][0]
    assert not any(r["key"] == fx.TWO_STOCK_IND for r in rec["rows"]), \
        "다섯 나라 모두 2종목뿐인 업종은 '있다' 가 아니다(MIN_N)"
    wk = pages["map_payload"]["KR"]["weeks"][0]
    assert fx.TWO_STOCK_IND not in {r["key"] for r in wk["groups"]["ind"]}


def test_finalize_runs_when_a_session_is_stored(store):
    fx.simulate(store, _at(2026, 10, 5, 0), 8)
    t = _at(2026, 10, 13, 9, 30)
    store.now = t
    ws.tick(t)
    assert (ws._REC_DIR / "2026-10-12.json").exists(), "받은 주기에 섹터혼합 기록을 굳히지 않았다"


def test_frozen_mix_record_is_recomputed_when_the_rules_change(store):
    fx.simulate(store, _at(2026, 10, 5, 0), 9)
    now = _at(2026, 10, 13, 9, 30)
    ws.finalize_records(now)
    path = ws._REC_DIR / "2026-10-12.json"
    rec = json.loads(path.read_text())
    assert rec["ver"] == ws.calc_sig()
    rec["ver"], rec["rows"] = "old", []
    path.write_text(json.dumps(rec))
    assert ws.load_mix_record("2026-10-12") is None, "규칙이 바뀌었는데 굳힌 기록이 수정을 가린다"
    assert ws.mix_records(now)[0]["rows"], "다시 계산한 값을 그려야 한다"
    ws.finalize_records(now)
    assert json.loads(path.read_text())["ver"] == ws.calc_sig()
    for m in ws.MARKETS:                      # 업종 합계가 보관 기간을 넘겨 지워진 날 — 옛 기록뿐
        ws.agg_path(m, "2026-10-12").unlink(missing_ok=True)
    rec["ver"] = "old"
    path.write_text(json.dumps(rec))
    assert ws.load_mix_record("2026-10-12")["ver"] == "old"


def test_week_stocks_keeps_each_days_cap_and_the_last_as_cap(store):
    fx.simulate(store, _at(2026, 10, 5, 0), 9)
    st = ws.week_stocks("KR", ws.week_monday("2026-10-06"))
    it = st["stocks"]["000005"]
    ok = [c for c in it["caps"] if c]
    assert len(set(ok)) > 1, "픽스처 시총이 날마다 같으면 첫날·마지막 날을 못 가른다"
    assert it["cap"] == ok[-1]


def test_size_bands_rank_by_the_first_days_cap():
    stocks = {}
    for i in range(40):
        first, last = 100.0 - i, float(i + 1)        # 첫날 큰 순 ≠ 마지막 날 큰 순
        chg = 1.0 if i < 30 else -1.0
        stocks[str(i)] = {"chg": [chg, chg, None, None, None], "val": [1.0] * 2 + [None] * 3,
                          "caps": [first, last, None, None, None], "cap": last, "fx": 0}
    st = {"ok": [True, True, False, False, False], "days": ["a", "b", "c", "d", "e"], "stocks": stocks}
    sz = wp.size_rows("KR", None, st)
    top = sz["rows"][0]
    assert top["label"] == "초대형 30" and top["days"][:2] == [1.0, 1.0], top


def test_concentration_is_the_top_ten_share():
    vals = [float(v) for v in range(1, 13)]                # 1..12
    stocks = {str(i): {"chg": [1.0, None, None, None, None], "val": [v, None, None, None, None],
                       "caps": [10.0, None, None, None, None], "cap": 10.0, "fx": 0}
              for i, v in enumerate(vals)}
    st = {"ok": [True, False, False, False, False], "days": list("abcde"), "stocks": stocks}
    sz = wp.size_rows("KR", None, st)
    assert sz["conc"][0] == pytest.approx(sum(sorted(vals)[-10:]) / sum(vals) * 100)


def test_week_payload_sums_count_only_rising_days(monkeypatch):
    days = [f"2026-10-{d:02d}" for d in range(12, 17)]
    monkeypatch.setattr(ws, "map_week", lambda m, mon, now: {
        "cols": [{"date": d, "status": "ok"} for d in days], "basis": []})
    monkeypatch.setattr(ws, "load_agg", lambda m, d: None)
    st = {"days": days, "ok": [True] * 5, "mval": [100.0] * 5,
          "labels": {"sec": {}, "ind": {}},
          "stocks": {"1": {"sym": "KRX:000001", "name": "가", "sector": "S", "industry": "I",
                           "cap": 10.0, "caps": [10.0] * 5, "fx": 0,
                           "chg": [1.0, 0.0, -1.0, None, 2.0], "val": [10.0, 20.0, 30.0, None, 40.0]}}}
    monkeypatch.setattr(ws, "week_stocks", lambda m, mon: st)
    wk, js = wp._week_payload("KR", ws.week_monday("2026-10-12"), _at(2026, 10, 12, 20))
    s = wk["sums"][("sec", "S")]
    assert s["val"] == 100.0 and s["up"] == 50.0, "보합(0.0) 날은 '오른 날' 이 아니다"
    assert s["upshare"] == pytest.approx(50.0) and s["share"] == pytest.approx(100 / 500 * 100)
    assert js["rows"][0][10] == "lookup/000001"


def test_mix_table_hides_quiet_rows_behind_a_button():
    rec = {"markets": {}, "rows": [
        {"key": "a", "label": "A", "cells": {}, "v_week": "none", "l_week": "뚜렷하지 않음",
         "k_week": [3, 0, 0]},
        {"key": "b", "label": "B", "cells": {}, "v_week": "k_up", "l_week": "2국 강세",
         "k_week": [1, -2, -1]}]}
    h = wp._mix_table(rec, "week", "t")
    row_a = re.search(r"<tr[^>]*>(?:(?!</tr>).)*<b>A</b>", h).group(0)
    assert "display:none" in row_a and "js-ws-q" in row_a
    row_b = re.search(r"<tr[^>]*>(?:(?!</tr>).)*<b>B</b>", h).group(0)
    assert "display:none" not in row_b
    assert "신호 약한 업종 1개 더 보기" in h


@pytest.mark.parametrize("st, want", [
    ({"day": "ok", "sessions": 5, "got": 4}, "4/5일 받음"),
    ({"day": "ok", "sessions": 5, "got": 0}, None),
])
def test_mix_week_cell_says_how_many_days_were_received(st, want):
    rec = {"markets": {"KR": st}}
    if want:
        h = wp._mix_cell("KR", {"week": 1.5, "wins": 3, "N": 4, "n": 9}, "week", rec)
        assert "이긴 날 3/4 · 4/5일 받음" in h
    else:
        assert ">받은 날 없음<" in wp._mix_cell("KR", None, "week", rec)
    assert ">휴장<" in wp._mix_cell("KR", None, "week", {"markets": {"KR": {"sessions": 0}}})
    full = wp._mix_cell("KR", {"week": 1.5, "wins": 3, "N": 5, "n": 9}, "week",
                        {"markets": {"KR": {"sessions": 5, "got": 5}}})
    assert "일 받음" not in full


def test_map_pending_column_shows_a_dot_not_a_dash():
    wk = {"monday": "2026-10-12", "cols": [{"date": "2026-10-12", "status": "ok"},
                                           {"date": "2026-10-13", "status": "pending"},
                                           {"date": "2026-10-14", "status": "holiday"}],
          "groups": {"sec": [{"key": "S", "label": "섹터", "n": 9, "cells": {},
                              "week": {"rel": None}}]}}
    h = wp._map_table("KR", 0, wk, "sec", {}, "ws_KR.json")
    tds = re.findall(r"<td class='ws-off'>([^<]*)</td>", h)
    assert tds[1:3] == ["·", "휴장 추정"], tds          # [0] = 받은 날인데 그 칸 값 없음 → '—'


@pytest.mark.parametrize("now, want", [
    (_at(2026, 10, 12, 10, 0), " — 지난주"),          # 월요일 아침 — 마지막 저장은 지난주
    (_at(2026, 10, 7, 20, 0), " — 이번 주"),
    (_at(2026, 10, 20, 9, 0), ""),
])
def test_week_tag_follows_today_not_the_last_stored_day(now, want):
    assert wp.week_tag(ws.week_monday("2026-10-05"), now) == want


def test_map_title_does_not_call_last_week_this_week(store):
    fx.simulate(store, _at(2026, 9, 28, 0), 12)
    now = _at(2026, 10, 12, 10, 0)
    pages_, _js = wp.map_payloads(now)
    h = wp.render_map(pages_, ws.load_state(), now)
    wrong = "10/5(월) 주 — 이번 주" in h
    assert "10/5(월) 주 — 지난주" in h and not wrong


@pytest.mark.parametrize("sym", ["NAG:7203", "FSE:9999", "SAPSE:1234", "XYZ:ABC"])
def test_unknown_exchange_prefix_gets_no_link(sym):
    assert wp.lookup_ticker(sym) == "" and wp.lookup_href(sym) == ""


def test_stocks_page_json_tags_parse_and_keep_dangerous_names(pages):
    h = pages["stocks"]
    tags = re.findall(r"<script type='application/json' id='(ws-etf-[^']+)'>(.*?)</script>", h, re.S)
    assert tags
    names = []
    for _id, body in tags:
        names += [r["nm"] for r in json.loads(body)["rows"]]   # `</script>` 가 태그를 닫으면 깨진다
    assert any("</script>" in n for n in names), "픽스처에 위험한 이름이 실려야 가드를 잰다"
    outside = re.sub(r"<script.*?</script>", "", h, flags=re.S)
    leaked = "<회사>" in outside
    assert not leaked, "이름이 스크립트 태그 밖으로 샜다(`</script>` 가 태그를 닫았다)"


def test_stocks_tables_mark_days_that_are_not_received(pages):
    kr = pages["stocks_payload"]["KR"]["etfs"][0]["tables"][0]
    assert "ok" in kr["status"] and set(kr["status"]) - {"ok"}, kr["status"]


def test_map_json_uses_the_same_korean_name_as_the_etf_table(store, monkeypatch):
    real = fx.fake_rows

    def rows(market, session, n=fx.N_STOCKS):
        out = real(market, session, n)
        out[4]["description"] = "Ganada Electronics"     # 원천 이름에 한글이 없는 종목
        return out
    monkeypatch.setattr(fx, "fake_rows", rows)
    fx.simulate(store, _at(2026, 10, 5, 0), 9)
    now = _at(2026, 10, 13, 9, 30)
    store.now = now
    eh.fetch_kr = lambda t, d: ([{"code": "000004", "name": "가나다전자", "weight": 9.0}], d)
    eh.fetch_us = lambda t, session=None: ([{"code": "T4", "name": "X CORP", "weight": 9.0}], "x")
    ws._refresh_holdings(now)
    _pages, jsons = wp.map_payloads(now)
    row = next(r for r in jsons["KR"]["weeks"][0]["rows"] if r[0] == "000004")
    assert row[1] == "가나다전자"
    us = next(r for r in jsons["US"]["weeks"][0]["rows"] if r[0] == "T4")
    assert us[1] == "Ganada Electronics", "보유 목록에도 한글 이름이 없으면 원천 이름 그대로"
    sp = wp.stocks_payloads(now)
    kr = next(r for t in sp["KR"]["etfs"] for tb in t["tables"] for r in tb["rows"] if r["c"] == "000004")
    assert kr["nm"] == row[1], "두 화면이 같은 종목을 다른 이름으로 부른다(#38)"


def test_map_meta_names_the_currency_mismatch_count(pages):
    meta = pages["map_payload"]["KR"]["weeks"][0]["meta"]
    n = sum(1 for i in range(fx.N_STOCKS) if i % fx.FX_EVERY == 7)
    assert f"시총 통화가 달라 등락 가중에서 뺀 종목 {n}" in meta, meta


def test_regenerate_reads_each_week_once(tmp_path, monkeypatch):
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "archive")
    with fx.patched(tmp_path) as fake:
        fx.simulate(fake, _at(2026, 10, 6, 0), 6)
        real = ws.week_stocks
        seen = []
        monkeypatch.setattr(ws, "week_stocks", lambda m, mon: seen.append((m, mon)) or real(m, mon))
        wp.regenerate(_at(2026, 10, 12, 9, 0))
    assert seen and len(seen) == len(set(seen)), "심리지도와 섹터종목이 같은 주를 두 번 읽었다"


def test_stocks_page_says_why_a_table_is_missing(store):
    eh._save("XLK", {"rows": [{"code": "T1", "name": "X", "weight": 5.0}], "fetched_day": "2026-10-12"})
    sp = wp.stocks_payloads(_at(2026, 10, 12, 9, 0))
    e = next(x for x in sp["US"]["etfs"] if x["ticker"] == "XLK")
    assert e["nodays"] == "보유 목록은 있지만 아직 받은 하루치가 없습니다"
    h = wp.render_stocks(sp, ws.load_state(), _at(2026, 10, 12, 9, 0))
    old_text = "해당 종목 없음 · 아직" in h
    assert "보유 목록은 있지만 아직 받은 하루치가 없습니다" in h and not old_text


def test_chart_board_nav_lists_the_three_boards():
    from bot.fred_boards import _NAV
    for page in ("sector_mix.html", "sentiment_map.html", "sector_stocks.html"):
        assert f'href="{page}"' in _NAV


@pytest.mark.skipif(not _NODE, reason="node 없음")
def test_cell_click_finds_the_week_by_monday_not_by_position():
    harness = r"""
    function El(attrs){this.attrs=attrs||{};this.innerHTML='';this.textContent='';this.value='';
      this.style={};this.kids={};this.handlers={};}
    El.prototype.getAttribute=function(k){return k in this.attrs?this.attrs[k]:null;};
    El.prototype.querySelector=function(s){return this.kids[s]||(this.kids[s]=new El());};
    El.prototype.querySelectorAll=function(){return [];};
    El.prototype.addEventListener=function(t,f){this.handlers[t]=f;};
    El.prototype.scrollIntoView=function(){};
    var box=new El({'data-gone':'GONE-MESSAGE'});
    var td=new El({'data-m':'KR','data-mon':'2026-10-12','data-kind':'sec','data-key':'S',
      'data-day':'','data-src':'ws_KR.json','data-head':'h','data-title':'t','data-sum':'s'});
    global.document={querySelectorAll:function(s){return s==='td.js-ws-cell'?[td]:[];},
      getElementById:function(id){return id==='ws-detail-KR'?box:null;}};
    global.window={};
    var payload=null;
    global.fetch=function(){return Promise.resolve({ok:true,json:function(){return payload;}});};
    """ + wp._WS_JS + r"""
    var wk={monday:'2026-10-12',days:['a'],dayLabels:['월'],status:['ok'],secs:['S'],inds:['I'],
      indLabels:['업'],rows:[['1','가',0,0,10,[1.0],1.0,100,0.1,1,'']]};
    payload={cur:'원',weeks:[{monday:'2026-10-05',days:[],dayLabels:[],status:[],secs:['S'],inds:[],
      indLabels:[],rows:[['9','다른 주',0,0,10,[1.0],1.0,100,0.1,1,'']]}]};
    td.handlers.click();
    setTimeout(function(){
      var miss=box.kids['.js-ws-body'].innerHTML;
      payload={cur:'원',weeks:[payload.weeks[0],wk]};
      // 캐시를 피하려 다른 나라 칸처럼 — 같은 칸을 새 응답으로 다시
      td.attrs['data-m']='KR';
      console.log(JSON.stringify({miss:miss}));
    },20);
    """
    out = _js_eval(harness)
    js = json.loads(out.strip().splitlines()[-1])
    assert "GONE-MESSAGE" in js["miss"] and "다른 주" not in js["miss"], js


def test_base_rows_is_the_median_not_the_last(store, monkeypatch):
    days = {"2026-10-05": 100, "2026-10-06": 900, "2026-10-07": 300}
    monkeypatch.setattr(ws, "stored_dates", lambda m: sorted(days))
    monkeypatch.setattr(ws, "load_agg", lambda m, d: {"rows": days[d], "mkt": {}})
    assert ws._base_rows("KR", "2026-10-08") == 300
    days["2026-10-07"] = 1000
    assert ws._base_rows("KR", "2026-10-08") == 900, "마지막 날(1000)이 아니라 중앙값"


def test_refresh_drops_non_finite_weights_even_if_the_fetcher_lets_them_through(tmp_path, monkeypatch):
    monkeypatch.setattr(eh, "_DIR", tmp_path / "h")
    monkeypatch.setattr(eh, "etfs", lambda m: {"XLK": "기술"})
    monkeypatch.setattr(eh, "fetch_us", lambda t, session=None: (
        [{"code": "A", "name": "a", "weight": float("nan")},
         {"code": "B", "name": "b", "weight": 5.0}], "x"))
    eh.refresh("US", _at(2026, 10, 12, 8, 0))
    assert [r["code"] for r in eh.load("XLK")["rows"]] == ["B"]
    json.dumps(eh.load("XLK"), allow_nan=False)


def test_twenty_session_baseline_reaches_the_map_once_history_is_long_enough(store):
    """자체 이력이 직전 20세션 중 15개를 넘으면 상대 거래대금은 근사가 아니라 그 이력 평균 —
    배선을 끝까지 태운다(`map_week` 가 기준을 버리면 화면이 영원히 점선)."""
    fx.simulate(store, _at(2026, 9, 8, 0), 35)
    wk = ws.map_week("KR", ws.week_monday("2026-10-12"), _at(2026, 10, 13, 9, 0))
    cells = [r["cells"]["2026-10-12"] for r in wk["groups"]["sec"]]
    assert cells and all(c["basis"] == "20d" for c in cells), [c["basis"] for c in cells]
    assert "20d" in wk["basis"]


def test_size_band_weights_each_day_by_that_days_cap():
    """그날 가중 = 그날 시총의 전일 환산 — 마지막 날 시총으로 가중하면 월요일 칸이 금요일 규모로
    재진다(같은 구간 안에서 등락이 다른 두 종목으로 가른다)."""
    stocks = {
        "A": {"chg": [10.0, None, None, None, None], "val": [1.0, None, None, None, None],
              "caps": [110.0, None, None, None, None], "cap": 1.0, "fx": 0},
        "B": {"chg": [0.0, None, None, None, None], "val": [1.0, None, None, None, None],
              "caps": [1.0, None, None, None, None], "cap": 100.0, "fx": 0},
    }
    st = {"ok": [True, False, False, False, False], "days": list("abcde"), "stocks": stocks}
    day0 = wp.size_rows("KR", None, st)["rows"][0]["days"][0]
    assert day0 == pytest.approx(round((100 * 10 + 1 * 0) / 101, 2)), day0


def test_size_escape_reaches_through_collection(store, monkeypatch):
    """원천 종목 수가 정당하게 줄면(필터 변경) 70% 검사가 영영 막는다 — 다른 세션 둘이 같은 크기로
    막혔으면 셋째 세션에 새 기준으로 받는다. 수집 경로(`_collect`)를 통째로 태운다."""
    real = fx.fake_rows
    monkeypatch.setattr(fx, "fake_rows", lambda m, d, n=None: real(m, d, 500 if d <= "2026-10-08" else 320))
    for day in (6, 7, 8):
        store.now = _at(2026, 10, day, 23, 30)
        ws.tick(store.now)
    assert ws._base_rows("KR", "2026-10-12") == 500
    for day in (12, 13):
        store.now = _at(2026, 10, day, 19, 0)
        ws.tick(store.now)
        ms = ws.load_state()["markets"]["KR"]
        assert ms["kind"] == "small" and ms["small_seen"][f"2026-10-{day}"] == 320
    store.now = _at(2026, 10, 14, 19, 0)
    ws.tick(store.now)
    assert ws.agg_path("KR", "2026-10-14").exists(), "같은 크기로 두 세션 막혔으면 셋째엔 받아야 한다"


def test_regenerate_memo_holds_only_the_sector_stock_markets(tmp_path, monkeypatch):
    """재생성 메모는 섹터종목이 다시 읽는 나라만 붙든다 — 다섯 나라를 다 들면 정점 메모리가 오른다."""
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "archive")
    seen = {}
    real = wp.map_payloads

    def spy(now, memo=None):
        out = real(now, memo)
        seen["keys"] = {m for m, _mon in memo}
        return out
    monkeypatch.setattr(wp, "map_payloads", spy)
    with fx.patched(tmp_path) as fake:
        fx.simulate(fake, _at(2026, 10, 6, 0), 6)
        wp.regenerate(_at(2026, 10, 12, 9, 0))
    assert seen["keys"] == set(wp.STOCK_MARKETS)


# ── 2026-10-12 VM 실측 뒤(사용자 nav 순서 · --why 거짓 ✅) ───────────────────────────
def test_why_does_not_claim_fresh_before_anything_was_collected(store, capsys):
    """VM 실측: 상태 파일도 없고 다섯 나라가 전부 ⏳ 인데 마지막 줄이 '✅ 다섯 나라 다 최신' 이었다."""
    rc = ws.why(_at(2026, 10, 12, 16, 43))
    last = capsys.readouterr().out.strip().splitlines()[-1]
    # 첫날(수집 시작 전 놓친 세션) — ⚠️ 로 말하고 기다릴 것과 볼 것을 같이 적는다
    assert rc == 0 and "✅" not in last and last.startswith("판정: ⚠️ 확인할 것 — 창 놓침"), last
    assert "보유 목록 아직 안 물음 KR·US" in last and "수집 대기" in last, last


def test_why_says_all_received_only_when_every_market_and_holding_is_ok(store, capsys):
    fx.simulate(store, _at(2026, 9, 28, 0), 15)
    now = _at(2026, 10, 13, 9, 30)
    store.now = now
    ws.tick(now)
    ws._refresh_holdings(now)
    assert ws.why(now) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last == "판정: ✅ 다섯 나라 다 받음 · 보유 목록 이상 없음", last


@pytest.mark.parametrize("kinds, bad, want", [
    ({"ok": list(ws.MARKETS), "h_ok": ["KR", "US"]}, 0, "✅"),
    ({"ok": list(ws.MARKETS), "h_none": ["KR", "US"]}, 0, "⏳"),
    ({"ok": ["KR"], "wait": ["US", "JP", "TW", "CN_A"], "h_ok": ["KR", "US"]}, 0, "⏳"),
    ({"ok": list(ws.MARKETS)[:4], "holiday": ["CN_A"], "h_ok": ["KR", "US"]}, 0, "⚠️"),
    ({"fail": ["KR"]}, 1, "❌"),
    ({"ok": ["KR", "US"], "h_ok": ["KR", "US"]}, 0, "⚠️"),     # 두 나라만 판정됨 → '판정 없음' 을 이름으로
    ({"ok": list(ws.MARKETS)}, 0, "⚠️"),                        # 보유 목록 판정이 아예 없음
    ({"ok": list(ws.MARKETS), "h_ok": ["KR"]}, 0, "⚠️"),        # 보유 목록 하나만
    ({"ok": list(ws.MARKETS)[:4], "stale": ["CN_A"], "h_ok": ["KR", "US"]}, 0, "⚠️"),
    ({"ok": list(ws.MARKETS), "h_ok": ["KR"], "h_error": ["US"]}, 0, "⚠️"),
])
def test_why_summary_branches(kinds, bad, want):
    line = ws.why_summary(kinds, bad)
    assert line.startswith(want), line
    if want == "⏳":
        for k, ms in kinds.items():
            if k not in ("ok", "h_ok"):
                assert "·".join(ms) in line, (k, line)


def test_why_summary_never_says_no_failure_next_to_a_failure():
    """'실패는 없음' 옆에 '갱신 실패' 를 적으면 스스로 모순이다(독립 리뷰 L7) — 사람이 볼 것은 ⚠️."""
    line = ws.why_summary({"ok": list(ws.MARKETS), "h_ok": ["KR"], "h_stale": ["US"]}, 0)
    assert line.startswith("⚠️ 확인할 것 — ") and "보유 목록 오늘 갱신 실패" in line
    assert "실패는 없음" not in line
    line = ws.why_summary({"ok": ["KR", "JP", "TW", "CN_A"], "wait": ["US"], "h_none": ["KR", "US"]}, 0)
    assert line == "⏳ 아직 다 받지 않음 — 수집 대기 US · 보유 목록 아직 안 물음 KR·US"
    line = ws.why_summary({"ok": list(ws.MARKETS), "h_ok": ["KR"]}, 0)
    assert line == "⚠️ 확인할 것 — 판정 없음 보유 US", "빈 꼬리로 끝내지 않는다"


def test_why_names_only_the_unjudged_holdings_market_on_error(store, monkeypatch, capsys):
    calls = []

    def verdict(m, now):
        calls.append(m)
        if len(calls) > 1:
            raise RuntimeError("x")
        return "ok", "fine"
    monkeypatch.setattr(eh, "holdings_verdict", verdict)
    ws.why(_at(2026, 10, 12, 18, 40))
    last = capsys.readouterr().out.strip().splitlines()[-1]
    seg = last.split("보유 목록 판정 불가 ")[1].split(" · ")[0]
    assert seg == ws.HOLD_MARKETS[1], "이미 판정한 나라까지 '판정 불가' 로 적었다"


def test_verdict_survives_a_calendar_that_raises(store, monkeypatch):
    """달력이 던져도 상태 줄·재생성·`--why` 가 죽지 않는다(독립 리뷰 M2) — 보호는 `window_end` 한
    곳이라 그걸 부르는 판정·하루 상태·페이지가 다 산다(델타 리뷰 L5: 판정에만 걸었더니 나머지가 죽었다)."""
    def boom(m, d):
        raise RuntimeError("calendar")
    monkeypatch.setattr(ws, "next_open", boom)
    now = _at(2026, 10, 12, 18, 40)
    assert ws.window_end("KR", "2026-10-12") is None
    kind, line = ws.market_verdict("KR", ws.load_state(), now)
    assert kind in ("wait", "none", "fail", "stale", "ok"), (kind, line)
    ws.market_day_status("KR", "2026-10-12", now, ws.load_state())
    wp.map_payloads(now)


def test_verdict_infers_the_miss_exactly_at_the_window_end(store):
    now = _at(2026, 10, 12, 18, 40)
    due = ws.due_session("KR", now)
    end = ws.window_end("KR", due)
    assert end is not None
    kind, _ = ws.market_verdict("KR", ws.load_state(), end.astimezone(K))
    assert kind != "wait", "창 끝 시각엔 이미 놓침"



def test_a_session_past_its_window_is_not_reported_as_waiting(store):
    """창이 지난 세션을 '다음 주기에 받는다(⏳)' 고 적으면 거짓 약속이다 — 다음 주기에 기록될
    판정을 지금 말한다(VM 실측 16:43: KR 10-08 은 07:30 에 창이 닫혔다)."""
    kind, line = ws.market_verdict("KR", ws.load_state(), _at(2026, 10, 12, 16, 43))
    assert kind == "missed" and "수집을 시작하기 전" in line, (kind, line)
    store.now = _at(2026, 10, 8, 18, 40)
    ws.tick(store.now)                                    # 받은 적이 생기면 그 뒤 놓침은 우리 실패
    kind, _ = ws.market_verdict("KR", ws.load_state(), _at(2026, 10, 13, 8, 0))
    assert kind == "missed_fail"
    kind, _ = ws.market_verdict("US", ws.load_state(), _at(2026, 10, 12, 16, 43))
    assert kind == "wait", "미국 10-09 는 아직 창 안(프리마켓 04:00 ET = 17:00 KST 전)"


def test_new_board_nav_runs_into_the_market_flow_boards_in_the_users_order():
    hrefs = re.findall(r'href="([^"]+)"', wp._NAV)
    assert hrefs == ["market.html", "index.html", "sector_mix.html", "sentiment_map.html",
                     "sector_stocks.html", "liquidity.html", "market_timing.html",
                     "breadth_strategy.html", "bollinger.html"]


def test_hub_nav_order_follows_the_user():
    from bot.dashboard import _render_market_page
    html = _render_market_page({})
    nav = html[html.index('<div class="nav">'):]
    nav = nav[:nav.index("</div>")]
    hrefs = re.findall(r'href="([^"]+)"', nav)
    want = ["bollinger.html", "sector_mix.html", "sentiment_map.html", "sector_stocks.html",
            "marketcap.html", "econ_calendar.html", "daily_inst.html", "daily_byte.html",
            "reddit_insider.html", "blog.html", "trade/", "realestate.html"]
    got = [h for h in hrefs if h in want]
    assert got == want, got


# ── '언제 갱신되나'(사용자 2026-10-13 "이 보드는 언제 업데이트되는거야?") ──────────────
def test_ready_kst_follows_the_calendar_close_and_dst():
    """시각은 달력의 실제 마감 + READY_MIN — 미국은 서머타임이면 이튿날 08:00, 아니면 09:00. 나라별
    시각을 리터럴로 적으면 마감이나 READY_MIN 이 바뀔 때 문구만 남는다(#38·#55)."""
    t, d = ws.ready_kst("US", _at(2026, 10, 13, 11))
    assert (d, t.strftime("%m-%d %H:%M")) == ("2026-10-13", "10-14 08:00")
    t, d = ws.ready_kst("US", _at(2026, 12, 14, 11))
    assert t.strftime("%H:%M") == "09:00"
    t, d = ws.ready_kst("KR", _at(2026, 10, 12, 11))               # 오늘 이하 마지막 세션(10-09 휴장)
    assert (d, t.strftime("%H:%M")) == ("2026-10-12", "18:30")
    t, d = ws.ready_kst("TW", _at(2026, 10, 13, 11))
    assert t.strftime("%H:%M") == "17:30"


def test_when_line_names_every_market_and_the_tick(monkeypatch):
    line = wp.when_line(_at(2026, 10, 13, 11))
    assert "장 마감 3시간 뒤" in line and f"{ws.TICK_MIN}분마다" in line
    assert "미국 이튿날 08:00" in line and "한국 18:30" in line and "대만 17:30" in line
    assert "중국 19:00" in line and "일본 18:30" in line
    only = wp.when_line(_at(2026, 10, 13, 11), ("KR", "US"))
    assert "대만" not in only and "미국 이튿날 08:00" in only
    monkeypatch.setattr(ws, "READY_MIN", 120)
    assert "한국 17:30" in wp.when_line(_at(2026, 10, 13, 11)) and "장 마감 2시간 뒤" in wp.when_line(
        _at(2026, 10, 13, 11))
    monkeypatch.setattr(ws, "ready_kst", lambda m, now=None: None)
    assert "한국 시각 미확인" in wp.when_line(_at(2026, 10, 13, 11))


def test_all_three_guides_say_when(store):
    now = _at(2026, 10, 13, 11)
    want = wp.when_line(now)
    mix = wp.render_mix([], ws.load_state(), now)
    smap = wp.render_map({}, ws.load_state(), now)
    stk = wp.render_stocks({}, ws.load_state(), now)
    for h in (mix, smap):
        g = h[h.index("ℹ️ 이 보드 읽는 법"):h.index("</details>", h.index("ℹ️ 이 보드 읽는 법"))]
        assert f"<b>언제 갱신되나</b> — {want}" in g
    g = stk[stk.index("ℹ️ 이 보드 읽는 법"):]
    assert f"<b>언제 갱신되나</b> — {wp.when_line(now, wp.STOCK_MARKETS)}" in g


def test_world_tick_matches_what_the_guide_says():
    import ast
    tree = ast.parse(open("bot/telegram_bot.py", encoding="utf-8").read())
    v = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
         and any(getattr(t, "id", "") == "_WORLD_SECTORS_TICK_SEC" for t in n.targets)][0]
    assert ast.literal_eval(v.value) == ws.TICK_MIN * 60


def test_ready_kst_uses_the_next_session_across_dst():
    """다음에 받을 세션 기준 — 2026-11-01(일) 에는 월요일(서머타임 끝) 세션이라 09:00. 지난 금요일
    세션(08:00)을 적으면 그 주말 내내 틀린 시각이다(리뷰 2026-10-13)."""
    t, d = ws.ready_kst("US", _at(2026, 11, 1, 11))
    assert (d, t.strftime("%m-%d %H:%M")) == ("2026-11-02", "11-03 09:00")
    t, d = ws.ready_kst("KR", _at(2026, 10, 12, 19))               # 그날 18:30 이 지났으면 다음 세션
    assert d == "2026-10-13"
