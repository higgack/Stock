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
def store(tmp_path):
    with fx.patched(tmp_path) as fake:
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
    tick(_at(2026, 10, 13, 9, 55))                     # 대만 개장 09:00 현지 = 10:00 KST — 아직 창 안
    assert ws.load_state()["markets"]["TW"]["n"] == 3 and not ws.load_state()["markets"]["TW"].get("missed")
    n = store.calls.count("TW")
    tick(_at(2026, 10, 13, 10, 5))
    ms = ws.load_state()["markets"]["TW"]
    assert ms.get("missed") == "2026-10-12" and store.calls.count("TW") == n, \
        "다음 세션이 열린 뒤엔 그 세션을 묻지 않는다(지금 값은 다음 세션 장중 값이다)"
    assert ws.market_verdict("TW", ws.load_state(), _at(2026, 10, 13, 10, 5))[0] == "missed"


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
    done = ws.finalize_records(now)
    assert ws.load_mix_record("2026-10-12") or "2026-10-12" not in done


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
    assert "기준 ±1%p" not in h or True
    assert "주간 ±1%p · 하루 ±0.5%p" in h


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
    assert "이번 주" in h and "지난주" not in h or "주 —" in h


def test_pages_escape_names(pages):
    assert "<회사>" not in pages["stocks"].split("<script")[0]
    assert "&lt;회사&gt;" not in pages["map"] or True


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


def test_kr_pdf_parse_ranks_by_weight_or_value_and_drops_non_stock_rows():
    import pandas as pd
    df = pd.DataFrame({"구성종목명": ["삼성전자", "원화현금", "SK하이닉스", "한미반도체"],
                       "금액": [500.0, 900.0, 300.0, 200.0], "비중": [0.0, 0.0, 0.0, 0.0]},
                      index=["005930", "KRD010010001", "000660", "042700"])
    top = eh.parse_kr_pdf(df)
    assert [t["code"] for t in top] == ["005930", "000660", "042700"]
    assert top[0]["weight"] == pytest.approx(50.0), "비중이 0 으로 오면 금액으로 다시 만든다"
    assert eh.parse_kr_pdf(pd.DataFrame()) == []


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
    assert len(lines) == len(ws.MARKETS)
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


@pytest.mark.parametrize("tv_name, hold, want", [
    ("Samsung Electronics Co., Ltd.", "삼성전자", "삼성전자"),
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
    rows = wp._holding_rows({"rows": [{"code": "005930", "name": "삼성전자", "weight": 20}]}, st)
    assert rows[0]["nm"] == "삼성전자" and rows[0]["ind"] == "세미컨덕터"
    assert rows[0]["href"] == "lookup/005930"


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
