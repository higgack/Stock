"""종목명 옆 실시간 등락(`bot/live_pct.py`, 사용자 2026-10-13 "() 안에 등락률은 가능한 실시간으로 해주고,
보유종목 News 대시보드도 포함해서") — 시세 갈래 · 요청 경로 무네트워크(SWR) · 화면 배선."""
import json
import shutil
import subprocess

import pytest

import bot.live_pct as lp


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(lp, "_TABLE", {})
    monkeypatch.setattr(lp, "_ASKED", {})
    monkeypatch.setattr(lp, "_PENDING", set())
    monkeypatch.setattr(lp, "_RUNNING", False)


class _SyncThread:
    """`Thread.start()` 를 그 자리에서 돌린다 — 시간·순서로 재지 않는다(#128)."""
    def __init__(self, target=None, **kw):
        self.target = target

    def start(self):
        self.target()


def _stub_quotes(monkeypatch, table, calls=None):
    import bot.naver_quote as nq
    import bot.tw_quote as tq
    import bot.world_quote as wq

    def mk(name):
        def f(t):
            if calls is not None:
                calls.append((name, t))
            return table.get(t)
        return f
    monkeypatch.setattr(nq, "fetch_kr_quote", mk("kr"))
    monkeypatch.setattr(tq, "fetch_tw_quote", mk("tw"))
    monkeypatch.setattr(wq, "fetch_world_quote", mk("world"))


@pytest.mark.parametrize("t,want", [("005930", "KR"), ("0009K0", "KR"), ("005930.KS", "KR"), ("AAPL", "US"),
                                     ("BRK-B", "US"), ("7203.T", "JP"), ("2330.TW", "TW"),
                                     ("6488.TWO", "TW"), ("600519.SS", "CN_A")])
def test_market_of_reads_bare_six_digits_as_korea(t, want):
    """접미 없는 6자리는 국내 — `detect_market` 은 미국으로 본다(링크가 국내를 접미 없이 싣는다, #399)."""
    assert lp.market_of(t) == want


@pytest.fixture
def unpaused(monkeypatch):
    import bot.finviz_client as fc
    monkeypatch.setattr(fc, "naver_paused", lambda: False)


def test_fetch_one_routes_each_market_to_its_live_source(monkeypatch, unpaused):
    calls = []
    _stub_quotes(monkeypatch, {"005930": {"pct": 1.234}, "2330.TW": {"pct": -0.5},
                               "AAPL": {"pct": 2.0, "ts": "r", "over_session": "AFTER_MARKET",
                                        "over_ts": "o", "over_pct": 9.9}}, calls)
    assert lp.fetch_one("005930") == {"pct": 1.23, "ts": None, "src": "네이버"}
    assert lp.fetch_one("2330.TW")["src"] == "TWSE"
    assert lp.fetch_one("AAPL") == {"pct": 2.0, "ts": "r", "src": "네이버"}, \
        "정규장 등락·정규장 시각 — 시간외 값·라벨을 섞지 않는다(사용자 2026-06-16, 리뷰 2026-10-13)"
    assert [c[0] for c in calls] == ["kr", "tw", "world"]


def test_fetch_one_skips_what_it_cannot_ask(monkeypatch):
    """네이버 일시정지 · 숫자 아닌 국내 코드 · 재 보지 않은 .BJ — 원천을 부르지 않는다(헛 요청 금지)."""
    import bot.finviz_client as fc
    calls = []
    _stub_quotes(monkeypatch, {"0009K0": {"pct": 1.0}, "AAPL": {"pct": 1.0}, "430000.BJ": {"pct": 1.0},
                               "2330.TW": {"pct": 1.0}}, calls)
    monkeypatch.setattr(fc, "naver_paused", lambda: False)
    assert lp.fetch_one("0009K0") is None and lp.fetch_one("430000.BJ") is None and calls == []
    monkeypatch.setattr(fc, "naver_paused", lambda: True)
    assert lp.fetch_one("AAPL") is None and lp.fetch_one("005930") is None and calls == []
    assert lp.fetch_one("2330.TW")["pct"] == 1.0, "TWSE 는 네이버가 아니라 일시정지와 무관"
    monkeypatch.setattr(lp, "market_of", lambda t: "XX")
    assert lp.fetch_one("ZZ") is None


@pytest.mark.parametrize("q", [None, {}, {"pct": None}, {"pct": "x"}, {"pct": float("nan")}, {"pct": 5000}])
def test_fetch_one_drops_unusable_values(monkeypatch, unpaused, q):
    _stub_quotes(monkeypatch, {"AAPL": q})
    assert lp.fetch_one("AAPL") is None


def test_request_path_never_fetches_and_refreshes_in_the_background(monkeypatch, unpaused):
    calls = []
    _stub_quotes(monkeypatch, {"AAPL": {"pct": 1.0}, "005930": {"pct": -2.0}}, calls)
    started = []
    monkeypatch.setattr(lp, "_Thread", lambda target=None, **kw: started.append(target) or _Noop())
    r = lp.lookup(["AAPL", "005930"], now=1000.0)
    assert r == {"q": {}, "pending": 2, "capped": False} and calls == [], "요청 경로는 네트워크를 안 부른다"
    assert len(started) == 1
    lp.lookup(["AAPL"], now=1001.0)
    assert len(started) == 1, "돌고 있으면 스레드를 또 띄우지 않는다"
    started[0]()                                           # 뒤에서 받는다
    assert sorted(t for _, t in calls) == ["005930", "AAPL"]
    r = lp.lookup(["AAPL", "005930"], now=time_after(lp, 0))
    assert r["q"]["AAPL"]["pct"] == 1.0 and r["q"]["005930"]["pct"] == -2.0 and r["pending"] == 0


class _Noop:
    def start(self):
        pass


def time_after(mod, sec):
    return max(t for t, _ in mod._TABLE.values()) + sec


def test_fresh_window_and_failures_are_not_refetched_within_it(monkeypatch, unpaused):
    calls = []
    _stub_quotes(monkeypatch, {"AAPL": {"pct": 1.0}}, calls)    # MSFT 은 못 받는다
    monkeypatch.setattr(lp, "_Thread", _SyncThread)
    lp.lookup(["AAPL", "MSFT"], now=0.0)
    n = len(calls)
    base = time_after(lp, 0)
    r = lp.lookup(["AAPL", "MSFT"], now=base + lp.FRESH_SEC - 1)
    assert len(calls) == n and r["pending"] == 0 and "MSFT" not in r["q"], "실패도 짧게 기억한다(#303)"
    lp.lookup(["AAPL", "MSFT"], now=base + lp.FRESH_SEC)
    assert len(calls) == 2 * n


def test_cap_and_ticker_shape(monkeypatch):
    monkeypatch.setattr(lp, "_Thread", lambda **kw: _Noop())
    many = [f"T{i}" for i in range(lp.MAX_TICKERS + 5)]
    r = lp.lookup(many + ["<script>", "a b"], now=0.0)
    assert r["capped"] is True and r["pending"] == lp.MAX_TICKERS


def test_thread_start_failure_does_not_wedge_the_refresher(monkeypatch):
    class Boom:
        def __init__(self, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(lp, "_Thread", Boom)
    lp.lookup(["AAPL"], now=0.0)
    assert lp._RUNNING is False, "그대로 두면 다음 요청도 영영 안 띄운다(#371)"


def test_unasked_tickers_are_pruned(monkeypatch):
    monkeypatch.setattr(lp, "_Thread", lambda **kw: _Noop())
    lp.lookup(["AAPL"], now=0.0)
    lp._TABLE["AAPL"] = (0.0, {"pct": 1.0})
    lp.lookup(["MSFT"], now=lp._KEEP_SEC + 1)
    assert "AAPL" not in lp._TABLE and "AAPL" not in lp._ASKED


# ── 화면 쪽
_NODE = shutil.which("node")


def _node(script: str) -> str:
    return subprocess.run([_NODE, "-e", script], capture_output=True, text=True, timeout=30).stdout


_DOM = r"""
function El(attrs,cls){this.attrs=attrs||{};this.textContent='';this.cls={};(cls||[]).forEach(function(c){this.cls[c]=1;},this);
  var self=this;this.classList={toggle:function(c,on){if(on)self.cls[c]=1;else delete self.cls[c];}};}
El.prototype.getAttribute=function(k){return k in this.attrs?this.attrs[k]:null;};
El.prototype.hasAttribute=function(k){return k in this.attrs;};
El.prototype.setAttribute=function(k,v){this.attrs[k]=String(v);};
"""


@pytest.mark.skipif(not _NODE, reason="node 없음")
def test_live_js_paints_value_colour_and_kst_title_and_keeps_server_text_when_missing():
    script = _DOM + r"""
    var a=new El({'data-t':'AAPL','data-cu':'u','data-cd':'d',title:'old'});
    var b=new El({'data-t':'005930','data-cu':'u','data-cd':'d'},['u']); b.textContent='(+1.00%)';
    var z=new El({'data-t':'ZERO','data-cu':'u','data-cd':'d'},['u']);
    var urls=[];
    global.document={readyState:'complete',querySelectorAll:function(){return [a,b,z];}};
    global.window={};
    global.setTimeout=function(){return 0;}; global.clearTimeout=function(){};
    global.fetch=function(u){urls.push(u);return Promise.resolve({ok:true,json:function(){
      return {q:{AAPL:{pct:-1.5,src:'네이버',ts:'2026-10-13T15:30:00+09:00',session:''},
        ZERO:{pct:0,src:'네이버',ts:null,session:''}},pending:0};}});};
    """ + lp.LIVE_JS + r"""
    setTimeout=global.setTimeout;
    Promise.resolve().then(function(){}).then(function(){}).then(function(){}).then(function(){
      console.log(JSON.stringify({a:a.textContent,acls:Object.keys(a.cls),at:a.attrs.title,
        b:b.textContent,bcls:Object.keys(b.cls),url:urls[0],z:z.textContent,zcls:Object.keys(z.cls)}));});
    """
    js = json.loads(_node(script).strip().splitlines()[-1])
    assert js["a"] == "(-1.50%)" and js["acls"] == ["d"]
    assert "실시간 등락률(정규장" in js["at"] and "15:30 KST" in js["at"], js["at"]
    assert js["b"] == "(+1.00%)" and js["bcls"] == ["u"], "못 받은 칸은 서버가 그린 것을 그대로 둔다"
    assert js["z"] == "(0.00%)" and js["zcls"] == [], "보합은 색이 없다 — 앞선 상승 색을 지운다"
    assert js["url"] == "api/live_pct?t=" + "AAPL%2C005930%2CZERO", "상대 경로(토큰 경로 보존, #359)"


@pytest.mark.skipif(not _NODE, reason="node 없음")
def test_board_table_adds_live_span_only_for_linked_rows_and_fills_after_draw():
    import bot.world_sector_pages as wp
    script = r"""
    function El(attrs){this.attrs=attrs||{};this.innerHTML='';this.textContent='';this.value='';
      this.style={};this.kids={};this.handlers={};}
    El.prototype.getAttribute=function(k){return k in this.attrs?this.attrs[k]:null;};
    El.prototype.querySelector=function(s){return this.kids[s]||(this.kids[s]=new El());};
    El.prototype.querySelectorAll=function(){return [];};
    El.prototype.addEventListener=function(t,f){this.handlers[t]=f;};
    var filled=[];
    global.document={querySelectorAll:function(){return [];},getElementById:function(){return null;}};
    global.window={liveFill:function(r){filled.push(r);}};
    """ + wp._WS_JS + r"""
    var box=new El();
    window.wsTable(box,{days:['a'],dayLabels:['월'],status:['ok'],cur:'원',rows:[
      {c:'005930',nm:'삼성전자',ind:'',href:'lookup/005930',cap:1,chg:[1],wk:1,up:50,sh:1,dcap:1},
      {c:'X',nm:'평문',ind:'',href:'',cap:2,chg:[1],wk:1,up:50,sh:1,dcap:1}]});
    var h=box.kids['.js-ws-body'].innerHTML;
    console.log(JSON.stringify({h:h,filled:filled.length,same:filled[0]===box.kids['.js-ws-body']}));
    """
    js = json.loads(_node(script).strip().splitlines()[-1])
    assert js["h"].count("js-live-pct") == 1 and 'data-t="005930"' in js["h"]
    assert js["filled"] == 1 and js["same"], "다시 그린 표를 바로 채운다"


def test_holdings_page_asks_no_ticker_quotes(monkeypatch):
    """옛 계약(보유종목 News 최신일 카드는 실시간 칸)은 사용자 2026-10-14 "실시간 부하가 크면 기준을 장종료가격으로"
    로 바뀌었다(#222) — 보유종목 News 는 최신일·지난 날 어느 카드도 종목 시세를 묻지 않는다(열어 둔 화면 하나가
    분당 수백 건이었다). 국가별 보드의 실시간 칸은 그대로다(아래 테스트)."""
    import bot.holdings_news as hn
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 10, 13, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    monkeypatch.setattr(hn, "prev_changes", lambda d, now=None: (lambda s: None))
    stock = {"ticker": "005930", "market": "KR", "name": "삼성전자", "value": 1e7, "status": "ok",
             "pros": [], "cons": [], "items": [{"n": 1, "src": "naver", "title": "t", "url": "https://x",
                                                 "summary": "", "ts": "2026-10-13T07:00:00+09:00"}]}
    us = dict(stock, ticker="AAPL", market="US", name="Apple")
    rec = {"date": "2026-10-13", "stocks": [stock, us]}
    page = hn.render_page([rec, dict(rec, date="2026-10-12")], {}, now=now)
    assert "data-t=" not in page and "js-live-pct" not in page and lp.LIVE_JS not in page
    frag = hn._day_body(dict(rec, date="2026-10-12"), now=now)
    assert "js-live-pct" not in frag


def test_api_route_hands_the_ticker_list_to_lookup_and_dispatches(monkeypatch):
    import bot.dashboard_server as ds
    seen = []
    monkeypatch.setattr(lp, "lookup", lambda raw: seen.append(raw) or {"q": {"A": {"pct": 1.0}}, "pending": 0,
                                                                     "capped": False})
    h = ds.DashboardHandler.__new__(ds.DashboardHandler)
    h.path = "/api/live_pct?t=AAPL,005930&t=MSFT"
    got = {}
    h._reply_json = lambda status, body: got.update(body, status=status)
    h._handle_live_pct()
    assert seen == [["AAPL", "005930", "MSFT"]] and got["ok"] and got["q"]["A"]["pct"] == 1.0
    monkeypatch.setattr(lp, "lookup", lambda raw: 1 / 0)
    got.clear()
    h._handle_live_pct()
    assert got["status"] == 500 and got["error"] == "ZeroDivisionError"
    called = []
    h2 = ds.DashboardHandler.__new__(ds.DashboardHandler)
    h2.path = "/api/live_pct?t=A"
    h2._authorize = lambda: True
    h2._handle_live_pct = lambda: called.append(1)
    h2._do_GET_routed()
    assert called == [1], "GET 라우팅이 이 핸들러로 간다"


def test_api_says_fx_is_pending_only_when_the_table_is_empty(monkeypatch):
    """빈 환율 표 — 받는 중(재시작 직후)인지 받기를 마쳤는데 없는지 서버가 말한다(화면이 '미확인' 을 지어내지 않게)."""
    import bot.dashboard_server as ds
    real_pending = lp.fx_pending
    monkeypatch.setattr(lp, "lookup", lambda raw: {"q": {}, "pending": 0, "capped": False})
    got = {}
    h = ds.DashboardHandler.__new__(ds.DashboardHandler)
    h.path = "/api/live_pct?fx=1"
    h._reply_json = lambda status, body: got.update(body, status=status)
    monkeypatch.setattr(lp, "fx_lookup", lambda: {})
    monkeypatch.setattr(lp, "fx_pending", lambda: True)
    h._handle_live_pct()
    assert got["fx"] == {} and got["fx_pending"] is True
    got.clear()
    monkeypatch.setattr(lp, "fx_lookup", lambda: {"USD": {"pct": 1.0}})
    h._handle_live_pct()
    assert "fx_pending" not in got
    monkeypatch.setitem(lp._FX, "running", True)
    assert real_pending() is True
    monkeypatch.setitem(lp._FX, "running", False)
    assert real_pending() is False


def test_world_board_pages_load_the_live_script_before_the_board_script():
    import bot.world_sector_pages as wp
    from bot import world_sectors as ws
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 10, 13, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    h = wp.render_stocks({}, {}, now)
    assert lp.LIVE_JS in h and h.index(lp.LIVE_JS) < h.index(wp._WS_JS)


def test_worker_drains_more_than_one_batch_and_tickers_added_meanwhile(monkeypatch, unpaused):
    """한 묶음(WORKERS*8)보다 많아도, 도는 동안 새로 들어와도 다 받는다."""
    n = lp.WORKERS * 8 * 2 + 3
    table = {f"T{i}": {"pct": 1.0} for i in range(n + 1)}
    calls = []
    _stub_quotes(monkeypatch, table, calls)
    started = []
    monkeypatch.setattr(lp, "_Thread", lambda target=None, **kw: started.append(target) or _Noop())
    lp.lookup([f"T{i}" for i in range(n)], now=0.0)
    real = lp._safe_fetch
    added = []

    def spy(t):
        if not added:
            added.append(1)
            lp.lookup([f"T{n}"], now=0.0)                    # 도는 동안 새 티커
        return real(t)
    monkeypatch.setattr(lp, "_safe_fetch", spy)
    started[0]()
    assert len(started) == 1 and len({t for _, t in calls}) == n + 1
    assert lp._RUNNING is False


def test_worker_crash_releases_the_running_flag(monkeypatch):
    monkeypatch.setattr(lp, "_Thread", _SyncThread)

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("pool")
    import concurrent.futures as cf
    monkeypatch.setattr(cf, "ThreadPoolExecutor", Boom)
    lp.lookup(["AAPL"], now=0.0)
    assert lp._RUNNING is False, "그대로 두면 이 프로세스에서 영영 안 받는다"


def test_one_failing_ticker_does_not_sink_the_batch(monkeypatch, unpaused):
    def f(t):
        if t == "BAD":
            raise ValueError("x")
        return {"pct": 1.0}
    import bot.world_quote as wq
    monkeypatch.setattr(wq, "fetch_world_quote", f)
    monkeypatch.setattr(lp, "_Thread", _SyncThread)
    lp.lookup(["BAD", "GOOD"], now=0.0)
    r = lp.lookup(["BAD", "GOOD"], now=time_after(lp, 0))
    assert r["q"]["GOOD"]["pct"] == 1.0 and "BAD" not in r["q"]


@pytest.mark.skipif(not _NODE, reason="node 없음")
def test_live_js_asks_only_visible_cells_and_refills_after_a_busy_redraw():
    script = _DOM + r"""
    var a=new El({'data-t':'AAPL'}); a.offsetParent={};
    var h=new El({'data-t':'HIDDEN'}); h.offsetParent=null;
    var urls=[], timers=[], resolve=null;
    global.document={readyState:'complete',querySelectorAll:function(){return [a,h];}};
    global.window={};
    global.setTimeout=function(f,ms){timers.push([f,ms]);return timers.length;}; global.clearTimeout=function(){};
    global.fetch=function(u){urls.push(u);return new Promise(function(r){resolve=r;});};
    """ + lp.LIVE_JS + r"""
    window.liveFill(document);                // 요청이 도는 동안 다시 그림
    timers[timers.length-1][0]();              // 300ms 타이머 — busy 라 미뤄진다
    var n1=urls.length, t0=timers.length;
    resolve({ok:true,json:function(){return {q:{},pending:0};}});
    Promise.resolve().then(function(){}).then(function(){}).then(function(){}).then(function(){})
      .then(function(){
      var after=timers.slice(t0).map(function(x){return x[1];});
      console.log(JSON.stringify({url:urls[0],n1:n1,after:after}));});
    """
    js = json.loads(_node(script).strip().splitlines()[-1])
    assert js["url"] == "api/live_pct?t=AAPL", "숨은 칸은 묻지 않는다"
    assert js["n1"] == 1 and js["after"][-1] == 300, ("도는 동안 다시 그린 표는 끝나자마자 다시 묻는다", js)


@pytest.mark.skipif(not _NODE, reason="node 없음")
def test_live_js_reads_twse_date_only_ts():
    script = _DOM + r"""
    var a=new El({'data-t':'2330.TW'});
    global.document={readyState:'complete',querySelectorAll:function(){return [a];}};
    global.window={}; global.setTimeout=function(){return 0;}; global.clearTimeout=function(){};
    global.fetch=function(){return Promise.resolve({ok:true,json:function(){
      return {q:{'2330.TW':{pct:1,src:'TWSE',ts:'20261013'}},pending:0};}});};
    """ + lp.LIVE_JS + r"""
    Promise.resolve().then(function(){}).then(function(){}).then(function(){}).then(function(){
      console.log(JSON.stringify({t:a.attrs.title}));});
    """
    js = json.loads(_node(script).strip().splitlines()[-1])
    assert "기준 10-13 거래일" in js["t"], js


# ── 원화 환율 전일대비(보유종목 News 해외 원화 손익, 사용자 2026-10-14) ─────────────────────────────────
@pytest.fixture
def fx_fresh(monkeypatch):
    monkeypatch.setattr(lp, "_FX", {"at": 0.0, "val": {}, "running": False})


def test_fx_pct_map_reads_only_krw_pairs_and_drops_junk():
    tbl = {"FX_USDKRW": {"pct": -0.31}, "FX_JPYKRW": {"pct": "0.5"}, "FX_EURUSD": {"pct": 1.0},
           "FX_HKDKRW": {"pct": float("nan")}, "FX_TWDKRW": {"pct": 99.0}, "FX_CNYKRW": {"pct": None},
           "FX_GBPKRW": "x"}
    assert lp.fx_pct_map(tbl) == {"USD": -0.31, "JPY": 0.5}
    assert lp.fx_pct_map(None) == {} and lp.fx_pct_map([1]) == {}


def test_fx_changes_drops_a_stale_pool(monkeypatch):
    import bot.naver_marketindex as nm
    monkeypatch.setattr(nm, "fetch_kr_fx", lambda: {"FX_USDKRW": {"pct": 0.4}})
    monkeypatch.setattr(nm, "value_age_sec", lambda kind: 60.0)
    assert lp.fx_changes() == {"USD": 0.4}
    monkeypatch.setattr(nm, "value_age_sec", lambda kind: lp.FX_MAX_AGE_SEC + 1)
    assert lp.fx_changes() == {}                     # 24h 낡은 값 풀은 그날 환율 등락이 아닐 수 있다
    monkeypatch.setattr(nm, "fetch_kr_fx", lambda: 1 / 0)
    assert lp.fx_changes() == {}


def test_fx_lookup_never_fetches_on_the_request_path_and_refreshes_behind(monkeypatch, fx_fresh):
    started = []
    monkeypatch.setattr(lp, "_Thread", lambda target=None, **kw: started.append(target) or _Noop())
    assert lp.fx_lookup(now=1000.0) == {} and len(started) == 1
    assert lp.fx_lookup(now=1001.0) == {} and len(started) == 1        # 돌고 있으면 또 띄우지 않는다
    monkeypatch.setattr(lp, "fx_changes", lambda **kw: {"USD": 0.25})
    monkeypatch.setattr(lp.time, "time", lambda: 1002.0)
    started[0]()                                                        # 뒤에서 받은 값
    assert lp.fx_lookup(now=1010.0, kick=False) == {"USD": {"pct": 0.25, "age": 8}}
    monkeypatch.setattr(lp, "fx_changes", lambda **kw: {})               # 실패는 직전 값을 지우지 않는다
    monkeypatch.setattr(lp.time, "time", lambda: 1100.0)
    lp._fx_worker()
    assert lp.fx_lookup(now=1100.0, kick=False)["USD"]["pct"] == 0.25
    assert lp.fx_lookup(now=1002.0 + lp.FX_MAX_AGE_SEC + 1, kick=False) == {}   # 너무 낡으면 싣지 않는다


def test_fx_lookup_thread_start_failure_releases(monkeypatch, fx_fresh):
    class Boom:
        def __init__(self, **kw):
            pass

        def start(self):
            raise RuntimeError("x")
    monkeypatch.setattr(lp, "_Thread", Boom)
    lp.fx_lookup(now=1.0)
    assert lp._FX["running"] is False


def test_api_route_adds_fx_only_when_asked(monkeypatch):
    import bot.dashboard_server as ds
    monkeypatch.setattr(lp, "lookup", lambda raw: {"q": {}, "pending": 0, "capped": False})
    monkeypatch.setattr(lp, "fx_lookup", lambda: {"USD": {"pct": 0.1, "age": 1}})
    h = ds.DashboardHandler.__new__(ds.DashboardHandler)
    got = {}
    h._reply_json = lambda status, body: got.update(body, status=status)
    h.path = "/api/live_pct?t=AAPL"
    h._handle_live_pct()
    assert "fx" not in got
    h.path = "/api/live_pct?t=AAPL&fx=1"
    h._handle_live_pct()
    assert got["fx"] == {"USD": {"pct": 0.1, "age": 1}}
