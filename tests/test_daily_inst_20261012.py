"""Daily 기관매수 보드(사용자 2026-10-12 — 시총 3구간 × 「순매수 강도 · 전환 ·
5일 연속」 TOP 10 을 매일 같은 모양으로, 화면은 일별 업데이트 보드와 같은 형식).

수집기는 원천(pykrx)을 가짜로 바꿔 **통째로** 태운다 — 헬퍼만 재면 배선을 떼는
변형을 못 잡는다(#20). 달력도 합성으로 갈아 끼운다(운영 달력 데이터 판에 기대면
휴장일 표가 바뀌는 날 무관한 빨간불이 난다)."""
import json
import os
import subprocess
from datetime import datetime, timedelta

import pandas as pd
import pytest

from bot import daily_inst as di
from bot import kr_session

K = di._KST

# 합성 거래일 — 10/03(토)·10/04(일)·10/05(월, 휴장으로 친다)·10/09(금) 은 거래일이 아니다.
SESS = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30",
        "2026-10-01", "2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08",
        "2026-10-12", "2026-10-13"]


@pytest.fixture
def cal(monkeypatch):
    from bot import market_calendar as mc

    def last_on_or_before(market, d):
        c = [s for s in SESS if s <= d]
        return c[-1] if c else None

    def between(market, a, b):
        return [s for s in SESS if a <= s <= b]

    def behind(market, have, exp):
        if have >= exp:
            return 0
        return len([s for s in SESS if have < s <= exp])

    monkeypatch.setattr(mc, "last_session_on_or_before", last_on_or_before)
    monkeypatch.setattr(mc, "sessions_between", between)
    monkeypatch.setattr(mc, "sessions_behind", behind)
    return mc


@pytest.fixture
def store(monkeypatch, tmp_path):
    base = tmp_path / "daily_inst"
    monkeypatch.setattr(di, "_DIR", base)
    monkeypatch.setattr(di, "_REC_DIR", base / "records")
    monkeypatch.setattr(di, "_RAW_DIR", base / "raw")
    monkeypatch.setattr(di, "_STATE", base / "state.json")
    return base


class FakeKRX:
    """pykrx.stock 대역 — `nets[날짜][시장] = {코드: (이름, 원)}`, `ready` 에 없는
    날짜는 '아직 집계 전'(빈 표)으로 답한다."""

    def __init__(self, nets, mcap, ready=None):
        self.nets, self.mcap, self.ready = nets, mcap, ready
        self.calls = []

    def get_market_net_purchases_of_equities(self, a, b, mkt, inv):
        self.calls.append(("net", a, mkt, inv))
        d = f"{a[:4]}-{a[4:6]}-{a[6:]}"
        if self.ready is not None and d not in self.ready:
            return pd.DataFrame()
        rows = (self.nets.get(d) or {}).get(mkt, {})
        return pd.DataFrame({"종목명": [v[0] for v in rows.values()],
                             "매도거래대금": [0] * len(rows),
                             "순매수거래대금": [v[1] for v in rows.values()]},
                            index=list(rows))

    def get_market_cap_by_ticker(self, d, market="ALL"):
        self.calls.append(("cap", d, market))
        return pd.DataFrame({"종가": [1] * len(self.mcap),
                             "시가총액": list(self.mcap.values())},
                            index=list(self.mcap))


MCAP = {"005930": 4e14, "000660": 1e14, "111111": 6e11, "222222": 3e11,
        "333333": 8e11}


def _day(samsung=5e10, hynix=-1e9, a=3e8, b=1e8, c=-5e7):
    return {"KOSPI": {"005930": ("삼성전자", samsung), "000660": ("SK하이닉스", hynix),
                      "111111": ("소형A", a)},
            "KOSDAQ": {"222222": ("코닥B", b), "333333": ("코닥C", c)}}


@pytest.fixture
def krx(monkeypatch):
    def make(nets, mcap=MCAP, ready=None):
        f = FakeKRX(nets, mcap, ready)
        monkeypatch.setattr(di, "_pykrx_stock", lambda: f)
        import bot.pykrx_client as pc
        monkeypatch.setattr(pc, "krx_login_ready", lambda: True)
        return f
    return make


# ── 경계 ────────────────────────────────────────────────────────────────────
def test_settled_session_waits_for_after_market_end_plus_margin(cal):
    """그날은 애프터마켓 종료(표에서 파생, 20:00) + 30분 뒤부터 요구한다 —
    정규장 경계(`last_closed_session`)는 그대로다(#38 같은 규칙, 경계만 다르다)."""
    t = lambda h, m: datetime(2026, 10, 8, h, m, tzinfo=K)   # noqa: E731
    assert kr_session.day_end("KRX") == (20, 0)
    assert kr_session.last_settled_session(t(20, 29)) == "2026-10-07"
    assert kr_session.last_settled_session(t(20, 30)) == "2026-10-08"
    assert kr_session.last_closed_session(t(15, 29)) == "2026-10-07"
    assert kr_session.last_closed_session(t(15, 30)) == "2026-10-08"
    # 휴장일(10/09)엔 직전 거래일
    assert kr_session.last_settled_session(datetime(2026, 10, 9, 10, 0, tzinfo=K)) == "2026-10-08"
    assert di.boundary_hm() == (20, 30)


# ── 순수 계산 ────────────────────────────────────────────────────────────────
def _nets(d):
    out = {}
    for mkt, sfx in di.MARKETS:
        for code, (nm, v) in d[mkt].items():
            out[f"{code}{sfx}"] = [nm, v]
    return out


def test_build_record_tiers_lists_and_values():
    sessions = SESS[:10]   # ... 10-08 까지
    nbd = {d: _nets(_day()) for d in sessions[-5:]}
    # 하이닉스: 직전일 순매도 → 기준일 순매수 = 전환. 연속은 아니다.
    nbd["2026-10-08"] = _nets(_day(hynix=2e9))
    # 코닥C: 직전일 0 → 기준일 순매수 = '순매도 → 순매수' 가 아니므로 전환 아님
    nbd["2026-10-07"]["333333.KQ"][1] = 0
    nbd["2026-10-08"]["333333.KQ"][1] = 2e8   # 0.025% — 소형A(0.05%) 다음
    rec = di.build_record("2026-10-08", sessions, nbd, MCAP)
    tiers = {t["key"]: t for t in rec["tiers"]}
    big = tiers["big"]["lists"]
    assert [r["n"] for r in big["strength"]] == ["삼성전자", "SK하이닉스"]
    assert [r["n"] for r in big["turn"]] == ["SK하이닉스"]
    assert [r["n"] for r in big["streak"]] == ["삼성전자"]
    # 값 = 기준일 순매수 ÷ 기준일 시총 × 100 — 목록이 달라도 같은 값
    assert big["streak"][0]["r"] == pytest.approx(5e10 / 4e14 * 100)
    assert big["strength"][0]["r"] == big["streak"][0]["r"]
    mid = tiers["mid"]["lists"]
    assert [r["n"] for r in mid["strength"]] == ["소형A", "코닥C"]
    assert mid["turn"] == []                       # 직전일 0 은 전환이 아니다
    assert [r["n"] for r in mid["streak"]] == ["소형A"]
    assert tiers["small"]["n"] == 1 and tiers["mid"]["n"] == 2
    assert rec["window"] == sessions[-5:] and rec["prev"] == "2026-10-07"


def test_tier_boundaries_are_lower_inclusive():
    sessions = ["2026-10-08"]
    nets = {"2026-10-08": {"000001.KS": ["딱1조", 1.0], "000002.KS": ["딱5천억", 1.0],
                           "000003.KS": ["5천억미만", 1.0]}}
    rec = di.build_record("2026-10-08", sessions, nets,
                          {"000001": 1e12, "000002": 5e11, "000003": 5e11 - 1})
    got = {t["key"]: [r["n"] for r in t["lists"]["strength"]] for t in rec["tiers"]}
    assert got == {"big": ["딱1조"], "mid": ["딱5천억"], "small": ["5천억미만"]}


def test_top_n_cut_and_order_ties_by_amount():
    nets = {"2026-10-08": {f"{i:06d}.KS": [f"S{i}", 1e8 * (i % 3 + 1)] for i in range(1, 16)}}
    mcap = {f"{i:06d}": 1e12 * (i % 3 + 1) for i in range(1, 16)}   # 비율 전부 0.01%
    rec = di.build_record("2026-10-08", ["2026-10-08"], nets, mcap)
    top = rec["tiers"][0]["lists"]["strength"]
    assert len(top) == di.TOP_N
    # 비율이 같으면 금액이 큰 쪽이 먼저
    nets_order = [r["net"] for r in top]
    assert nets_order == sorted(nets_order, reverse=True)


def test_missing_prior_days_make_lists_unjudged_not_empty():
    """앞 거래일 자료가 없으면 전환·연속은 **None**(판정 불가) — 빈 목록은 '해당
    종목 없음' 으로 읽힌다(#43·#54)."""
    sessions = SESS[:10]
    nbd = {"2026-10-08": _nets(_day())}
    rec = di.build_record("2026-10-08", sessions, nbd, MCAP)
    lists = rec["tiers"][0]["lists"]
    assert lists["turn"] is None and lists["streak"] is None
    assert lists["strength"]
    assert len(rec["streak_missing"]) == 4


def test_mcap_missing_tickers_are_excluded_and_counted():
    rec = di.build_record("2026-10-08", ["2026-10-08"], {"2026-10-08": _nets(_day())},
                          {k: v for k, v in MCAP.items() if k != "111111"})
    assert rec["mcap_missing"] == 1 and rec["universe"] == 4


# ── 원천 ────────────────────────────────────────────────────────────────────
def test_fetch_nets_suffixes_and_failure_branches(krx):
    f = krx({"2026-10-08": _day()})
    nets, why = di.fetch_nets("2026-10-08")
    assert why == "" and nets["005930.KS"] == ["삼성전자", 5e10]
    assert nets["222222.KQ"][0] == "코닥B"
    assert {c[3] for c in f.calls} == {"기관합계"}
    # 한 시장이라도 비면 통째로 실패(반쪽을 굽지 않는다, #280)
    krx({"2026-10-08": {"KOSPI": _day()["KOSPI"], "KOSDAQ": {}}})
    nets, why = di.fetch_nets("2026-10-08")
    assert nets is None and "KOSDAQ" in why and "비었다" in why
    # 전 종목 0 = 집계 전
    zero = {m: {k: (n, 0) for k, (n, _v) in rows.items()} for m, rows in _day().items()}
    krx({"2026-10-08": zero})
    nets, why = di.fetch_nets("2026-10-08")
    assert nets is None and "집계 전" in why


def test_fetch_mcap_all_zero_is_failure(krx):
    krx({}, mcap={"005930": 0, "000660": 0})
    m, why = di.fetch_mcap("2026-10-08")
    assert m is None and "0" in why


# ── 수집 E2E ────────────────────────────────────────────────────────────────
def test_tick_backfills_past_days_even_when_today_is_not_ready(cal, store, krx):
    nets = {d: _day() for d in SESS}
    f = krx(nets, ready=set(SESS[:10]))          # 10-12 은 아직 집계 전
    now = datetime(2026, 10, 12, 21, 0, tzinfo=K)
    res = di.tick(now=now, budget=100)
    assert res["status"] == "fail" and "2026-10-12" in res["reason"]
    assert "2026-10-08" in res["built"]
    assert not (store / "raw" / "2026-10-12.json").exists(), "집계 전 날을 구웠다"
    assert not di.load_record("2026-10-12")
    st = di.load_state()
    assert st["fails"] == 1 and st["next_try"]
    # 백오프 안에는 원천을 안 부른다
    n = len(f.calls)
    assert di.tick(now=now + timedelta(minutes=5))["status"] == "backoff"
    assert len(f.calls) == n
    # 집계가 나오면 받는다 — 앞날 원자료는 캐시를 쓴다(그날 2콜 + 시총 1콜)
    f.ready = set(SESS)
    n = len(f.calls)
    res = di.tick(now=now + timedelta(minutes=20), budget=100)
    assert res["status"] == "ok" and res["built"] == ["2026-10-12"]
    assert len(f.calls) - n == 3
    st = di.load_state()
    assert st["fails"] == 0 and not st.get("next_try") and st["last_ok"]
    assert len(di.load_records()) == di.KEEP_SESSIONS


def test_past_day_failure_stops_the_walk(cal, store, krx):
    """지난 거래일이 실패하면 원천 장애로 보고 더 걷지 않는다 — 기준일(집계 전일 수
    있다)만 예외다. 실패한 날 앞의 날까지 계속 두드리면 같은 실패에 호출만 쓴다(#279)."""
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06"})
    res = di.tick(now=datetime(2026, 10, 12, 21, 0, tzinfo=K), budget=100)
    assert res["status"] == "fail" and "2026-10-06" in res["reason"]
    # 기준일(10-12) 기록은 앞날 10-06 원자료가 없어 못 만든다 — '기준일 집계 전' 이
    # 아니므로 더 오래된 날로 넘어가지 않는다
    assert res["built"] == []
    assert not [c for c in f.calls if c[1] < "20261002"], "실패 뒤에도 더 걸었다"
    assert not di.load_record("2026-10-12")


def test_budget_exhaustion_is_not_a_failure(cal, store, krx):
    krx({d: _day() for d in SESS})
    now = datetime(2026, 10, 12, 21, 0, tzinfo=K)
    res = di.tick(now=now, budget=5)
    assert res["status"] == "budget" and res["calls"] <= 5
    assert not di.load_state().get("next_try"), "예산 소진에 백오프를 걸었다"
    res = di.tick(now=now + timedelta(minutes=15), budget=100)
    assert res["status"] == "ok"


def test_partial_mcap_is_not_baked(cal, store, krx):
    krx({d: _day() for d in SESS}, mcap={"005930": 4e14})   # 5종목 중 1종목만 시총
    res = di.tick(now=datetime(2026, 10, 12, 21, 0, tzinfo=K), budget=100)
    assert res["status"] == "fail" and "반쪽" in res["reason"]
    assert not di.load_records()


def test_next_morning_recheck_records_the_difference(cal, store, krx):
    nets = {d: _day() for d in SESS}
    f = krx(nets)
    evening = datetime(2026, 10, 12, 20, 40, tzinfo=K)
    assert di.tick(now=evening, budget=100)["status"] == "ok"
    rec = di.load_record("2026-10-12")
    assert rec["recheck"] is None and di.needs_recheck(rec, evening + timedelta(hours=2)) is False
    # 밤사이 한 종목 값이 바뀌었다
    f.nets["2026-10-12"] = _day(samsung=6e10)
    morning = datetime(2026, 10, 13, 7, 5, tzinfo=K)
    assert di.needs_recheck(rec, morning)
    res = di.tick(now=morning, budget=100)
    assert res["rechecked"] == ["2026-10-12"]
    rec = di.load_record("2026-10-12")
    assert rec["recheck"]["changed"] == 1 and rec["recheck"]["max_abs_won"] == int(1e10)
    assert rec["tiers"][0]["lists"]["strength"][0]["net"] == int(6e10), "표가 재확인 값으로 안 바뀜"
    assert rec["collected_at"].startswith("2026-10-12T20:40")
    # 한 번만 — 다음 주기엔 안 묻는다
    n = len(f.calls)
    di.tick(now=morning + timedelta(minutes=15), budget=100)
    assert not [c for c in f.calls[n:] if c[1] == "20261012"]


def test_recheck_without_evening_raw_says_it_could_not_compare(cal, store, krx):
    """저녁 원자료가 없으면 대조할 수 없다 — 전 종목을 '바뀜' 으로 세면 거짓이다(#54)."""
    krx({d: _day() for d in SESS})
    assert di.tick(now=datetime(2026, 10, 12, 20, 40, tzinfo=K), budget=100)["status"] == "ok"
    (store / "raw" / "2026-10-12.json").unlink()
    res = di.tick(now=datetime(2026, 10, 13, 7, 5, tzinfo=K), budget=100)
    assert res["rechecked"] == ["2026-10-12"]
    rec = di.load_record("2026-10-12")
    assert rec["recheck"]["changed"] is None
    assert "저녁 원자료가 없어 대조 못 함" in di._day_meta(rec)


def test_backfilled_record_needs_no_recheck():
    rec = {"date": "2026-10-08", "collected_at": "2026-10-12T21:00:00+09:00", "recheck": None}
    assert di.needs_recheck(rec, datetime(2026, 10, 12, 22, 0, tzinfo=K)) is False


def test_missing_credentials_backs_off_without_calling_source(cal, store, monkeypatch):
    import bot.pykrx_client as pc
    monkeypatch.setattr(pc, "krx_login_ready", lambda: False)
    monkeypatch.setattr(di, "_pykrx_stock", lambda: (_ for _ in ()).throw(AssertionError("불렀다")))
    res = di.tick(now=datetime(2026, 10, 12, 21, 0, tzinfo=K))
    assert res["status"] == "fail" and "KRX_ID" in res["reason"]
    assert di.load_state()["next_try"]


def test_corrupt_files_never_raise(store):
    (store / "records").mkdir(parents=True)
    (store / "records" / "2026-10-08.json").write_bytes(b"\x8d\xff{")
    store.joinpath("state.json").write_bytes(b"\x00")
    assert di.load_records() == [] and di.load_state() == {}


# ── 판정 ────────────────────────────────────────────────────────────────────
def test_status_verdict_branches(cal):
    t = lambda d, h, m: datetime.fromisoformat(d).replace(hour=h, minute=m, tzinfo=K)  # noqa: E731
    assert di.status_verdict("2026-10-08", t("2026-10-12", 15, 0))[0] == "ok"
    assert di.status_verdict("2026-10-08", t("2026-10-12", 21, 0))[0] == "wait"
    kind, line = di.status_verdict("2026-10-08", t("2026-10-12", 21, 31),
                                   {"reason": "X 실패", "next_try": "2026-10-12T22:00:00+09:00"})
    assert kind == "behind" and "X 실패" in line and "10-12 22:00" in line
    assert di.status_verdict("2026-10-06", t("2026-10-12", 15, 0))[0] == "behind"
    assert di.status_verdict(None, t("2026-10-12", 15, 0))[0] == "none"


def test_status_unknown_without_calendar(monkeypatch):
    monkeypatch.setattr(di, "settled_session", lambda now=None: None)
    assert di.status_verdict("2026-10-08")[0] == "unknown"


# ── 화면 ────────────────────────────────────────────────────────────────────
def _records():
    sessions = SESS[:10]
    nbd = {d: _nets(_day()) for d in sessions}
    nbd["2026-10-08"] = _nets(_day(hynix=2e9))
    r1 = di.build_record("2026-10-08", sessions, nbd, MCAP,
                         collected_at="2026-10-08T20:35:00+09:00")
    r1["recheck"] = {"at": "2026-10-09T07:02:00+09:00", "changed": 0,
                     "compared": 5, "max_abs_won": 0}
    r0 = di.build_record("2026-09-30", sessions, {"2026-09-30": _nets(_day())}, MCAP,
                         collected_at="2026-10-08T21:00:00+09:00")
    return [r0, r1]


def test_render_shows_the_three_by_three_and_reasons(cal):
    html = di.render_page(_records(), {}, datetime(2026, 10, 12, 10, 0, tzinfo=K))
    i = html.index("2026-10-08 (목)")
    day = html[html.rindex("<details class='di-day'", 0, i): html.index("</details>", i)]
    assert day.startswith("<details class='di-day' data-open='1' open>")
    assert day.count("<table class='di-tbl'>") == 9
    for _k, title in di.LISTS:
        assert title in day
    for _k, label, *_ in di.TIERS:
        assert label in day
    assert "href='lookup/005930.KS'" in day and "href='lookup/222222.KQ'" in day
    assert "재확인 10-09 07:02 · 바뀐 종목 0" in day
    # 9/30 은 앞 거래일 자료 없이 만든 기록 — 판정 불가 사유가 보인다
    j = html.index("2026-09-30 (수)")
    # 그 **날** 블록만 자른다 — 앞 200자 창은 9월 '월' 블록의 data-open='0' 이 대신
    # 만족시켜 '모든 날 펼침' 변형이 통과했다(#75 · 뮤테이션 실측).
    old = html[html.rindex("<details class='di-day'", 0, j): html.index("</details>", j)]
    assert old.startswith("<details class='di-day' data-open='0'>")
    assert "직전 거래일(2026-09-29) 자료가 없어" in old
    assert "최근 5거래일 중 4일" in old
    # 값은 두 자리 % — 원문 표와 같은 표기
    assert "0.01%" in day


def test_render_status_and_empty_page(cal):
    html = di.render_page([], {}, datetime(2026, 10, 12, 10, 0, tzinfo=K))
    assert "아직 기록이 없습니다" in html and "20:30" in html


def test_regenerate_writes_the_page(monkeypatch, tmp_path, store, cal):
    from bot import dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path)
    di.regenerate()
    out = tmp_path / "daily_inst.html"
    assert out.exists() and "Daily 기관매수" in out.read_text(encoding="utf-8")


_DOM = r"""
function El(tag, attrs, kids, text){
  this.tagName=tag; this.attrs=attrs||{}; this.kids=kids||[]; this._t=text||'';
  this.style={display:''}; this.open=this.attrs['data-open']==='1';
  var self=this; this._cls=(this.attrs['class']||'').split(' ').filter(Boolean);
  this.classList={add:function(c){if(self._cls.indexOf(c)<0)self._cls.push(c);},
    remove:function(c){self._cls=self._cls.filter(function(x){return x!==c;});},
    contains:function(c){return self._cls.indexOf(c)>=0;}};
}
El.prototype.getAttribute=function(k){return this.attrs[k]==null?null:this.attrs[k];};
El.prototype.all=function(){var o=[];this.kids.forEach(function(k){o.push(k);o=o.concat(k.all());});return o;};
El.prototype.querySelectorAll=function(sel){var p=sel.split('.');return this.all().filter(function(e){
  return e.tagName===p[0]&&e.classList.contains(p[1]);});};
Object.defineProperty(El.prototype,'textContent',{get:function(){
  return this._t+this.kids.map(function(k){return k.textContent;}).join('');},
  set:function(v){this._t=v;this.kids=[];}});
function cell(n){return new El('td',{'class':'di-nm'},[],n);}
function day(open,names){return new El('details',{'class':'di-day','data-open':open},names.map(cell));}
var d1=day('1',['스피어','삼성전자']), d2=day('0',['LS에코에너지']), d3=day('0',['스피어']);
var m1=new El('details',{'class':'di-month','data-open':'1'},[d1,d2]);
var m2=new El('details',{'class':'di-month','data-open':'0'},[d3]);
var root=new El('root',{},[m1,m2]);
var q={value:'',_h:null,addEventListener:function(e,h){this._h=h;}};
var st={textContent:'총 3거래일 기록'};
var document={getElementById:function(id){return id==='di-q'?q:id==='di-st'?st:null;},
  querySelectorAll:function(s){return root.querySelectorAll(s);}};
"""


def test_search_js_runs_and_filters_days(tmp_path):
    """검색 JS 를 **실행**한다 — 파서만 태우면 동작 결함을 못 본다(#253)."""
    js = _DOM + di._DI_JS + r"""
q.value='스피어'; q._h();
var out={d1:[d1.style.display,d1.open], d2:d2.style.display, d3:[d3.style.display,d3.open],
  m2:[m2.style.display,m2.open], st:st.textContent,
  hit:d1.kids[0].classList.contains('di-hit'), miss:d1.kids[1].classList.contains('di-hit')};
q.value=''; q._h();
out.reset=[d2.style.display, d1.open, d3.open, m2.open, st.textContent,
  d1.kids[0].classList.contains('di-hit')];
console.log(JSON.stringify(out));
"""
    f = tmp_path / "t.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["d1"] == ["", True] and out["d2"] == "none" and out["d3"] == ["", True]
    assert out["m2"] == ["", True] and out["st"] == '"스피어" — 2일에 등장'
    assert out["hit"] is True and out["miss"] is False
    assert out["reset"] == ["", True, False, False, "총 3거래일 기록", False]


# ── 배선 ────────────────────────────────────────────────────────────────────
def _help_text():
    import ast
    src = open("bot/telegram_bot.py", encoding="utf-8").read()
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "_HELP_TEXT" for t in node.targets)):
            return ast.literal_eval(node.value)
    raise AssertionError("_HELP_TEXT 없음")


def test_help_names_the_board_with_the_real_boundary_time():
    text = _help_text()
    bh, bm = di.boundary_hm()
    i = text.index("🏦Daily 기관매수")
    assert f"{bh:02d}:{bm:02d}" in text[i:i + 80]
    assert len(text.encode("utf-16-le")) // 2 < 4096


def test_periodic_task_is_registered():
    import ast
    src = open("bot/telegram_bot.py", encoding="utf-8").read()
    tree = ast.parse(src)
    created = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "create_task"
               and n.args and isinstance(n.args[0], ast.Call)
               and getattr(n.args[0].func, "id", "") == "_periodic_daily_inst"]
    assert len(created) == 1
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "_periodic_daily_inst")
    names = {a.name for n in ast.walk(fn) if isinstance(n, ast.ImportFrom)
             and n.module == "bot.daily_inst" for a in n.names}
    assert {"regenerate", "tick_and_render"} <= names


def test_nav_links_the_board_from_home_noah_and_daily_byte():
    from bot import dashboard as d
    src = open("bot/dashboard.py", encoding="utf-8").read()
    assert src.count('href="daily_inst.html">🏦 Daily 기관매수</a>') >= 3
    html = d._render_daily_byte_page([])
    assert 'href="daily_inst.html"' in html


def test_board_audit_line_uses_the_screen_verdict(cal):
    from bot.scripts.board_audit import _daily_inst_line
    now = datetime(2026, 10, 12, 10, 0, tzinfo=K)
    recs = _records()
    assert _daily_inst_line(recs, {}, now).lstrip().startswith("✅")
    assert _daily_inst_line([], {}, now).lstrip().startswith("❌")
    late = datetime(2026, 10, 13, 9, 0, tzinfo=K)
    line = _daily_inst_line(recs, {"reason": "KRX 응답 없음"}, late)
    assert line.lstrip().startswith("❌") and "KRX 응답 없음" in line


def test_why_is_read_only(cal, store, monkeypatch, capsys):
    """진단은 아무것도 받지도 쓰지도 않는다(#264·#283)."""
    monkeypatch.setattr(di, "_pykrx_stock", lambda: (_ for _ in ()).throw(AssertionError("받았다")))
    di._write_json(di.record_path("2026-10-08"), _records()[1])
    before = sorted(p.name for p in store.rglob("*"))
    rc = di.why()
    out = capsys.readouterr().out
    assert sorted(p.name for p in store.rglob("*")) == before
    assert "최신 2026-10-08" in out and "판정:" in out and rc in (0, 1)
