"""🗞️ 보유종목 News — 10분 간격 실시간 등락(사용자 2026-10-19 "종목가격을 실시간비슷하게 10분에 한번씩").

재는 것: 서버가 이어 붙일 세션(`live_d`)을 정하는 규칙(장 중 · 종가 수집 전 · 안 센 세션이 둘이면 안 붙임) · 화면 재료
(`t`·`ld`·`m`) · `live_pct` 의 세션 날짜·현재가·10분 신선도(탭이 몇 개든 한 번) · 화면 JS 가 세션 날짜가 맞을 때만
종가 누적에 곱해 요약·카드·칸 이름·시세 반영 칸을 다시 그린다 · 주기(10분 · pending 이면 15초 몇 번). 네트워크 0.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time

import pytest

import bot.live_pct as lp
from tests.test_holdings_news_20261012 import (  # noqa: F401 — 픽스처는 이름으로 잡힌다
    _PNL_HARNESS, _closes, _pf, _rec, _stock, _t, hn, ws_snaps)

LIVE_POLL_MS = 600_000      # 사용자 2026-10-19 "10분에 한번씩" — 리터럴로 못박는다(제품 상수를 읽으면 크기를 못 잰다, #66)


# ── 서버: 이어 붙일 세션 ──────────────────────────────────────────────────────
def test_live_session_is_the_single_uncounted_session(hn, ws_snaps):
    ws_snaps("KR", "2026-10-08", [("KRX:000001", 1.0)])
    st = {"market": "KR", "ticker": "000001"}
    base = _t("2026-10-07T18:00").timestamp()
    assert hn.cum_lookup(base, _t("2026-10-12T10:00"))(st)["live_d"] == "2026-10-12"   # 장 중(10-09 한글날)
    assert hn.cum_lookup(base, _t("2026-10-12T08:30"))(st)["live_d"] is None          # 장 전 — 붙일 것 없음
    # 15:30 마감 · 하루치는 마감 3시간 뒤 — 그 사이는 마감한 세션을 실시간(그 세션 하루치 등락)으로 잇는다
    assert hn.cum_lookup(base, _t("2026-10-12T17:00"))(st)["live_d"] == "2026-10-12"


def test_two_uncounted_sessions_are_never_bridged_by_one_live_change(hn, ws_snaps, monkeypatch):
    """종가를 못 받은 세션 뒤에 새 세션이 열리면 안 센 세션이 둘 — 실시간(직전 종가 대비 하루치)은 하나만 덮으므로
    붙이면 가운데 하루를 빼먹은 값이다(#165)."""
    import bot.world_sectors as ws
    monkeypatch.setattr(ws, "market_day_status", lambda mk, d, now, state=None: "pending")
    c = hn.cum_lookup(_t("2026-10-07T18:00").timestamp(), _t("2026-10-12T10:00"))(
        {"market": "KR", "ticker": "000001"})
    assert c["wait"] == ["2026-10-08"] and c["live_d"] is None


def test_page_carries_live_targets_and_the_live_heading(hn, ws_snaps):
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    rec = _rec({**_stock("KR:005930"), "name": "삼성전자", "value": 150_000_000.0})
    html = hn.render_page([rec], {}, _t("2026-10-12T10:00"), gen="GEN", portfolio=_pf("2026-10-08T08:00"))
    el = html.split("id='hn-pnl-data'")[1]
    data = {d["k"]: d for d in json.loads(el.split(">", 1)[1].split("</script>")[0])}
    assert data["KR:005930"]["t"] == "005930" and data["KR:005930"]["ld"] == "2026-10-12"
    assert data["KR:005930"]["m"] == "KR"
    assert "t" not in data["US:AAPL"]                     # 종가 누적이 없는 종목은 묻지 않는다(지어내지 않는다)
    # 칸 이름 — 서버 값은 '전일대비'(10-08 한 세션) · 실시간을 붙이면 10-12 까지 두 세션
    assert "data-lh='10-08 기록 이후(2세션) · 국내'><div class='k'>전일대비 · 국내</div>" in html
    asof = json.loads(html.split("id='hn-asof-data'>")[1].split("</script>")[0])
    assert asof == [{"m": "KR", "n": "한국", "d": "10-08"}, {"m": "US", "n": "미국", "d": ""}]
    assert "10분마다 받는 실시간 등락" in html


def test_live_heading_counts_the_live_session_and_drops_the_wait(hn):
    c = {"ka": 1.01, "sess": 1, "wait": ["2026-10-12"], "live_d": "2026-10-12"}
    ctx = {"cum": {"k": c}, "at": "10-08 08:00", "newk": set()}
    pos = [{"key": "k", "market": "KR"}]
    assert hn.pnl_span_head(ctx, pos, "KR") == "10-08 기록 이후(1세션)"          # 받는 중이라 '전일대비' 아님
    assert hn.pnl_span_head(ctx, pos, "KR", live=True) == "10-08 기록 이후(2세션)"
    c2 = {"ka": 1.0, "sess": 0, "wait": ["2026-10-12"], "live_d": "2026-10-12"}
    ctx["cum"]["k"] = c2
    assert hn.pnl_span_head(ctx, pos, "KR", live=True) == "전일대비"               # 남은 한 세션을 실시간으로 채움


def test_live_ticker_shape(hn):
    assert hn.live_ticker({"market": "KR", "ticker": "005930", "sym": "005930.KS"}) == "005930"
    assert hn.live_ticker({"market": "JP", "ticker": "7203.T", "sym": "7203.t"}) == "7203.T"
    assert hn.live_ticker({"market": "US", "ticker": "AAPL"}) == "AAPL"


# ── live_pct: 세션 날짜 · 현재가 · 10분 신선도 ───────────────────────────────
@pytest.mark.parametrize("ts,want", [("2026-10-19T15:30:00+09:00", "2026-10-19"), ("20261019", "2026-10-19"),
                                     ("2026-10-16T16:00:00-04:00", "2026-10-16"), ("", None), ("r", None),
                                     (None, None)])
def test_session_date_reads_the_exchange_local_date(ts, want):
    assert lp.session_date(ts) == want


def test_chart_data_uses_the_same_session_date():
    from datetime import date

    import bot.chart_data as cd
    assert cd._quote_date("20260615") == date(2026, 6, 15) and cd._quote_date("x") is None
    assert cd._quote_date("2026-06-15T15:30:00+09:00") == date(2026, 6, 15)


def test_fetch_one_carries_session_date_and_price(monkeypatch):
    import bot.finviz_client as fc
    import bot.naver_quote as nq
    monkeypatch.setattr(fc, "naver_paused", lambda: False)
    monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: {"pct": 1.234, "price": 61000.0,
                                                         "ts": "2026-10-19T10:00:00+09:00"})
    assert lp.fetch_one("005930") == {"pct": 1.23, "ts": "2026-10-19T10:00:00+09:00", "src": "네이버",
                                      "d": "2026-10-19", "price": 61000.0}
    monkeypatch.setattr(nq, "fetch_kr_quote", lambda t: {"pct": 1.0, "price": "x", "ts": "?"})
    assert lp.fetch_one("005930") == {"pct": 1.0, "ts": "?", "src": "네이버"}       # 못 읽은 건 싣지 않는다


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setattr(lp, "_TABLE", {})
    monkeypatch.setattr(lp, "_ASKED", {})
    monkeypatch.setattr(lp, "_PENDING", set())
    monkeypatch.setattr(lp, "_RUNNING", False)
    return lp._TABLE


def test_lookup_fresh_window_lets_holdings_ask_every_ten_minutes(table):
    """같은 표 — 국가별 표(1분)와 보유종목(10분)이 같이 쓴다. 10분 신선도면 5분 된 값은 다시 받지 않는다(탭이 몇 개든)."""
    now = time.time()
    table["005930"] = (now - 300, {"pct": 1.0})
    r = lp.lookup(["005930"], now, kick=False, fresh=600)
    assert r["pending"] == 0 and r["q"]["005930"]["age"] == 300
    assert lp.lookup(["005930"], now, kick=False)["pending"] == 1                       # 기본(1분)이면 낡음
    table["005930"] = (now - 700, {"pct": 1.0})
    assert lp.lookup(["005930"], now, kick=False, fresh=600)["pending"] == 1
    table["005930"] = (now - 3 * 3600, {"pct": 1.0})
    assert lp.lookup(["005930"], now, kick=False, fresh=99_999)["pending"] == 1        # 상한(1시간)으로 묶는다
    table["005930"] = (now - 30, {"pct": 1.0})
    assert lp.lookup(["005930"], now, kick=False, fresh=1)["pending"] == 0             # 하한(1분)
    assert lp.lookup(["005930"], now, kick=False, fresh="x")["pending"] == 0           # 못 읽으면 기본


def test_api_passes_the_fresh_window(monkeypatch):
    import bot.dashboard_server as ds
    seen = []
    monkeypatch.setattr(lp, "lookup", lambda raw, **kw: seen.append((raw, kw)) or {"q": {}, "pending": 0,
                                                                                  "capped": False})
    h = ds.DashboardHandler.__new__(ds.DashboardHandler)
    h.path = "/api/live_pct?t=005930&fresh=600"
    h._reply_json = lambda status, body: None
    h._handle_live_pct()
    assert seen == [(["005930"], {"fresh": "600"})]


# ── 화면 JS ─────────────────────────────────────────────────────────────────
_EXTRA = r"""
['all','KR','XX'].forEach(function(g){var v=els['hn-pnl-'+g];
  var k={textContent:'H0-'+g,attrs:{},getAttribute:function(a){return this.attrs[a]===undefined?null:this.attrs[a];},
    setAttribute:function(a,b){this.attrs[a]=b;}};
  v.parentNode={attrs:(typeof LH!=='undefined'&&LH[g])?{'data-lh':LH[g]}:{},
    getAttribute:function(a){return this.attrs[a]===undefined?null:this.attrs[a];},
    querySelector:function(){return k;}};
  els['k-'+g]=k;});
var asofBox=null;
if(typeof ASOF!=='undefined'){mk('hn-asof-data',JSON.stringify(ASOF));
  var av={textContent:'한국 10-08',attrs:{},getAttribute:function(a){return this.attrs[a]===undefined?null:this.attrs[a];},
    setAttribute:function(a,b){this.attrs[a]=b;}};
  var ak={textContent:'시세 반영(마지막 종가)',attrs:{},getAttribute:av.getAttribute,setAttribute:av.setAttribute};
  asofBox={querySelector:function(s){return s==='.v'?av:ak;}}; els['hn-st-asof']=asofBox; els['asof-v']=av;
  els['asof-k']=ak;}
"""


def _run(hn, data, resp, *, cell=None, lh=None, asof=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    src = (f"var DATA={json.dumps(json.dumps(data))}; var RESP={json.dumps(resp)};"
           f"var BASIS={json.dumps(hn.PNL_BASIS)};"
           + (f"var CELL={json.dumps(cell)};" if cell is not None else "")
           + (f"var LH={json.dumps(lh)};" if lh is not None else "")
           + (f"var ASOF={json.dumps(asof)};" if asof is not None else "")
           + _PNL_HARNESS + "els['hn-pnl-data'].attrs['data-poll']='600';" + _EXTRA
           + hn._PNL_JS
           + "\nprocess.on('exit',function(){console.log(JSON.stringify("
             "{all:els['hn-pnl-all'].textContent,kr:els['hn-pnl-KR'].textContent,"
             "krsub:els['hn-pnl-KR-sub'].textContent,krk:els['k-KR'].textContent,"
             "asofv:els['asof-v']?els['asof-v'].textContent:'',asofk:els['asof-k']?els['asof-k'].textContent:'',"
             "cell:cell?cell.textContent:'',cls:cell?cell.className:'',title:cell?(cell.attrs.title||''):'',"
             "urls:fetched,delays:delays}));});")
    r = subprocess.run([node, "-e", src], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


_KR = {"k": "KR:005930", "v": 100_000_000, "g": "KR", "p": 1.0, "t": "005930", "ld": "2026-10-12", "m": "KR"}


def test_js_compounds_live_change_only_for_the_matching_session(hn):
    q = {"005930": {"pct": 2.0, "price": 61000.0, "d": "2026-10-12", "age": 30, "src": "네이버"}}
    o = _run(hn, [_KR], {"q": q, "pending": 0}, lh={"KR": "10-08 기록 이후(2세션) · 국내"},
             asof=[{"m": "KR", "n": "한국", "d": "10-08"}])
    assert o["kr"] == "+302만원 (+3.02%)"                        # (1.01 × 1.02 − 1) × 1억
    assert o["krsub"] == "종가 기준 1/1종목 · 실시간 1종목" and o["krk"] == "10-08 기록 이후(2세션) · 국내"
    assert o["urls"] == ["api/live_pct?t=005930&fresh=600"] and o["delays"] == [LIVE_POLL_MS]
    assert o["asofv"].startswith("한국 10-12 실시간 · 실시간 ") and o["asofv"].endswith(" KST 받음")
    assert o["asofk"] == "시세 반영(마지막 종가 · 실시간 10분 간격)"
    # 원천이 다른 세션의 등락을 줬다(장이 바뀌었는데 화면이 낡음) — 붙이지 않고 종가 누적 그대로 · 이름도 그대로
    q["005930"]["d"] = "2026-10-08"
    o = _run(hn, [_KR], {"q": q, "pending": 0}, lh={"KR": "LIVE"}, asof=[{"m": "KR", "n": "한국", "d": "10-08"}])
    assert o["kr"] == "+100만원 (+1.00%)" and o["krsub"] == "종가 기준 1/1종목" and o["krk"] == "H0-KR"
    assert o["asofv"] == "한국 10-08" and o["asofk"] == "시세 반영(마지막 종가)"


def test_js_keeps_the_server_heading_when_only_some_targets_got_live(hn):
    b = dict(_KR, k="KR:000660", t="000660")
    q = {"005930": {"pct": 2.0, "d": "2026-10-12", "age": 1, "src": "네이버"}}
    o = _run(hn, [_KR, b], {"q": q, "pending": 0}, lh={"KR": "LIVE"})
    assert o["krk"] == "H0-KR" and o["krsub"] == "종가 기준 2/2종목 · 실시간 1종목"


def test_js_asks_nothing_without_live_targets_and_retries_pending_a_few_times(hn):
    o = _run(hn, [{"k": "KR:A", "v": 1, "g": "KR", "p": 1.0}], {"q": {}, "pending": 0})
    assert o["urls"] == [] and o["kr"] == "SERVER"                                  # 묻지도 다시 그리지도 않는다
    o = _run(hn, [_KR], {"q": {}, "pending": 1})
    assert o["delays"] == [15_000]                                                  # 서버가 받는 중 — 곧 다시


def test_js_card_shows_the_live_price_and_recolors(hn):
    cell = {"data-p": "1.0", "data-v": "100000000", "data-k": "KR:005930", "data-cu": "KRW", "data-sym": "₩",
            "data-px": "₩60,000", "data-tip": "TIP"}
    q = {"005930": {"pct": -3.0, "price": 61234.0, "d": "2026-10-12", "age": 1, "src": "네이버"}}
    o = _run(hn, [_KR], {"q": q, "pending": 0}, cell=cell)
    assert o["cell"] == "(₩61,234 · -2.03% · -203만원)"           # 1.01 × 0.97 − 1
    assert "hn-dn" in o["cls"].split() and "hn-up" not in o["cls"].split()
    assert o["title"].startswith("₩61,234 = 2026-10-12 실시간(정규장 · 네이버 · ") and o["title"].endswith("TIP")
    assert "종가 누적 +1.00% × 그 세션 -3.00%" in o["title"]
    cell["data-k"] = "KR:없음"                                    # 실시간이 없는 카드 — 서버 종가 그대로
    o = _run(hn, [_KR], {"q": q, "pending": 0}, cell=cell)
    assert o["cell"] == "(₩60,000 · +1.00% · +100만원)" and o["title"] == "TIP"


@pytest.mark.parametrize("n,cu,want", [(61234.0, "KRW", "₩61,234"), (123.456, "USD", "$123.46"),
                                       (1234.5, "USD", "$1,234"), (1235.5, "USD", "$1,236"),
                                       (2500.4, "JPY", "¥2,500"), (0.125, "USD", "$0.12")])
def test_js_price_format_matches_python(hn, n, cu, want):
    from bot.dashboard import _currency_sym
    from bot.quarterly_series import fmt_price
    assert fmt_price(n, cu) == want
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    src = (_PNL_HARNESS.replace("var data=mk('hn-pnl-data',DATA); data.attrs['data-basis']=BASIS;", "")
           .replace("global.fetch=", "var _f=")
           + hn._PNL_JS + f"\nconsole.log(window.hnPx({n},{json.dumps(cu)},{json.dumps(_currency_sym(cu))}));")
    r = subprocess.run([node, "-e", "var RESP={};" + src], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == want


def test_lookup_nan_fresh_falls_back_to_the_default(table):
    """`fresh=nan` 은 비교가 늘 거짓이라 그 티커가 영영 다시 안 받힌다 — 기본 간격으로 읽는다."""
    now = time.time()
    table["005930"] = (now - 120, {"pct": 1.0})
    assert lp.lookup(["005930"], now, kick=False, fresh="nan")["pending"] == 1
    assert lp.lookup(["005930"], now, kick=False, fresh=float("nan"))["pending"] == 1
