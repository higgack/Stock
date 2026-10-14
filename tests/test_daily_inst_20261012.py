"""Daily 기관매수 보드(사용자 2026-10-12 — 시총 3구간 × 「순매수 강도 · 전환 ·
5일 연속」 TOP 10 을 매일 같은 모양으로, 화면은 일별 업데이트 보드와 같은 형식).

수집기는 원천(pykrx)을 가짜로 바꿔 **통째로** 태운다 — 헬퍼만 재면 배선을 떼는
변형을 못 잡는다(#20). 달력도 합성으로 갈아 끼운다(운영 달력 데이터 판에 기대면
휴장일 표가 바뀌는 날 무관한 빨간불이 난다). 독립 리뷰(2026-10-12)가 찾은 생존
뮤테이션 자리마다 발화 테스트를 둔다(#91·#291)."""
import ast
import json
import shutil
import subprocess
from datetime import date as _date
from datetime import datetime, timedelta

import pandas as pd
import pytest

from bot import daily_inst as di
from bot import kr_session

K = di._KST
_HOLIDAYS = {"2026-10-05", "2026-10-09"}   # 합성 달력의 휴장일


def _gen_sessions():
    d, out = _date(2026, 8, 24), []
    while d <= _date(2026, 10, 20):
        s = d.isoformat()
        if d.weekday() < 5 and s not in _HOLIDAYS:
            out.append(s)
        d += timedelta(days=1)
    return out


SESS = _gen_sessions()


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


MCAP = {"005930": 4e14, "000660": 1e14, "111111": 6e11, "222222": 3e11,
        "333333": 8e11}


class FakeKRX:
    """pykrx.stock 대역. `ready` 에 없는 날짜의 순매수는 빈 표(집계 전·자료 없음).
    `mcap_mode[날짜]` = ok(기본) · holiday(행은 있는데 전부 0) · empty(빈 응답) ·
    partial(첫 종목만 — 커버리지 미달). `boom` 에 든 날은 순매수 조회가 예외."""

    def __init__(self, nets, mcap, ready=None, mcap_mode=None):
        self.nets, self.mcap, self.ready = nets, mcap, ready
        self.mcap_mode = mcap_mode or {}
        self.boom = set()           # 순매수 조회가 예외를 던지는 날
        self.calls = []

    @staticmethod
    def _d(a):
        return f"{a[:4]}-{a[4:6]}-{a[6:]}"

    def get_market_net_purchases_of_equities(self, a, b, mkt, inv):
        self.calls.append(("net", a, mkt, inv))
        d = self._d(a)
        if d in self.boom:
            raise ConnectionError("read timed out")
        if self.ready is not None and d not in self.ready:
            return pd.DataFrame()
        rows = (self.nets.get(d) or {}).get(mkt, {})
        return pd.DataFrame({"종목명": [v[0] for v in rows.values()],
                             "매도거래대금": [0] * len(rows),
                             "순매수거래대금": [v[1] for v in rows.values()]},
                            index=list(rows))

    def get_market_cap_by_ticker(self, d, market="ALL"):
        self.calls.append(("cap", d, market))
        mode = self.mcap_mode.get(self._d(d), "ok")
        if mode == "empty":
            return pd.DataFrame()
        mc = dict(list(self.mcap.items())[:1]) if mode == "partial" else self.mcap
        vals = list(mc.values()) if mode in ("ok", "partial") else [0] * len(mc)
        return pd.DataFrame({"종가": [1] * len(mc), "시가총액": vals}, index=list(mc))


def _day(samsung=5e10, hynix=-1e9, a=3e8, b=1e8, c=-5e7):
    return {"KOSPI": {"005930": ("삼성전자", samsung), "000660": ("SK하이닉스", hynix),
                      "111111": ("소형A", a)},
            "KOSDAQ": {"222222": ("코닥B", b), "333333": ("코닥C", c)}}


@pytest.fixture
def krx(monkeypatch):
    def make(nets, mcap=MCAP, ready=None, mcap_mode=None):
        f = FakeKRX(nets, mcap, ready, mcap_mode)
        monkeypatch.setattr(di, "_pykrx_stock", lambda: f)
        import bot.pykrx_client as pc
        monkeypatch.setattr(pc, "krx_login_ready", lambda: True)
        return f
    return make


def _at(d, h, m):
    return datetime.fromisoformat(d).replace(hour=h, minute=m, tzinfo=K)


def _want(target):
    return di.recent_sessions(target, 10)


# ── 경계·시각 ───────────────────────────────────────────────────────────────
def test_settled_session_waits_for_after_market_end_plus_margin(cal):
    """그날은 애프터마켓 종료(표에서 파생, 20:00) + 30분 뒤부터 요구한다 —
    정규장 경계(`last_closed_session`)는 그대로다(#38 같은 규칙, 경계만 다르다)."""
    assert kr_session.day_end("KRX") == (20, 0)
    assert kr_session.last_settled_session(_at("2026-10-08", 20, 29)) == "2026-10-07"
    assert kr_session.last_settled_session(_at("2026-10-08", 20, 30)) == "2026-10-08"
    assert kr_session.last_closed_session(_at("2026-10-08", 15, 29)) == "2026-10-07"
    assert kr_session.last_closed_session(_at("2026-10-08", 15, 30)) == "2026-10-08"
    assert kr_session.last_settled_session(_at("2026-10-09", 10, 0)) == "2026-10-08"
    assert di.boundary_hm() == (20, 30)
    # 모듈 배선 — 여유 상수가 실제로 넘어가는지(기본값만 재면 배선을 못 본다)
    assert di.settled_session(_at("2026-10-08", 20, 29)) == "2026-10-07"
    assert di.settled_session(_at("2026-10-08", 20, 30)) == "2026-10-08"


def test_naive_times_are_kst_and_recheck_boundary_literal():
    t = di._now(datetime(2026, 10, 12, 20, 40))
    assert t.hour == 20 and t.utcoffset() == timedelta(hours=9)
    assert di.recheck_due_at("2026-10-12") == datetime(2026, 10, 13, 7, 0, tzinfo=K)
    rec = {"date": "2026-10-12", "collected_at": "2026-10-12T20:40:00+09:00", "recheck": None}
    assert di.needs_recheck(rec, datetime(2026, 10, 13, 7, 0, tzinfo=K)) is True
    assert di.needs_recheck(rec, datetime(2026, 10, 13, 6, 59, tzinfo=K)) is False


# ── 순수 계산 ────────────────────────────────────────────────────────────────
def _nets(d):
    out = {}
    for mkt, sfx in di.MARKETS:
        for code, (nm, v) in d[mkt].items():
            out[f"{code}{sfx}"] = [nm, v]
    return out


def test_build_record_tiers_lists_and_values():
    sessions = _want("2026-10-08")
    nbd = {d: _nets(_day()) for d in sessions[-5:]}
    nbd["2026-10-08"] = _nets(_day(hynix=2e9))          # 순매도 → 순매수 = 전환
    nbd["2026-10-07"]["333333.KQ"][1] = 0               # 직전일 0 은 전환 아님
    nbd["2026-10-08"]["333333.KQ"][1] = 2e8             # 0.025% — 소형A(0.05%) 다음
    rec = di.build_record("2026-10-08", sessions, nbd, MCAP)
    tiers = {t["key"]: t for t in rec["tiers"]}
    big = tiers["big"]["lists"]
    assert [r["n"] for r in big["strength"]] == ["삼성전자", "SK하이닉스"]
    assert [r["n"] for r in big["turn"]] == ["SK하이닉스"]
    assert [r["n"] for r in big["streak"]] == ["삼성전자"]
    assert big["streak"][0]["r"] == pytest.approx(5e10 / 4e14 * 100)
    assert big["strength"][0]["r"] == big["streak"][0]["r"]
    mid = tiers["mid"]["lists"]
    assert [r["n"] for r in mid["strength"]] == ["소형A", "코닥C"]
    assert mid["turn"] == []
    assert [r["n"] for r in mid["streak"]] == ["소형A"]
    assert tiers["small"]["n"] == 1 and tiers["mid"]["n"] == 2
    assert rec["window"] == sessions[-5:] and rec["prev"] == "2026-10-07"


def test_zero_net_is_never_a_buy():
    """순매수 0 은 순매수가 아니다 — 강도·전환·5일 연속 어디에도 안 들어간다."""
    sessions = _want("2026-10-08")
    nbd = {d: {"000001.KS": ["영", 0.0], "000002.KS": ["양", 1e8]} for d in sessions[-5:]}
    nbd["2026-10-06"]["000002.KS"][1] = 0.0   # 창 안 하루가 0 → 연속 아님
    nbd["2026-10-07"]["000001.KS"][1] = -1e8
    rec = di.build_record("2026-10-08", sessions, nbd, {"000001": 2e12, "000002": 2e12})
    lists = rec["tiers"][0]["lists"]
    assert [r["n"] for r in lists["strength"]] == ["양"]
    assert lists["turn"] == [] and lists["streak"] == []


def test_tier_boundaries_are_lower_inclusive():
    nets = {"2026-10-08": {"000001.KS": ["딱1조", 1.0], "000002.KS": ["딱5천억", 1.0],
                           "000003.KS": ["5천억미만", 1.0]}}
    rec = di.build_record("2026-10-08", ["2026-10-08"], nets,
                          {"000001": 1e12, "000002": 5e11, "000003": 5e11 - 1})
    got = {t["key"]: [r["n"] for r in t["lists"]["strength"]] for t in rec["tiers"]}
    assert got == {"big": ["딱1조"], "mid": ["딱5천억"], "small": ["5천억미만"]}


def test_top_n_cut_and_order_ties_by_amount():
    nets = {"2026-10-08": {f"{i:06d}.KS": [f"S{i}", 1e8 * (i % 3 + 1)] for i in range(1, 16)}}
    mcap = {f"{i:06d}": 1e12 * (i % 3 + 1) for i in range(1, 16)}
    rec = di.build_record("2026-10-08", ["2026-10-08"], nets, mcap)
    top = rec["tiers"][0]["lists"]["strength"]
    assert len(top) == 10
    assert [r["net"] for r in top] == sorted((r["net"] for r in top), reverse=True)


def test_turn_list_is_cut_at_ten():
    sessions = ["2026-10-07", "2026-10-08"]
    prev = {f"{i:06d}.KS": [f"S{i}", -1e8] for i in range(1, 16)}
    cur = {f"{i:06d}.KS": [f"S{i}", 1e8] for i in range(1, 16)}
    rec = di.build_record("2026-10-08", sessions, {"2026-10-07": prev, "2026-10-08": cur},
                          {f"{i:06d}": 2e12 for i in range(1, 16)})
    assert len(rec["tiers"][0]["lists"]["turn"]) == 10


def test_missing_prior_days_make_lists_unjudged_not_empty():
    sessions = _want("2026-10-08")
    rec = di.build_record("2026-10-08", sessions, {"2026-10-08": _nets(_day())}, MCAP)
    lists = rec["tiers"][0]["lists"]
    assert lists["turn"] is None and lists["streak"] is None
    assert lists["strength"] and len(rec["streak_missing"]) == 4


def test_mcap_missing_tickers_are_excluded_and_counted():
    rec = di.build_record("2026-10-08", ["2026-10-08"], {"2026-10-08": _nets(_day())},
                          {k: v for k, v in MCAP.items() if k != "111111"})
    assert rec["mcap_missing"] == 1 and rec["universe"] == 4


# ── 원천 ────────────────────────────────────────────────────────────────────
def test_fetch_nets_suffixes_and_failure_branches(krx):
    f = krx({"2026-10-08": _day()})
    nets, why, kind = di.fetch_nets("2026-10-08")
    assert (why, kind) == ("", "ok") and nets["005930.KS"] == ["삼성전자", 5e10]
    assert nets["222222.KQ"][0] == "코닥B" and {c[3] for c in f.calls} == {"기관합계"}
    krx({"2026-10-08": {"KOSPI": _day()["KOSPI"], "KOSDAQ": {}}})
    nets, why, kind = di.fetch_nets("2026-10-08")
    assert nets is None and kind == "empty" and "KOSDAQ" in why
    zero = {m: {k: (n, 0) for k, (n, _v) in rows.items()} for m, rows in _day().items()}
    krx({"2026-10-08": zero})
    assert di.fetch_nets("2026-10-08")[2] == "empty"


def test_fetch_mcap_kinds(krx):
    krx({}, mcap_mode={"2026-10-08": "holiday"})
    m, why, kind = di.fetch_mcap("2026-10-08")
    assert m is None and kind == "holiday" and "휴장" in why
    krx({}, mcap_mode={"2026-10-08": "empty"})
    assert di.fetch_mcap("2026-10-08")[2] == "empty"
    krx({})
    m, _w, kind = di.fetch_mcap("2026-10-08")
    assert kind == "ok" and m["005930"] == 4e14


# ── 수집 E2E ────────────────────────────────────────────────────────────────
def test_target_not_aggregated_waits_on_fixed_interval_and_backfills(cal, store, krx):
    """기준일만 집계 전(시총은 옴) = 대기 — 실패가 아니라 15분 고정으로 다시 묻고
    지난 날은 계속 메운다(독립 리뷰 M4·#171)."""
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-12"})
    now = _at("2026-10-12", 21, 0)
    res = di.tick(now=now, budget=100)
    assert res["status"] == "waiting" and "집계" in res["reason"]
    assert res["calls"] >= len(f.calls)      # 예산은 실제 호출보다 적게 세지 않는다
    assert "2026-10-08" in res["built"] and not di.load_record("2026-10-12")
    assert not (store / "raw" / "2026-10-12.json").exists(), "집계 전 날을 구웠다"
    st = di.load_state()
    assert st["fails"] == 0 and st["waiting"] is True
    assert di._parse(st["next_try"]) == now + timedelta(minutes=15)
    assert "running_since" not in st
    # 백오프 안에는 원천을 안 부른다
    n = len(f.calls)
    assert di.tick(now=now + timedelta(minutes=5))["status"] == "backoff"
    assert len(f.calls) == n
    # 두 번째 대기도 15분(지수 백오프가 아니다)
    res = di.tick(now=now + timedelta(minutes=15), budget=100)
    assert res["status"] == "waiting"
    assert di._parse(di.load_state()["next_try"]) == now + timedelta(minutes=30)
    # 집계가 나오면 받는다 — 앞날 원자료는 캐시(그날 2콜 + 시총 1콜)
    f.ready = set(SESS)
    n = len(f.calls)
    res = di.tick(now=now + timedelta(minutes=30), budget=100)
    assert res["status"] == "ok" and res["built"] == ["2026-10-12"]
    assert len(f.calls) - n == 3 == res["calls"]
    st = di.load_state()
    assert (st["fails"], st["reason"], st["waiting"], st["wait_n"]) == (0, "", False, 0)
    assert not st.get("next_try") and st["last_ok"]
    recs = di.load_records()
    assert len(recs) == 10
    assert all(r["tiers"][0]["lists"]["streak"] is not None for r in recs), \
        "오래된 기록의 5일 창을 덮을 만큼 거래일을 안 받았다"


def test_waiting_falls_back_to_ladder_after_cap(cal, store, krx):
    krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-12"})
    di._write_json(di._STATE, {"wait_n": 16, "wait_date": "2026-10-12"})
    now = _at("2026-10-12", 23, 0)
    di.tick(now=now, budget=100)
    assert di._parse(di.load_state()["next_try"]) == now + timedelta(minutes=15)
    di.tick(now=now + timedelta(minutes=15), budget=100)
    assert di._parse(di.load_state()["next_try"]) == now + timedelta(minutes=15 + 30)


def test_wait_count_restarts_for_a_new_target(cal, store, krx):
    """앞 기준일이 끝내 안 나와 상한을 넘겼어도 새 기준일은 다시 15분부터 —
    횟수를 이어 세면 새 기준일이 첫 회부터 지수 백오프다(독립 리뷰)."""
    krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-12"})
    di._write_json(di._STATE, {"wait_n": 40, "wait_date": "2026-10-08"})
    now = _at("2026-10-12", 21, 0)
    assert di.tick(now=now, budget=100)["status"] == "waiting"
    st = di.load_state()
    assert (st["wait_n"], st["wait_date"]) == (1, "2026-10-12")
    assert di._parse(st["next_try"]) == now + timedelta(minutes=15)


def test_source_silence_is_an_outage_with_ladder(cal, store, krx):
    """순매수도 시총도 비면 KRX 가 답하지 않는 것 — 전체 백오프(15→30분)."""
    krx({d: _day() for d in SESS}, ready=set(), mcap_mode={d: "empty" for d in SESS})
    now = _at("2026-10-12", 21, 0)
    res = di.tick(now=now, budget=100)
    assert res["status"] == "fail" and "답하지 않음" in res["reason"]
    st = di.load_state()
    assert st["fails"] == 1 and di._parse(st["next_try"]) == now + timedelta(minutes=15)
    di.tick(now=now + timedelta(minutes=15), budget=100)
    st = di.load_state()
    assert st["fails"] == 2 and di._parse(st["next_try"]) == now + timedelta(minutes=45)


def test_one_bad_past_day_does_not_block_others_then_becomes_a_nodata_card(cal, store, krx):
    """지난 거래일 하나가 계속 비어도(시장은 열림) 다른 날은 메우고, 충분히 지켜본 뒤엔
    그날을 빼고 주변 기록을 만든다 — 그날 카드는 사유를 말한다(독립 리뷰 H1).
    '자료 없음' 확정은 ① 3회 이상 ② 첫 실패 뒤 12시간 ③ **같은 주기에 더 나중 날짜의
    순매수를 원천에서 받았다**(같은 엔드포인트의 양성 대조) 셋이 다 서야 한다(델타 리뷰)."""
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06"})
    now = _at("2026-10-12", 21, 0)
    res = di.tick(now=now, budget=100)
    assert res["status"] == "ok"
    assert not set(res["built"]) & {"2026-10-12", "2026-10-08", "2026-10-07", "2026-10-06"}
    assert "2026-10-02" in res["built"]
    assert len([c for c in f.calls if c[1] == "20261006" and c[2] == "KOSPI"]) == 1, \
        "한 주기에 같은 날을 두 번 물었다"
    st = di.load_state()
    assert st["days"]["2026-10-06"]["n"] == 1 and not st.get("next_try")
    assert st["days"]["2026-10-06"]["traded"] is True
    assert st["days"]["2026-10-06"]["first"] == di._iso(now)
    kind, line = di.status_verdict("2026-10-02", now, st)
    assert kind == "behind" and "2026-10-06 1회 실패" in line
    t = now
    for _ in range(2):
        t = di._parse(di.load_state()["days"]["2026-10-06"]["next_try"]) + timedelta(minutes=1)
        di.tick(now=t, budget=100)
    info = di.load_state()["days"]["2026-10-06"]
    assert info["n"] == 3 and info["first"] == di._iso(now), "첫 실패 시각을 덮었다"
    blocked = {"20261012", "20261008", "20261007"}
    assert not [c for c in f.calls if c[0] == "cap" and c[1] in blocked], \
        "창 날짜가 막힌 기록의 시가총액을 먼저 받았다(캐시도 안 돼 매 주기 다시 받는다)"
    assert not di.load_raw("2026-10-06"), "12시간도 안 지켜보고 확정했다"
    assert len([c for c in f.calls if c[1] == "20261012" and c[2] == "KOSPI"]) == 1, \
        "다른 조건이 안 섰는데 대조를 물었다(그때마다 2콜)"
    # 이튿날 아침: 12시간이 지났고 이번 주기에 새로 받은 더 나중 날짜는 없다(전부 캐시) —
    # 캐시된 가장 나중 날짜(10-12)를 **지금 다시** 물어 답하면 그게 양성 대조다. 새로 받은
    # 날만 대조로 치면 15분 주기에서 그 조건이 다시 서지 않아 영영 막힌다(델타 리뷰).
    n = len(f.calls)
    di.tick(now=_at("2026-10-13", 10, 0), budget=100)
    assert ("net", "20261012", "KOSPI", "기관합계") in f.calls[n:]
    raw = di.load_raw("2026-10-06")
    assert raw["nodata"] == "missing" and "시가총액은 나옴" in raw["reason"]
    assert "2026-10-06" not in di.load_state().get("days", {})
    assert di.load_raw("2026-10-12") == {"date": "2026-10-12",
                                         "fetched_at": di._iso(now),
                                         "nets": _nets(_day())}, "대조가 원자료를 덮었다"
    # 다음 주기에 그날을 빼고 주변 기록이 만들어진다
    built = di.tick(now=_at("2026-10-13", 10, 16), budget=100)["built"]
    assert {"2026-10-12", "2026-10-08", "2026-10-07", "2026-10-06"} <= set(built)
    stub = di.load_record("2026-10-06")
    assert stub["nodata"] == raw["reason"] and not stub["tiers"], "카드가 사유를 안 실었다"
    r7 = di.load_record("2026-10-07")
    assert r7["tiers"][0]["lists"]["turn"] is None          # 직전 거래일 자료 없음
    assert r7["tiers"][0]["lists"]["streak"] is None


@pytest.mark.parametrize("n_pre, age_h, marked", [(2, 11, False), (2, 13, True),
                                                   (1, 13, False)])
def test_missing_needs_twelve_hours_and_three_tries(cal, store, krx, n_pre, age_h, marked):
    krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06"})
    now = _at("2026-10-12", 21, 0)
    di._write_json(di._STATE, {"days": {"2026-10-06": {
        "n": n_pre, "reason": "x(시가총액은 나옴)", "kind": "empty", "traded": True,
        "first": di._iso(now - timedelta(hours=age_h)),
        "next_try": di._iso(now - timedelta(minutes=1))}}})
    di.tick(now=now, budget=100)
    assert bool(di.load_raw("2026-10-06")) is marked


def test_missing_is_never_marked_in_a_tick_cut_by_an_outage(cal, store, krx):
    """같은 주기에 확정 조건을 다 채운 날(3회 · 12시간 · 더 나중 날짜를 받음)이 있어도,
    그 뒤에 원천 장애로 끊긴 주기면 확정하지 않는다 — 장애 중의 '빈 응답' 은 그날 자료가
    없다는 증거가 아니다(독립 리뷰). 같은 설정에서 장애가 없으면 확정되는 것은
    `test_missing_needs_twelve_hours_and_three_tries[2-13-True]` 가 잰다."""
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06", "2026-09-30"},
            mcap_mode={"2026-09-30": "empty"})
    now = _at("2026-10-12", 21, 0)
    di._write_json(di._STATE, {"days": {"2026-10-06": {
        "n": 2, "reason": "x(시가총액은 나옴)", "kind": "empty", "traded": True,
        "first": di._iso(now - timedelta(hours=13)),
        "next_try": di._iso(now - timedelta(minutes=1))}}})
    res = di.tick(now=now, budget=100)
    assert ("net", "20261012", "KOSPI", "기관합계") in f.calls, "기준일을 안 받았다(대조가 안 섰다)"
    assert res["status"] == "fail" and "KRX 가 답하지 않음" in res["reason"]
    assert di.load_state()["days"]["2026-10-06"]["n"] == 3, "확정 조건까지 가지 않았다(재현 실패)"
    assert not di.load_raw("2026-10-06"), "원천 장애로 끊긴 주기에 '자료 없음' 을 확정했다"


def test_missing_needs_a_later_date_answering_now(cal, store, krx):
    """더 **나중** 날짜가 **지금** 답해야 대조가 선다 — 앞 날짜만 새로 받은 것은 대조가
    아니고(시총은 다른 엔드포인트라 순매수만 죽은 것과 구별하지 못한다), 나중 날짜가
    캐시면 그 가장 나중 날을 지금 다시 묻는다(2콜, 아무것도 안 쓴다). 그게 안 답하면
    확정하지 않는다(델타 리뷰)."""
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06"})
    f.boom = {"2026-10-12"}
    now = _at("2026-10-12", 21, 0)
    for d in ("2026-10-07", "2026-10-08", "2026-10-12"):
        di._write_json(di._raw_path(d), {"date": d, "nets": _nets(_day())})
    di._write_json(di._STATE, {"days": {"2026-10-06": {
        "n": 2, "reason": "x(시가총액은 나옴)", "kind": "empty", "traded": True,
        "first": di._iso(now - timedelta(hours=30)),
        "next_try": di._iso(now - timedelta(minutes=1))}}})
    res = di.tick(now=now, budget=100)
    assert "2026-10-02" in res["built"], "앞 날짜는 새로 받았다(대조 후보에서 빠져야 한다)"
    assert [c for c in f.calls if c[1] == "20261012"], "캐시된 나중 날짜를 다시 묻지 않았다"
    assert not [c for c in f.calls if c[1] in ("20261007", "20261008")], \
        "가장 나중 날짜가 아닌 날을 물었다"
    assert not di.load_raw("2026-10-06"), "나중 날짜가 안 답하는데 확정했다"
    assert di.load_state()["days"]["2026-10-06"]["n"] == 3
    assert di.load_raw("2026-10-12") == {"date": "2026-10-12", "nets": _nets(_day())}
    # 나중 날짜가 지금 답하면 확정한다
    f.boom = set()
    t = di._parse(di.load_state()["days"]["2026-10-06"]["next_try"]) + timedelta(minutes=1)
    di.tick(now=t, budget=100)
    assert di.load_raw("2026-10-06")["nodata"] == "missing"
    # 예산이 2콜도 안 남았으면 대조를 못 한 것이다 — 묻지도 확정하지도 않는다
    n = len(f.calls)
    assert di._live_control("2026-10-06", SESS, di._Budget(1), "t") is None
    assert len(f.calls) == n, "예산 없이 물었다"


def test_errors_and_coverage_are_never_marked_missing(cal, store, krx):
    """예외·시총 반쪽은 '시장은 열렸는데 기관 순매수만 없다' 가 아니다 — 몇 번이든 그날만
    늦추고 표식은 안 단다. 그리고 지난 날 하나의 예외·반쪽은 전체 장애가 아니다(델타 리뷰)."""
    f = krx({d: _day() for d in SESS})
    f.boom = {"2026-10-06"}
    now = _at("2026-10-12", 21, 0)
    old = {"n": 5, "reason": "x", "kind": "error", "traded": False,
           "first": di._iso(now - timedelta(hours=30)),
           "next_try": di._iso(now - timedelta(minutes=1))}
    di._write_json(di._STATE, {"days": {"2026-10-06": dict(old)}})
    res = di.tick(now=now, budget=100)
    st = di.load_state()
    assert res["status"] == "ok" and st["fails"] == 0 and not st.get("next_try")
    assert st["days"]["2026-10-06"]["traded"] is False and "ConnectionError" in \
        st["days"]["2026-10-06"]["reason"]
    assert not di.load_raw("2026-10-06")
    # 시총 반쪽(커버리지) — 그날 자기 기록만 막힌다
    f2 = krx({d: _day() for d in SESS}, mcap_mode={"2026-10-02": "partial"})
    di._write_json(di._STATE, {"days": {"2026-10-02": {**old, "kind": "coverage"}}})
    for p in list((store / "records").glob("*.json")) + list((store / "raw").glob("*.json")):
        p.unlink()
    res = di.tick(now=now, budget=100)
    st = di.load_state()
    assert res["status"] == "ok" and st["fails"] == 0
    assert "2026-10-02" not in res["built"] and "2026-10-06" in res["built"]
    assert st["days"]["2026-10-02"]["kind"] == "coverage"
    assert not (di.load_raw("2026-10-02") or {}).get("nodata")
    # 미뤄 둔 날은 재시도 시각 전엔 시총도 다시 안 묻는다
    n = len(f2.calls)
    di.tick(now=now + timedelta(minutes=16), budget=100)
    assert not [c for c in f2.calls[n:] if c[1] == "20261002"]


def test_target_coverage_shortfall_is_a_day_failure_not_an_outage(cal, store, krx):
    krx({d: _day() for d in SESS}, mcap_mode={"2026-10-12": "partial"})
    res = di.tick(now=_at("2026-10-12", 21, 0), budget=100)
    st = di.load_state()
    assert res["status"] == "ok" and st["fails"] == 0 and not st.get("next_try")
    assert "반쪽" in st["days"]["2026-10-12"]["reason"]
    assert "2026-10-08" in res["built"]


def test_krx_holiday_is_removed_from_sessions(cal, store, krx):
    """달력엔 거래일인데 KRX 가 휴장으로 답하면(시총 전부 0) 거래일에서 뺀다 —
    직전 거래일·5일 창이 그날을 건너뛴다."""
    nets = {d: _day() for d in SESS}
    nets["2026-10-02"] = _day(hynix=-3e9)
    nets["2026-10-07"] = _day(hynix=2e9)
    krx(nets, ready=set(SESS) - {"2026-10-06"}, mcap_mode={"2026-10-06": "holiday"})
    now = _at("2026-10-12", 21, 0)
    res = di.tick(now=now, budget=100)
    assert res["holidays"] == ["2026-10-06"] and res["status"] == "ok"
    di.tick(now=now + timedelta(minutes=15), budget=100)
    assert not di.load_record("2026-10-06")
    r7 = di.load_record("2026-10-07")
    assert r7["prev"] == "2026-10-02" and "2026-10-06" not in r7["window"]
    assert [x["n"] for x in r7["tiers"][0]["lists"]["turn"]] == ["SK하이닉스"]


def test_target_zero_mcap_before_next_morning_waits_not_holiday(cal, store, krx):
    krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-12"},
        mcap_mode={"2026-10-12": "holiday"})
    res = di.tick(now=_at("2026-10-12", 21, 0), budget=100)
    assert res["status"] == "waiting" and not res["holidays"]
    assert not di.load_raw("2026-10-12")


def test_day_failures_outside_the_window_are_dropped(cal, store, krx):
    """창을 벗어난 날의 실패 기록이 남으면 화면이 영원히 '⚠️ 실패 중' 이다."""
    krx({d: _day() for d in SESS})
    di._write_json(di._STATE, {"days": {"2026-09-01": {"n": 2, "reason": "x"},
                                        "recheck:2026-09-02": {"n": 1, "reason": "y"}}})
    di.tick(now=_at("2026-10-12", 21, 0), budget=100)
    assert di.load_state()["days"] == {}


def test_two_bad_past_days_with_a_dead_endpoint_are_an_outage(cal, store, krx):
    """지난 거래일 둘이 실패했는데 이번 주기에 순매수를 받은 날도 없고, 캐시된 가장 나중
    날을 다시 물어도 안 답하면 원천 장애다 — 전체 백오프."""
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06", "2026-09-30"})
    f.boom = {"2026-10-12"}
    _seed_raw(_SEED, skip={"2026-10-06", "2026-09-30"})
    res = di.tick(now=_at("2026-10-12", 21, 0), budget=100)
    assert res["status"] == "fail" and "여러 날" in res["reason"]
    assert "다시 물어 답한 날도 없다" in res["reason"]
    assert [c for c in f.calls if c[1] == "20261012"], "장애로 끊기 전에 대조를 안 물었다"
    assert di.load_state()["fails"] == 1
    assert not di.load_raw("2026-10-06") and not di.load_raw("2026-09-30"), "장애에 표식을 달았다"


def test_two_bad_past_days_with_a_live_endpoint_keep_building_then_both_get_cards(
        cal, store, krx):
    """둘째 날이 실패해도 엔드포인트가 살아 있으면(이번 주기에 받은 날이 있다) 장애로
    끊지 않고 그 앞 날짜를 계속 메운다 — 무조건 끊으면 두 날이 실제로 비는 동안 기준일
    하나만 만들고 보드가 멈춘다(델타 리뷰 실측). 충분히 지켜본 뒤엔 두 날 다 '자료 없음'
    카드가 되고 대조는 한 주기에 한 번이다(셋째 날이 실패해도 다시 묻지 않는다)."""
    bad = {"2026-10-06", "2026-10-01", "2026-09-30"}
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - bad)
    now = _at("2026-10-12", 21, 0)
    res = di.tick(now=now, budget=100)
    st = di.load_state()
    assert res["status"] == "ok" and st["fails"] == 0 and not st.get("next_try")
    assert "2026-09-29" in res["built"], "둘째 실패에서 끊어 그 앞 날짜를 안 메웠다"
    assert bad <= set(st["days"])
    assert len([c for c in f.calls if c[1] == "20261012" and c[2] == "KOSPI"]) == 1, \
        "이번 주기에 받은 날이 있는데 대조를 또 물었다"
    t = now
    for _ in range(2):
        t = max(di._parse(st["days"][d]["next_try"]) for d in bad) + timedelta(minutes=1)
        di.tick(now=t, budget=100)
        st = di.load_state()
    assert all(st["days"][d]["n"] == 3 for d in bad)
    assert not any(di.load_raw(d) for d in bad), "12시간도 안 지켜보고 확정했다"
    # 이튿날 아침: 받은 날이 없는 주기 — 캐시된 가장 나중 날을 한 번만 다시 물어 대조한다
    n = len(f.calls)
    res = di.tick(now=_at("2026-10-13", 10, 0), budget=100)
    assert res["status"] != "fail"
    live = [c for c in f.calls[n:] if c[1] == "20261012" and c[2] == "KOSPI"]
    assert len(live) == 1, f"대조를 {len(live)}번 물었다(한 주기 한 번)"
    for d in bad:
        assert di.load_raw(d)["nodata"] == "missing"
    assert not bad & set(di.load_state().get("days", {}))
    built = di.tick(now=_at("2026-10-13", 10, 16), budget=100)["built"]
    assert {"2026-10-12", "2026-10-06", "2026-10-02", "2026-10-01", "2026-09-30"} <= set(built)
    assert all(di.load_record(d)["nodata"] for d in bad)


def test_failed_control_is_asked_once_and_marks_nothing(cal, store, krx):
    """엔드포인트는 살아 있지만(재질의로 앞 날짜를 받았다) 그건 빈 날들보다 **앞**이라
    대조가 아니다 — 가장 나중 날을 한 번 다시 묻고, 안 답하면 두 날 다 확정하지 않는다.
    대조는 한 주기에 한 번이다(실패한 대조를 날마다 또 물으면 2콜씩 샌다)."""
    bad = {"2026-10-06", "2026-10-07"}
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - bad)
    f.boom = {"2026-10-12"}
    now = _at("2026-10-12", 21, 0)
    _seed_raw(_SEED, skip=bad)
    di._mark_nodata("2026-09-25", "missing", "x", now - timedelta(hours=25))
    old = {"n": 2, "reason": "x(시가총액은 나옴)", "kind": "empty", "traded": True,
           "first": di._iso(now - timedelta(hours=30)),
           "next_try": di._iso(now - timedelta(minutes=1))}
    di._write_json(di._STATE, {"days": {d: dict(old) for d in bad}})
    res = di.tick(now=now, budget=100)
    assert di.load_raw("2026-09-25").get("nets"), "앞 날짜 재질의가 안 됐다(재현이 안 됐다)"
    assert res["status"] != "fail", "엔드포인트가 답했는데 장애로 끊었다"
    assert len([c for c in f.calls if c[1] == "20261012" and c[2] == "KOSPI"]) == 1, \
        "실패한 대조를 다시 물었다"
    assert not any(di.load_raw(d) for d in bad), "빈 날보다 앞 날짜로 확정했다"


def test_second_failure_without_budget_for_the_control_is_not_an_outage(cal, store, krx):
    """대조할 예산이 없으면 장애로 단정하지 않는다 — 예산 소진으로 끝난다."""
    krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06", "2026-09-30"})
    _seed_raw(_SEED, skip={"2026-10-06", "2026-09-30"})
    # 10-06(2) + 시총 대조(1) + 09-30(2) + 시총 대조(1) = 6 — 대조 2콜이 안 남는다
    res = di.tick(now=_at("2026-10-12", 21, 0), budget=7)
    st = di.load_state()
    assert res["status"] == "budget" and st["fails"] == 0 and not st.get("next_try")


def test_partial_mcap_is_not_cached_and_is_asked_again(cal, store, krx):
    """커버리지 미달 시총은 저장하지 않는다 — 다음 시도가 원천에 다시 묻는다(독립 리뷰 M3)."""
    f = krx({d: _day() for d in SESS}, mcap={"005930": 4e14})
    now = _at("2026-10-12", 21, 0)
    di.tick(now=now, budget=100)
    assert not di.load_records()
    assert not (di.load_raw("2026-10-12") or {}).get("mcap")
    st = di.load_state()
    assert "반쪽" in (st.get("reason") or "") + json.dumps(st.get("days"), ensure_ascii=False)
    f.mcap = MCAP
    later = now + timedelta(hours=4)
    n = len(f.calls)
    res = di.tick(now=later, budget=100)
    assert ("cap", "20261012", "ALL") in f.calls[n:]
    assert "2026-10-12" in res["built"]


def test_unexpected_exception_leaves_state_and_backoff(cal, store, krx, monkeypatch):
    krx({d: _day() for d in SESS})
    monkeypatch.setattr(di, "build_record",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    now = _at("2026-10-12", 21, 0)
    res = di.tick(now=now, budget=100)
    st = di.load_state()
    assert res["status"] == "fail" and "OSError" in st["reason"] and "disk full" in st["reason"]
    assert di._parse(st["next_try"]) == now + timedelta(minutes=15)
    assert "running_since" not in st


def test_budget_exhaustion_is_not_a_failure(cal, store, krx):
    f = krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    old = store / "raw" / "2026-07-01.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"date": "2026-07-01", "nets": {}}), encoding="utf-8")
    res = di.tick(now=now, budget=5)
    assert res["status"] == "budget" and res["calls"] == len(f.calls) <= 5
    assert not di.load_state().get("next_try"), "예산 소진에 백오프를 걸었다"
    assert old.exists(), "다 못 받은 주기에 원자료를 정리했다"
    assert di.tick(now=now + timedelta(minutes=15), budget=100)["status"] == "ok"
    assert not old.exists()


def test_idle_tick_keeps_last_ok_and_prunes_old_raw(cal, store, krx):
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    di.tick(now=now, budget=100)
    first = di.load_state()["last_ok"]
    old = store / "raw" / "2026-07-01.json"
    old.write_text(json.dumps({"date": "2026-07-01", "nets": {}}), encoding="utf-8")
    res = di.tick(now=now + timedelta(minutes=15), budget=100)
    assert res["status"] == "idle" and di.load_state()["last_ok"] == first
    assert not old.exists(), f"{di.RAW_KEEP_DAYS}일 넘은 원자료를 안 지웠다"


def test_next_morning_recheck_records_the_difference(cal, store, krx):
    nets = {d: _day() for d in SESS}
    f = krx(nets)
    evening = _at("2026-10-12", 20, 40)
    assert di.tick(now=evening, budget=100)["status"] == "ok"
    rec = di.load_record("2026-10-12")
    assert rec["recheck"] is None and di.needs_recheck(rec, evening + timedelta(hours=2)) is False
    f.nets["2026-10-12"] = _day(samsung=6e10)
    morning = _at("2026-10-13", 7, 5)
    n = len(f.calls)
    res = di.tick(now=morning, budget=100)
    assert res["rechecked"] == ["2026-10-12"] and len(f.calls) - n == 2   # 시총은 캐시
    rec = di.load_record("2026-10-12")
    assert rec["recheck"]["changed"] == 1 and rec["recheck"]["max_abs_won"] == int(1e10)
    assert rec["tiers"][0]["lists"]["strength"][0]["net"] == int(6e10)
    assert rec["tiers"][0]["lists"]["streak"] is not None, "재확인이 5일 창 원자료를 안 읽었다"
    assert rec["collected_at"].startswith("2026-10-12T20:40")
    n = len(f.calls)
    di.tick(now=morning + timedelta(minutes=15), budget=100)
    assert not [c for c in f.calls[n:] if c[1] == "20261012"]


def test_recheck_covers_every_evening_record_not_just_the_last(cal, store, krx):
    """봇이 이틀 멈췄다 와도 저녁에 받은 기록은 전부 재확인한다(독립 리뷰 Low)."""
    krx({d: _day() for d in SESS})
    for d in ("2026-10-08", "2026-10-07"):
        r = di.build_record(d, _want(d), {x: _nets(_day()) for x in _want(d)}, MCAP,
                            collected_at=f"{d}T20:40:00+09:00")
        di._write_json(di.record_path(d), r)
        di._write_json(di._raw_path(d), {"date": d, "nets": _nets(_day()), "mcap": MCAP})
    res = di.tick(now=_at("2026-10-12", 10, 0), budget=100)
    assert {"2026-10-08", "2026-10-07"} <= set(res["rechecked"])


def test_recheck_failure_midway_keeps_the_evening_raw(cal, store, krx):
    """재확인이 중간에 실패해도 저녁 원자료를 덮지 않는다 — 다음 재시도가 여전히
    저녁 값과 대조한다(독립 리뷰 M7: 거짓 '바뀐 종목 0')."""
    f = krx({d: _day() for d in SESS})
    assert di.tick(now=_at("2026-10-12", 20, 40), budget=100)["status"] == "ok"
    raw = di.load_raw("2026-10-12")
    raw.pop("mcap")
    di._write_json(di._raw_path("2026-10-12"), raw)          # 시총을 다시 받아야 하게
    f.nets["2026-10-12"] = _day(samsung=6e10)
    f.mcap_mode["2026-10-12"] = "empty"
    morning = _at("2026-10-13", 7, 5)
    res = di.tick(now=morning, budget=100)
    assert not res["rechecked"]
    assert di.load_raw("2026-10-12")["nets"]["005930.KS"][1] == 5e10, "저녁 원자료를 덮었다"
    f.mcap_mode["2026-10-12"] = "ok"
    t = di._parse(di.load_state()["days"]["recheck:2026-10-12"]["next_try"])
    res = di.tick(now=t + timedelta(minutes=1), budget=100)
    assert res["rechecked"] == ["2026-10-12"]
    assert di.load_record("2026-10-12")["recheck"]["changed"] == 1
    assert "recheck:2026-10-12" not in di.load_state()["days"], \
        "재확인이 끝났는데 실패 기록이 남아 화면이 계속 ⚠️"


def test_recheck_without_evening_raw_says_it_could_not_compare(cal, store, krx):
    krx({d: _day() for d in SESS})
    assert di.tick(now=_at("2026-10-12", 20, 40), budget=100)["status"] == "ok"
    (store / "raw" / "2026-10-12.json").unlink()
    res = di.tick(now=_at("2026-10-13", 7, 5), budget=100)
    assert res["rechecked"] == ["2026-10-12"]
    rec = di.load_record("2026-10-12")
    assert rec["recheck"]["changed"] is None
    assert "저녁 원자료가 없어 대조 못 함" in di._day_meta(rec)


def test_backfilled_record_needs_no_recheck():
    rec = {"date": "2026-10-08", "collected_at": "2026-10-12T21:00:00+09:00", "recheck": None}
    assert di.needs_recheck(rec, datetime(2026, 10, 12, 22, 0, tzinfo=K)) is False


def test_target_zero_mcap_after_next_morning_is_a_holiday(cal, store, krx):
    """이튿날 07:00 이 지나도 시총이 전부 0 이면 KRX 가 그날을 휴장으로 답한 것."""
    krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-12"},
        mcap_mode={"2026-10-12": "holiday"})
    res = di.tick(now=_at("2026-10-13", 6, 59), budget=100)
    assert res["status"] == "waiting" and not res["holidays"]
    di._write_json(di._STATE, {})
    res = di.tick(now=_at("2026-10-13", 7, 0), budget=100)
    assert res["holidays"] == ["2026-10-12"]
    assert di.load_raw("2026-10-12")["nodata"] == "holiday"


def test_effective_target_skips_a_holiday_marked_settled_day(cal, store, krx):
    """경계상 기준일(10-12)이 휴장 표식이면 실제 기준일은 그 앞 거래일 — 그날이 비면
    '집계 대기' 이지 '지난 날 실패' 가 아니다."""
    now = _at("2026-10-12", 21, 0)
    di._write_json(di._raw_path("2026-10-12"), {"date": "2026-10-12", "nodata": "holiday",
                                                 "reason": "x", "at": di._iso(now)})
    krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-08", "2026-10-12"})
    res = di.tick(now=now, budget=100)
    st = di.load_state()
    assert res["status"] == "waiting" and st["wait_date"] == "2026-10-08"
    assert "2026-10-08" not in st.get("days", {})


def test_backoff_of_a_window_day_before_want_survives(cal, store, krx):
    """보관 기록(want) 바로 앞 날짜도 앞 기록들의 5일 창에 든다 — 그날의 백오프를 want
    기준으로 지우면 매 주기 다시 묻는다(델타 리뷰: 24시간 KRX 576콜)."""
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    span = di.recent_sessions("2026-10-12", di.KEEP_SESSIONS + di.STREAK_N - 1)
    early = span[0]
    assert early not in _want("2026-10-12")
    entry = {"n": 1, "reason": "x", "next_try": di._iso(now + timedelta(hours=1))}
    di._write_json(di._STATE, {"days": {early: dict(entry), "2026-08-24": dict(entry)}})
    di.tick(now=now, budget=100)
    days = di.load_state()["days"]
    assert early in days and "2026-08-24" not in days


def test_reopen_nodata_mark_after_a_day_and_rebuild_neighbours(cal, store, krx):
    """'자료 없음' 표식은 창 안에 있는 동안 하루 한 번 다시 묻는다 — 이번엔 오면 표식을
    지우고 그날과 그날을 창에 둔 기록을 **제자리에서** 다시 만든다(델타 리뷰: 일시 장애가
    영구 표식이 되지 않게 · 기록을 지우고 맡기면 보관 범위 밖 기록이 사라진다)."""
    f = krx({d: _day() for d in SESS}, ready=set(SESS) - {"2026-10-06"})
    now = _at("2026-10-12", 21, 0)
    di._mark_nodata("2026-10-06", "missing", "x(시가총액은 나옴)", now)
    di.tick(now=now, budget=100)
    assert di.load_record("2026-10-06")["nodata"]
    assert di.load_record("2026-10-07")["tiers"][0]["lists"]["turn"] is None
    # 24시간 안엔 다시 안 묻는다
    n = len(f.calls)
    di.tick(now=now + timedelta(hours=23), budget=100)
    assert not [c for c in f.calls[n:] if c[1] == "20261006"]
    # 24시간 뒤에도 비면 표식을 하루 미룬다
    later = now + timedelta(hours=24)
    di.tick(now=later, budget=100)
    raw = di.load_raw("2026-10-06")
    assert raw["nodata"] == "missing" and di._parse(raw["at"]) == later
    # 그다음 날엔 자료가 왔다 → 표식을 지우고 카드·이웃 기록을 다시 만든다
    f.ready = set(SESS)
    t = later + timedelta(hours=24, minutes=1)
    res = di.tick(now=t, budget=100)
    assert di.load_raw("2026-10-06").get("nets")
    assert "2026-10-06" in res["built"] and "2026-10-07" in res["built"]
    assert not di.load_record("2026-10-06").get("nodata")
    r7 = di.load_record("2026-10-07")
    assert r7["prev"] == "2026-10-06" and r7["tiers"][0]["lists"]["turn"] is not None


def test_reopen_a_holiday_mark_that_traded_after_all(cal, store, krx):
    """휴장 표식도 다시 묻는다 — 자료가 오면 그날이 거래일로 돌아와 직전 거래일이 바뀐다."""
    f = krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    di._mark_nodata("2026-10-06", "holiday", "x", now - timedelta(hours=25))
    # 표식 상태로 만든 기록(10-07 의 직전 거래일 = 10-02)을 미리 둔다
    sess = [d for d in SESS if d != "2026-10-06"]
    w = [d for d in sess if d <= "2026-10-07"][-10:]
    di._write_json(di.record_path("2026-10-07"),
                   di.build_record("2026-10-07", w, {d: _nets(_day()) for d in w}, MCAP,
                                   collected_at=di._iso(now)))
    assert di.load_record("2026-10-07")["prev"] == "2026-10-02"
    res = di.tick(now=now, budget=100)
    assert ("net", "20261006", "KOSPI", "기관합계") in f.calls
    assert not di.load_raw("2026-10-06").get("nodata")
    assert "2026-10-07" in res["built"] and di.load_record("2026-10-07")["prev"] == "2026-10-06"


def _seed_raw(dates, skip=()):
    for d in dates:
        if d not in skip:
            di._write_json(di._raw_path(d), {"date": d, "nets": _nets(_day()),
                                             "mcap": dict(MCAP)})


def _pre_reopen_store(now):
    """09-22 를 '자료 없음' 으로 찍은 채 만든 기록 — 09-23·09-24 는 보관 범위(want) 밖,
    09-25·09-28 은 안. 그날을 창에 둔 기록의 전환·연속 칸은 판정 불가(None) 다."""
    di._mark_nodata("2026-09-22", "missing", "x(시가총액은 나옴)", now - timedelta(hours=25))
    di._write_json(di.record_path("2026-09-22"),
                   di.nodata_record("2026-09-22", "x(시가총액은 나옴)",
                                    "2026-09-23T21:00:00+09:00"))
    olds = {}
    for x in ("2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"):
        w = _want(x)
        nbd = {d: _nets(_day()) for d in w[-5:] if d != "2026-09-22"}
        olds[x] = di.build_record(x, w, nbd, MCAP, collected_at=f"{x}T20:40:00+09:00")
    olds["2026-09-23"]["recheck"] = {"at": "2026-09-24T07:05:00+09:00", "changed": 0,
                                     "value_changed": 0, "compared": 5, "max_abs_won": 0}
    for x, rec in olds.items():
        di._write_json(di.record_path(x), rec)
    return olds


_SEED = [d for d in SESS if "2026-09-14" <= d <= "2026-10-12"]


def test_reopen_rebuilds_neighbours_in_place_even_outside_the_kept_range(cal, store, krx):
    """표식이 풀린 날을 창에 둔 기록은 **제자리에서** 다시 만든다 — 지우고 ③ 에 맡기면
    보관 범위(want, 최근 10거래일) 밖 기록(09-23·09-24)은 영영 안 돌아왔다(델타 리뷰
    실측). 다시 못 만들면 옛 기록이 남고, 그 재시도는 그 날짜만 늦춘다."""
    f = krx({d: _day() for d in SESS})
    f.boom = {"2026-09-16"}                 # 09-22 의 5일 창 맨 앞 — 09-22 재구성만 막힌다
    now = _at("2026-10-12", 21, 0)
    assert "2026-09-23" not in _want("2026-10-12") and "2026-09-25" in _want("2026-10-12")
    _seed_raw(_SEED, skip={"2026-09-16", "2026-09-22"})
    olds = _pre_reopen_store(now)
    res = di.tick(now=now, budget=100)
    assert di.load_raw("2026-09-22").get("nets"), "표식을 안 풀었다"
    assert {"2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"} <= set(res["built"])
    r23 = di.load_record("2026-09-23")
    assert r23["prev"] == "2026-09-22" and r23["tiers"][0]["lists"]["turn"] is not None
    assert r23["recheck"] == olds["2026-09-23"]["recheck"], "재확인 결과를 버렸다(다시 받는다)"
    r24 = di.load_record("2026-09-24")
    assert r24["tiers"][0]["lists"]["streak"] is not None
    assert r24["collected_at"] == "2026-09-24T20:40:00+09:00", \
        "수집 시각을 지금으로 덮었다(재확인 전 기록이 재확인을 건너뛴다)"
    # 09-22 자신은 창 맨 앞(09-16)이 막혀 못 만들었다 — 카드는 남고 그 날짜만 늦춘다
    assert di.load_record("2026-09-22")["nodata"], "옛 기록을 지웠다"
    st = di.load_state()
    assert st["stale"] == ["2026-09-22"] and st["fails"] == 0 and res["status"] == "ok"
    assert st["days"]["stale:2026-09-22"]["n"] == 1
    assert "2026-09-22 재구성 1회 실패" in di._days_note(st)
    # 재시도 시각 전엔 다시 안 묻고, 그 뒤 원천이 답하면 만든다
    n = len(f.calls)
    di.tick(now=now + timedelta(minutes=5), budget=100)
    assert not [c for c in f.calls[n:] if c[1] == "20260916"]
    f.boom = set()
    res = di.tick(now=now + timedelta(minutes=16), budget=100)
    assert "2026-09-22" in res["built"] and not di.load_record("2026-09-22").get("nodata")
    st = di.load_state()
    assert st["stale"] == [] and "stale:2026-09-22" not in st.get("days", {})


def test_stale_rebuilds_survive_a_tick_that_runs_out_of_budget(cal, store, krx):
    """재구성(①-b)이 예산을 다 써도 다시 만들 기록을 잃지 않는다 — 상태에 남겨 다음 주기가
    잇는다. 예산 소진은 실패가 아니다(그 날짜를 늦추지 않는다). 재질의(①)는 2콜로 성공하고,
    캐시만으로 되는 이웃은 다시 만든 뒤 09-22 재구성(09-16 이 필요)에서 예산이 끝난다."""
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    _seed_raw(_SEED, skip={"2026-09-16", "2026-09-22"})
    _pre_reopen_store(now)
    res = di.tick(now=now, budget=2)
    st = di.load_state()
    assert res["status"] == "budget" and st["stale"] == ["2026-09-22"]
    assert di.load_record("2026-09-22")["nodata"], "옛 기록을 지웠다"
    assert "stale:2026-09-22" not in st.get("days", {}), "예산 소진을 실패로 셌다"
    res = di.tick(now=now + timedelta(minutes=15), budget=100)
    assert "2026-09-22" in res["built"] and di.load_state()["stale"] == []


_NEIGHBOURS = {"2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"}


def test_reopen_runs_out_of_budget_after_a_successful_reopen(cal, store, krx):
    """재질의(①) 자체가 예산으로 멈춰도 앞서 성공한 재질의의 재구성 대상은 남는다 —
    표식 둘, 예산 2콜: 나중 표식(09-22)이 2콜로 풀리고 앞 표식(09-21)에서 끝난다(델타
    리뷰 L3)."""
    f = krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    _seed_raw(_SEED, skip={"2026-09-16", "2026-09-22"})
    _pre_reopen_store(now)
    di._mark_nodata("2026-09-21", "missing", "x(시가총액은 나옴)", now - timedelta(hours=25))
    res = di.tick(now=now, budget=2)
    st = di.load_state()
    assert res["status"] == "budget" and set(st["stale"]) == _NEIGHBOURS
    assert di.load_raw("2026-09-21")["nodata"] == "missing"
    assert not [c for c in f.calls if c[1] == "20260921"], "예산 없이 물었다"
    res = di.tick(now=now + timedelta(minutes=15), budget=100)
    assert _NEIGHBOURS <= set(res["built"]) and di.load_state()["stale"] == []


def test_rebuild_obligation_is_saved_before_the_mark_is_lifted(cal, store, krx, monkeypatch):
    """표식을 지운 뒤 재구성 전에 프로세스가 죽어도(배포 재시작·watchdog) 다시 만들 기록을
    잃지 않는다 — 의무를 **먼저** 상태에 저장하고 원자료를 쓴다. 거꾸로면 표식이 없어
    재질의가 안 서고, 기록이 있어 ③ 도, 자료 없음 카드라 재확인도 건너뛰어 카드가 영영
    남았다(델타 리뷰 M1 실측)."""
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    _seed_raw(_SEED, skip={"2026-09-22"})
    _pre_reopen_store(now)
    real = di._rebuild

    def killed(*a, **k):
        raise SystemExit("restart")             # tick 의 except Exception 도 못 잡는다

    monkeypatch.setattr(di, "_rebuild", killed)
    with pytest.raises(SystemExit):
        di.tick(now=now, budget=100)
    assert di.load_raw("2026-09-22").get("nets"), "표식을 안 풀었다(재현이 안 됐다)"
    assert set(di.load_state()["stale"]) == _NEIGHBOURS, "재구성 의무를 잃었다"
    monkeypatch.setattr(di, "_rebuild", real)
    res = di.tick(now=now + timedelta(minutes=15), budget=100)
    assert _NEIGHBOURS <= set(res["built"])
    assert not di.load_record("2026-09-22").get("nodata")
    assert di.load_record("2026-09-23")["prev"] == "2026-09-22"


def test_reopen_keeps_the_mark_when_the_obligation_cannot_be_saved(cal, store, krx,
                                                                  monkeypatch):
    """의무를 상태에 남기지 못하면 표식을 그대로 둔다 — 다음 주기가 다시 묻는다(의무만
    잃는 것보다 2콜을 한 번 더 쓰는 쪽이 낫다)."""
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    _seed_raw(_SEED, skip={"2026-09-22"})
    _pre_reopen_store(now)
    at0 = di.load_raw("2026-09-22")["at"]
    monkeypatch.setattr(di, "_save_state", lambda st: False)
    res = di.tick(now=now, budget=100)
    raw = di.load_raw("2026-09-22")
    assert raw.get("nodata") == "missing" and raw["at"] == at0, "저장 못 한 채 표식을 지웠다"
    assert di.load_record("2026-09-22")["nodata"]
    assert not _NEIGHBOURS & set(res["built"]), "남기지 못한 의무로 기록을 다시 만들었다"


def test_stale_rebuild_blocked_by_a_deferred_window_day_is_not_a_failure(cal, store, krx):
    """재구성할 기록의 창 날짜가 이미 다른 이유로 재시도 대기면 그건 재구성 실패가 아니다
    — 실패로 세면 '재시도 대기 중' 이 사유로 남고 그 기록이 늦춰지고 판정이 ⚠️ 가 된다
    (델타 리뷰 L1)."""
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    di._write_json(di._STATE, {"stale": ["2026-10-07"], "days": {"2026-10-06": {
        "n": 1, "reason": "x(시가총액은 나옴)", "kind": "empty", "traded": True,
        "first": di._iso(now - timedelta(hours=1)),
        "next_try": di._iso(now + timedelta(minutes=10))}}})
    di.tick(now=now, budget=100)
    st = di.load_state()
    assert "stale:2026-10-07" not in st.get("days", {}), "대기 중인 창 날짜를 재구성 실패로 셌다"
    assert st["stale"] == ["2026-10-07"], "재구성 대상을 버렸다"


def test_rebuild_of_the_oldest_span_day_keeps_a_full_window_despite_krx_holidays(
        cal, store, krx):
    """창 범위 맨 앞 기록도 5일 창을 다 가진다 — 한 주기가 보는 거래일 여유가 2일이면
    KRX 휴장 표식 셋에 그 기록을 다시 만들 때 연속 목록이 판정 불가로 바뀐다(델타 리뷰
    L5)."""
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 21, 0)
    hol = ("2026-10-01", "2026-10-07", "2026-10-08")
    for d in hol:
        di._mark_nodata(d, "holiday", "x", now)         # 방금 찍음 — 재질의 안 함
    sess = [d for d in SESS if d <= "2026-10-12" and d not in hol]
    x = sess[-di.SPAN_N]
    di._write_json(di._STATE, {"stale": [x]})
    res = di.tick(now=now, budget=200)
    assert x in res["built"]
    rec = di.load_record(x)
    assert len(rec["window"]) == di.STREAK_N and not rec["streak_missing"]
    assert rec["tiers"][0]["lists"]["streak"] is not None


def test_waiting_state_of_an_older_target_clears_when_budget_runs_out(cal, store, krx):
    """예산이 끝난 주기에 집계 대기를 남기는 건 **지금 기준일**의 대기일 때만이다 —
    앞 기준일의 대기를 남기면 어제 사유가 새 기준일의 ⏳ 줄에 붙는다(델타 리뷰 L2)."""
    krx({d: _day() for d in SESS},
        ready=set(SESS) - {"2026-10-01", "2026-10-02", "2026-10-12"})
    now = _at("2026-10-12", 22, 0)
    for d in ("2026-10-01", "2026-10-02"):
        di._mark_nodata(d, "missing", "x", now - timedelta(hours=25))
    di._write_json(di._STATE, {"waiting": True, "wait_date": "2026-10-08", "wait_n": 3,
                               "reason": "2026-10-08 기관 순매수가 아직 비었다",
                               "next_try": di._iso(now - timedelta(minutes=1))})
    res = di.tick(now=now, budget=2)
    st = di.load_state()
    assert res["status"] == "budget"
    assert st["waiting"] is False and "wait_date" not in st and not st["reason"]


def test_stale_outside_the_window_is_dropped_not_built(cal, store, krx):
    """창 밖으로 밀린 재구성 대상은 버린다 — 거래일 목록에 없는 날을 만들려 들면 주기가
    통째로 예외로 끝난다. 상태 파일의 엉뚱한 값도 주기를 깨지 않는다."""
    krx({d: _day() for d in SESS})
    di._write_json(di._STATE, {"stale": [1, "2026-08-24"]})
    res = di.tick(now=_at("2026-10-12", 21, 0), budget=100)
    assert res["status"] == "ok" and di.load_state()["stale"] == []
    assert "2026-08-24" not in res["built"] and not di.load_record("2026-08-24")


def test_waiting_state_survives_a_tick_spent_on_reopening_marks(cal, store, krx):
    """표식 재질의가 예산을 다 써 기준일을 묻지 못한 주기 — 집계 대기는 그대로다. 지우면
    화면이 ⏳ 대신 ⚠️ 를 말하고 고정 재시도 횟수가 0 으로 돌아간다(델타 리뷰)."""
    krx({d: _day() for d in SESS},
        ready=set(SESS) - {"2026-10-01", "2026-10-02", "2026-10-12"})
    now = _at("2026-10-12", 22, 0)
    for d in ("2026-10-01", "2026-10-02"):
        di._mark_nodata(d, "missing", "x", now - timedelta(hours=25))
    di._write_json(di.record_path("2026-10-08"), _records()[1])
    st0 = {"waiting": True, "wait_date": "2026-10-12", "wait_n": 3,
           "reason": "2026-10-12 기관 순매수가 아직 비었다(시가총액은 나옴)",
           "next_try": di._iso(now - timedelta(minutes=1))}
    di._write_json(di._STATE, st0)
    res = di.tick(now=now, budget=2)
    st = di.load_state()
    assert res["status"] == "budget" and res["reason"] == st0["reason"]
    assert st["waiting"] is True and st["wait_n"] == 3 and st["wait_date"] == "2026-10-12"
    assert st["reason"] == st0["reason"] and not st.get("next_try") and st["fails"] == 0
    assert di.status_verdict("2026-10-08", now, st)[0] == "wait"


def test_waiting_state_clears_once_the_target_is_built_even_if_budget_runs_out(cal, store,
                                                                                krx):
    krx({d: _day() for d in SESS})
    now = _at("2026-10-12", 22, 0)
    di._write_json(di._STATE, {"waiting": True, "wait_date": "2026-10-12", "wait_n": 3,
                               "reason": "집계 대기",
                               "next_try": di._iso(now - timedelta(minutes=1))})
    res = di.tick(now=now, budget=12)       # 기준일 11콜 뒤 다음 날짜에서 예산이 끝난다
    st = di.load_state()
    assert "2026-10-12" in res["built"] and res["status"] == "budget"
    assert st["waiting"] is False and "wait_date" not in st and not st["reason"]


def test_verdict_skips_krx_holidays_for_expected_and_behind(cal, store):
    """KRX 가 휴장으로 답한 날은 '받아야 할 날' 에서도 '뒤처진 날' 수에서도 뺀다 —
    안 빼면 그 뒤로 화면·감사가 거짓 ⚠️·❌ 다(델타 리뷰)."""
    now = _at("2026-10-13", 10, 0)
    assert di.status_verdict("2026-10-08", now)[0] == "behind"
    di._mark_nodata("2026-10-12", "holiday", "x", now)
    assert di.status_verdict("2026-10-08", now)[0] == "ok"
    di._mark_nodata("2026-10-08", "holiday", "x", now)
    kind, line = di.status_verdict("2026-10-06", now)
    assert kind == "behind" and "1거래일 뒤처짐" in line and "기록이 아직 없습니다" in line
    assert "2026-10-07 기록이 아직 없습니다" in line
    # 사이에 낀 휴장 표식도 뒤처진 날 수에서 뺀다(10-07 휴장 → 10-08·10-12 두 날)
    for p in (store / "raw").glob("*.json"):
        p.unlink()
    di._mark_nodata("2026-10-07", "holiday", "x", now)
    assert "(마지막 기록 2026-10-06 · 2거래일 뒤처짐)" in di.status_verdict("2026-10-06", now)[1]


def test_waiting_line_shows_next_try_only_past_the_fixed_retries(cal):
    nxt = "2026-10-12T22:15:00+09:00"
    base = {"waiting": True, "reason": "집계 전", "next_try": nxt}
    kind, line = di.status_verdict("2026-10-08", _at("2026-10-12", 22, 0),
                                   {**base, "wait_n": di.WAIT_MAX})
    assert kind == "wait" and "15분마다 다시 묻는 중" in line and "다음 시도" not in line
    kind, line = di.status_verdict("2026-10-08", _at("2026-10-12", 22, 0),
                                   {**base, "wait_n": di.WAIT_MAX + 1})
    assert kind == "wait" and "다음 시도 10-12 22:15" in line
    # 대기 유예를 넘긴 뒤(behind) 꼬리도 같은 규칙
    late = _at("2026-10-13", 9, 0)
    assert "다음 시도" not in di.status_verdict("2026-10-08", late,
                                              {**base, "wait_n": 3})[1]
    assert "다음 시도 10-12 22:15" in di.status_verdict(
        "2026-10-08", late, {**base, "wait_n": di.WAIT_MAX + 1})[1]


def test_stub_cards_are_not_the_latest_record(cal):
    """'자료 없음' 카드는 받은 게 아니라 못 받은 사실 — 최신으로 세면 순매수가 통째로
    죽어도 화면·감사·진단이 '최신' 이다(델타 리뷰)."""
    from bot.scripts.board_audit import _daily_inst_line
    recs = _records()
    stub = di.nodata_record("2026-10-12", "KRX 가 그날 기관 순매수를 주지 않음",
                            "2026-10-13T21:00:00+09:00")
    assert di.latest_real(recs + [stub]) == "2026-10-08"
    assert di.latest_real([stub]) is None
    now = _at("2026-10-13", 10, 0)
    html = di.render_page(recs + [stub], {}, now)
    i = html.index('class="di-status"')
    assert "2026-10-12 기록이 아직 없습니다" in html[i: html.index("</div>", i)]
    assert "<div class='v'>2026-10-08</div>" in html and "KRX 자료 없음 1일 별도" in html
    assert "<div class='v'>2거래일</div>" in html, "자료 없음 카드를 기록 수에 셌다"
    assert _daily_inst_line(recs + [stub], {}, now).lstrip().startswith("❌")
    assert "기록 2거래일" in _daily_inst_line(recs + [stub], {}, now)


def test_why_counts_real_records_only(cal, store, monkeypatch, capsys):
    monkeypatch.setattr(di, "_pykrx_stock", lambda: (_ for _ in ()).throw(AssertionError("받았다")))
    monkeypatch.setattr(di, "_now", lambda now=None: _at("2026-10-13", 21, 0))
    di._write_json(di.record_path("2026-10-08"), _records()[1])
    di._write_json(di.record_path("2026-10-12"),
                   di.nodata_record("2026-10-12", "x", "2026-10-13T21:00:00+09:00"))
    assert di.why() == 1
    out = capsys.readouterr().out
    assert "최신 2026-10-08" in out and "KRX 자료 없음 1일 별도" in out
    # '최신 기록' 은 판정이 세는 그 기록이다 — 자료 없음 카드를 찍으면 다른 날을 말한다
    assert "   최신 기록: " + di._day_meta(_records()[1]) + "\n" in out
    assert "마지막 카드는 KRX 자료 없음(2026-10-12): x" in out


def test_why_names_the_expected_session_after_a_krx_holiday(cal, store, monkeypatch, capsys):
    """③ 은 판정과 같은 '받아야 할 날' 을 찍는다 — 달력 기준만 찍으면 KRX 가 휴장으로
    답한 날 뒤에 판정 줄과 다른 날짜를 말한다(델타 리뷰). 뺀 날은 **전부** 적는다 —
    달력 기준일만 적으면 연달아 휴장으로 답한 날 중 마지막 하나만 말한다(델타 리뷰 L4)."""
    monkeypatch.setattr(di, "_pykrx_stock", lambda: (_ for _ in ()).throw(AssertionError("받았다")))
    now = _at("2026-10-13", 21, 0)
    monkeypatch.setattr(di, "_now", lambda now=None: _at("2026-10-13", 21, 0))
    di._write_json(di.record_path("2026-10-08"), _records()[1])
    di._mark_nodata("2026-10-12", "holiday", "x", now)
    di._mark_nodata("2026-10-13", "holiday", "x", now)
    assert di.why() == 0
    out = capsys.readouterr().out
    assert ("③ 지금 받아야 할 마지막 거래일: 2026-10-08(달력 기준 2026-10-12, 2026-10-13 은 "
            "KRX 가 휴장으로 답해 뺐다)") in out
    # 휴장 표식이 없으면 덧붙이지 않는다
    for p in (store / "raw").glob("*.json"):
        p.unlink()
    di.why()
    out = capsys.readouterr().out
    assert "③ 지금 받아야 할 마지막 거래일: 2026-10-13 · 그날을 요구하는" in out


def test_regenerate_ignores_the_clock_and_rewrites_a_lost_page(monkeypatch, tmp_path,
                                                             store, cal):
    from bot import dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path)
    clock = {"t": _at("2026-10-12", 10, 0)}
    monkeypatch.setattr(di, "_now", lambda now=None: clock["t"])
    assert di.regenerate() is True
    out = tmp_path / "daily_inst.html"
    assert "2026-10-12 10:00 KST" in out.read_text(encoding="utf-8")
    clock["t"] = _at("2026-10-12", 10, 7)
    assert di.regenerate() is False, "생성 시각만 바뀐 페이지를 다시 썼다"
    out.unlink()                            # 서명만 남고 페이지가 사라졌다
    assert di.regenerate() is True and out.exists()


def test_tick_and_render_skips_when_another_process_holds_the_lock(monkeypatch, tmp_path,
                                                                   store, cal):
    import fcntl
    from bot import dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(di, "tick", lambda **k: calls.append(1) or {"status": "idle"})
    store.mkdir(parents=True, exist_ok=True)
    with open(store / ".tick.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        res = di.tick_and_render()
    assert res["status"] == "busy" and "다른 프로세스" in res["reason"] and calls == []
    assert (tmp_path / "daily_inst.html").exists()
    assert di.tick_and_render()["status"] == "idle" and calls == [1]


def test_krx_session_gets_a_default_timeout(monkeypatch, caplog):
    """pykrx 요청엔 timeout 이 없다 — KRX 가 응답 없이 멈추면 스레드가 안 돌아온다.
    세션에 기본 timeout 을 단 어댑터를 붙이고, 두 번 붙이지 않는다(델타 리뷰)."""
    import requests
    from requests.adapters import HTTPAdapter
    auth = pytest.importorskip("pykrx.website.comm.auth")
    sess = requests.Session()
    krxs = type("S", (), {"session": sess})()
    monkeypatch.setattr(auth, "_auth_session", krxs)
    # 세션은 전역을 직접 읽는다 — `get_auth_session()` 은 세션이 없으면 다시 로그인한다
    monkeypatch.setattr(auth, "get_auth_session",
                        lambda: (_ for _ in ()).throw(AssertionError("재로그인을 불렀다")))
    seen = []
    monkeypatch.setattr(HTTPAdapter, "send", lambda self, req, **kw: seen.append(kw) or "R")
    di._ensure_krx_timeout()
    ad = sess.get_adapter("https://data.krx.co.kr/x")
    assert ad.send(object()) == "R" and seen[-1]["timeout"] == di._KRX_TIMEOUT
    ad.send(object(), timeout=5)
    assert seen[-1]["timeout"] == 5, "호출자가 준 timeout 을 덮었다"
    di._ensure_krx_timeout()
    assert sess.get_adapter("https://data.krx.co.kr/x") is ad
    assert di._KRX_TIMEOUT[1] <= di.TICK_TIMEOUT_SEC
    # 세션이 없어도(로그인 실패) 수집을 막지 않고 재로그인도 안 부른다
    monkeypatch.setattr(auth, "_auth_session", None)
    monkeypatch.setattr(di, "_TIMEOUT_WARNED", [])
    with caplog.at_level("WARNING", logger="bot.daily_inst"):
        di._ensure_krx_timeout()
    assert not caplog.records, "세션이 없는 것은 경고할 일이 아니다"
    # 라이브러리 판이 바뀌어 전역이 사라지면 보호가 조용히 빠지지 않게 — 한 번만 경고
    monkeypatch.delattr(auth, "_auth_session")
    with caplog.at_level("WARNING", logger="bot.daily_inst"):
        di._ensure_krx_timeout()
        di._ensure_krx_timeout()
    warned = [r for r in caplog.records if "timeout 을 못 붙였다" in r.getMessage()]
    assert len(warned) == 1 and "_auth_session" in warned[0].getMessage()


def test_krx_timeout_is_reattached_per_market_after_a_mid_fetch_relogin(monkeypatch):
    """KOSPI 조회 도중 재로그인이 세션 객체를 새로 만들면 같은 `fetch_nets` 의 KOSDAQ
    조회가 timeout 없이 나갔다 — 시장마다 다시 붙인다(델타 리뷰 L6)."""
    import requests
    auth = pytest.importorskip("pykrx.website.comm.auth")
    krxs = type("S", (), {"session": requests.Session()})()
    monkeypatch.setattr(auth, "_auth_session", krxs)
    seen = {}

    class Relogin(FakeKRX):
        def get_market_net_purchases_of_equities(self, a, b, mkt, inv):
            if mkt == "KOSPI":
                krxs.session = requests.Session()      # 1시간 만료 재로그인(refresh)
            else:
                seen[mkt] = getattr(krxs.session, "_noah_timeout", False)
            return super().get_market_net_purchases_of_equities(a, b, mkt, inv)

    f = Relogin({"2026-10-12": _day()}, MCAP)
    monkeypatch.setattr(di, "_pykrx_stock", lambda: f)
    nets, _why, kind = di.fetch_nets("2026-10-12")
    assert kind == "ok" and seen == {"KOSDAQ": True}, "재로그인 뒤 KOSDAQ 조회가 무방비였다"


def test_missing_credentials_backs_off_without_calling_source(cal, store, monkeypatch):
    import bot.pykrx_client as pc
    monkeypatch.setattr(pc, "krx_login_ready", lambda: False)
    monkeypatch.setattr(di, "_pykrx_stock", lambda: (_ for _ in ()).throw(AssertionError("불렀다")))
    res = di.tick(now=_at("2026-10-12", 21, 0))
    assert res["status"] == "fail" and "KRX_ID" in res["reason"]
    assert di.load_state()["next_try"]


def test_corrupt_files_never_raise(store):
    (store / "records").mkdir(parents=True)
    (store / "records" / "2026-10-08.json").write_bytes(b"\x8d\xff{")
    store.joinpath("state.json").write_bytes(b"\x00")
    assert di.load_records() == [] and di.load_state() == {}


# ── 판정 ────────────────────────────────────────────────────────────────────
def test_status_verdict_branches(cal):
    assert di.status_verdict("2026-10-08", _at("2026-10-12", 15, 0))[0] == "ok"
    assert di.status_verdict("2026-10-08", _at("2026-10-12", 21, 29))[0] == "wait"
    kind, line = di.status_verdict("2026-10-08", _at("2026-10-12", 21, 30),
                                   {"reason": "X 실패", "next_try": "2026-10-12T22:00:00+09:00"})
    assert kind == "behind" and "X 실패" in line and "10-12 22:00" in line
    # 2세션 뒤처진 건 경계 안이어도 '받는 중' 이 아니다
    assert di.status_verdict("2026-10-07", _at("2026-10-12", 21, 0))[0] == "behind"
    assert di.status_verdict(None, _at("2026-10-12", 15, 0))[0] == "none"


def test_status_waiting_and_degraded_and_stuck(cal):
    w = {"waiting": True, "reason": "기관 순매수가 아직 비었다(시가총액은 나옴)"}
    kind, line = di.status_verdict("2026-10-08", _at("2026-10-13", 8, 0), w)
    assert kind == "wait" and "기관 순매수가 아직 비었다(시가총액은 나옴)" in line
    assert di.status_verdict("2026-10-08", _at("2026-10-13", 8, 31), w)[0] == "behind"
    d = {"days": {"2026-10-06": {"n": 2, "reason": "빈 응답"}}}
    kind, line = di.status_verdict("2026-10-12", _at("2026-10-13", 10, 0), d)
    assert kind == "degraded" and "2026-10-06 2회 실패" in line
    s = {"running_since": "2026-10-13T09:00:00+09:00"}
    kind, line = di.status_verdict("2026-10-12", _at("2026-10-13", 10, 0), s)
    assert kind == "degraded" and "끝나지 않음" in line
    assert di.status_verdict("2026-10-12", _at("2026-10-13", 9, 20), s)[0] == "ok"
    assert di.status_verdict("2026-10-12", _at("2026-10-13", 9, 29), s)[0] == "ok"
    assert di.status_verdict("2026-10-12", _at("2026-10-13", 9, 31), s)[0] == "degraded"
    # 최신이어도 전체 실패 사유가 남았으면 말한다(#41)
    kind, line = di.status_verdict("2026-10-12", _at("2026-10-13", 10, 0),
                                   {"reason": "KRX 응답 없음"})
    assert kind == "degraded" and "수집 실패: KRX 응답 없음" in line


def test_status_unknown_without_calendar(monkeypatch):
    monkeypatch.setattr(di, "settled_session", lambda now=None: None)
    assert di.status_verdict("2026-10-08")[0] == "unknown"


# ── 화면 ────────────────────────────────────────────────────────────────────
def _records():
    sessions = _want("2026-10-08")
    nbd = {d: _nets(_day()) for d in sessions}
    nbd["2026-10-08"] = _nets(_day(hynix=2e9))
    nbd["2026-10-08"]["005930.KS"][0] = "삼성<전자>"
    r1 = di.build_record("2026-10-08", sessions, nbd, MCAP,
                         collected_at="2026-10-08T20:35:00+09:00")
    r1["recheck"] = {"at": "2026-10-09T07:02:00+09:00", "changed": 2, "value_changed": 1,
                     "compared": 5, "max_abs_won": 120000000}
    r0 = di.build_record("2026-09-30", sessions, {"2026-09-30": _nets(_day())},
                         {k: v for k, v in MCAP.items() if k != "222222"},
                         collected_at="2026-09-30T20:40:00+09:00")
    return [r0, r1]


def _day_block(html, label):
    i = html.index(label)
    return html[html.rindex("<details class='di-day'", 0, i): html.index("</details>", i)]


def test_render_shows_the_three_by_three_and_reasons(cal):
    html = di.render_page(_records(), {}, _at("2026-10-12", 10, 0))
    day = _day_block(html, "2026-10-08 (목)")
    assert day.startswith("<details class='di-day' data-open='1' open>")
    assert day.count("<table class='di-tbl'>") == 9
    for _k, title in di.LISTS:
        assert title in day
    for _k, label, *_ in di.TIERS:
        assert label in day
    assert "href='lookup/005930.KS'" in day and "href='lookup/222222.KQ'" in day
    assert "삼성&lt;전자&gt;" in day and "삼성<전자>" not in day
    assert "수집 10-08 20:35" in day
    assert "재확인 10-09 07:02 · 바뀐 종목 2(최대 1.2억)" in day
    assert "조건에 맞는 종목이 2개뿐입니다" in day
    old = _day_block(html, "2026-09-30 (수)")
    # 날 블록만 자른다 — 앞 창은 9월 '월' 블록의 data-open='0' 이 대신 만족시켰다(#75)
    assert old.startswith("<details class='di-day' data-open='0'>")
    assert "직전 거래일(2026-09-29) 자료가 없어" in old and "최근 5거래일 중 4일" in old
    assert "시총 못 찾음 1종목 제외" in old
    assert "0.01%" in day and ".di-hit{" in di._DI_CSS


def test_render_meta_variants():
    base = {"date": "2026-10-12", "collected_at": "2026-10-12T20:40:00+09:00",
            "universe": 5, "tiers": []}
    assert "재확인 예정 10-13 07:00 이후" in di._day_meta({**base, "recheck": None})
    rc = {"at": "2026-10-13T07:05:00+09:00", "changed": 3, "value_changed": 0,
          "max_abs_won": 0}
    assert "바뀐 종목 3(종목 추가·삭제만)" in di._day_meta({**base, "recheck": rc})
    rc0 = {"at": "2026-10-13T07:05:00+09:00", "changed": 0, "max_abs_won": 0}
    assert di._day_meta({**base, "recheck": rc0}).count("바뀐 종목 0") == 1


def test_render_nodata_card_and_failure_reason(cal):
    stub = di.nodata_record("2026-10-06", "KRX 가 그날 기관 순매수를 주지 않음",
                            "2026-10-12T21:00:00+09:00")
    html = di.render_page([stub] + _records(), {"reason": "KRX 응답 없음"},
                          _at("2026-10-13", 10, 0))
    block = _day_block(html, "2026-10-06 (화)")
    assert "KRX 자료 없음 — KRX 가 그날 기관 순매수를 주지 않음" in block
    # 다시 묻는 범위는 '화면이 보여 주는 동안' 이 아니라 최근 14거래일이다(델타 리뷰 —
    # 화면은 120일을 보여 준다)
    assert "최근 14거래일 안에 있는 동안 24시간마다 다시 묻습니다" in block
    assert di.SPAN_N == 14 == di.KEEP_SESSIONS + di.STREAK_N - 1
    i = html.index('class="di-status"')
    assert "KRX 응답 없음" in html[i: html.index("</div>", i)], "실패 사유가 화면에 안 실림"


def test_render_caps_history_and_says_so(cal):
    stub = {"ver": 1, "nodata": "x", "collected_at": "", "tiers": [], "recheck": None}
    many = [{**stub, "date": f"2025-{m:02d}-{d:02d}"} for m in range(1, 13)
            for d in range(1, 15)]
    html = di.render_page(many, {}, _at("2026-10-13", 10, 0))
    assert html.count("<details class='di-day'") == di.RENDER_DAYS == 120
    assert f"최근 {di.RENDER_DAYS}거래일 표시" in html


def test_render_status_and_empty_page(cal):
    html = di.render_page([], {}, _at("2026-10-12", 10, 0))
    assert "아직 기록이 없습니다" in html and "20:30" in html


def test_regenerate_writes_only_when_content_changes(monkeypatch, tmp_path, store, cal):
    """15분마다 같은 페이지를 다시 쓰면 '새 업데이트' 배너가 상시 뜬다(독립 리뷰 H2)."""
    from bot import dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path)
    assert di.regenerate() is True
    out = tmp_path / "daily_inst.html"
    assert out.exists() and "Daily 기관매수" in out.read_text(encoding="utf-8")
    m1 = out.stat().st_mtime_ns
    assert di.regenerate() is False and out.stat().st_mtime_ns == m1
    di._write_json(di.record_path("2026-10-08"), _records()[1])
    assert di.regenerate() is True


def test_tick_and_render_renders_and_skips_when_busy(monkeypatch, tmp_path, store, cal):
    from bot import dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(di, "tick", lambda **k: calls.append(1) or {"status": "idle"})
    assert di.tick_and_render()["status"] == "idle" and calls == [1]
    assert (tmp_path / "daily_inst.html").exists()
    (tmp_path / "daily_inst.html").unlink()
    (tmp_path / "daily_inst.html.sig").unlink()
    di._TICK_LOCK.acquire()
    try:
        assert di.tick_and_render()["status"] == "busy" and calls == [1]
    finally:
        di._TICK_LOCK.release()
    assert (tmp_path / "daily_inst.html").exists(), "멈춘 주기 동안 재생성도 안 했다"


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_search_js_runs_and_filters_days(tmp_path):
    """검색 JS 를 **실행**한다 — 파서만 태우면 동작 결함을 못 본다(#253)."""
    js = _DOM + di._DI_JS + r"""
q.value='스피어'; q._h();
var out={d1:[d1.style.display,d1.open], d2:d2.style.display, d3:[d3.style.display,d3.open],
  m2:[m2.style.display,m2.open], st:st.textContent,
  hit:d1.kids[0].classList.contains('di-hit'), miss:d1.kids[1].classList.contains('di-hit')};
q.value='삼성전자'; q._h();
out.m2gone=m2.style.display;
q.value=''; q._h();
out.reset=[d2.style.display, d1.open, d3.open, m2.open, m2.style.display, st.textContent,
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
    assert out["m2gone"] == "none", "일치하는 날이 없는 달을 안 숨겼다"
    assert out["reset"] == ["", True, False, False, "", "총 3거래일 기록", False]


# ── 배선 ────────────────────────────────────────────────────────────────────
def _help_text():
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


def test_periodic_task_is_registered_and_shaped():
    """루프 모양까지 — create_task·import 이름만 재면 sleep 을 지우거나 수집 대신
    재생성만 부르는 변형이 통과했다(독립 리뷰)."""
    src = open("bot/telegram_bot.py", encoding="utf-8").read()
    tree = ast.parse(src)
    created = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "create_task"
               and n.args and isinstance(n.args[0], ast.Call)
               and getattr(n.args[0].func, "id", "") == "_periodic_daily_inst"]
    assert len(created) == 1
    tick = next(n for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "_DAILY_INST_TICK_SEC")
    assert ast.literal_eval(tick.value) == 900
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
              and n.name == "_periodic_daily_inst")
    loop = next(n for n in fn.body if isinstance(n, ast.While))
    pre = ast.unparse(ast.Module(body=fn.body[:fn.body.index(loop)], type_ignores=[]))
    assert "asyncio.to_thread(_regen_di)" in pre, "첫 바퀴 렌더가 없다"
    body = ast.unparse(loop)
    assert "asyncio.wait_for(asyncio.to_thread(_di_tick)" in body
    assert "import tick_and_render as _di_tick" in body
    assert "await asyncio.sleep(_DAILY_INST_TICK_SEC)" in body


def test_nav_links_the_board_from_home_noah_and_daily_byte():
    from bot import dashboard as d
    src = open("bot/dashboard.py", encoding="utf-8").read()
    assert src.count('href="daily_inst.html">🏦 Daily 기관매수</a>') >= 3
    assert 'href="daily_inst.html"' in d._render_daily_byte_page([])


def test_board_audit_line_uses_the_screen_verdict(cal):
    from bot.scripts.board_audit import _daily_inst_line
    now = _at("2026-10-12", 10, 0)
    recs = _records()
    assert _daily_inst_line(recs, {}, now).lstrip().startswith("✅")
    assert _daily_inst_line([], {}, now).lstrip().startswith("❌")
    late = _at("2026-10-13", 9, 0)
    line = _daily_inst_line(recs, {"reason": "KRX 응답 없음"}, late)
    assert line.lstrip().startswith("❌") and "KRX 응답 없음" in line
    waiting = _daily_inst_line(recs, {"waiting": True}, _at("2026-10-12", 23, 0))
    assert waiting.lstrip().startswith("⚠️")
    deg = {"days": {"2026-10-06": {"n": 1, "reason": "x"}}}
    assert _daily_inst_line(recs, deg, now).lstrip().startswith("⚠️")


def test_board_audit_unknown_and_wired_into_main(monkeypatch):
    from bot.scripts import board_audit as ba
    monkeypatch.setattr(di, "settled_session", lambda now=None: None)
    assert ba._daily_inst_line([], {}).lstrip().startswith("❓")
    tree = ast.parse(open("bot/scripts/board_audit.py", encoding="utf-8").read())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    assert "_p(_daily_inst_line())" in ast.unparse(main)


def test_why_is_read_only_and_exit_code_follows_the_verdict(cal, store, monkeypatch, capsys):
    """진단은 아무것도 받지도 쓰지도 않는다(#264·#283) · 최신이면 0, 뒤처지면 1."""
    monkeypatch.setattr(di, "_pykrx_stock", lambda: (_ for _ in ()).throw(AssertionError("받았다")))
    monkeypatch.setattr(di, "_now", lambda now=None: _at("2026-10-12", 10, 0))
    di._write_json(di.record_path("2026-10-08"), _records()[1])
    di._write_json(di._STATE, {})
    before = sorted((p.name, p.stat().st_mtime_ns) for p in store.rglob("*"))
    assert di.why() == 0
    out = capsys.readouterr().out
    assert sorted((p.name, p.stat().st_mtime_ns) for p in store.rglob("*")) == before
    assert "최신 2026-10-08" in out and "소스 " in out
    # 최신이어도 실패 사유가 남았으면 degraded — 진단도 0 으로 덮지 않는다(#41)
    di._write_json(di._STATE, {"reason": "어제 실패 사유"})
    assert di.why() == 1
    assert "어제 실패 사유" in capsys.readouterr().out
    di._write_json(di._STATE, {})
    monkeypatch.setattr(di, "_now", lambda now=None: _at("2026-10-14", 10, 0))
    assert di.why() == 1


def test_probe_writes_nothing_and_counts_rows(cal, store, krx, capsys):
    krx({"2026-10-08": _day()})
    assert di.probe("2026-10-08") == 0
    out = capsys.readouterr().out
    assert "KOSPI 기관 순매수 3행" in out and "KOSDAQ 기관 순매수 2행" in out
    assert not store.exists()


def test_stat_shows_previous_recorded_day_next_to_latest(cal):
    """사용자 2026-10-14 — '최신 기준일 옆에 그 전 기준일(전에 기록된 일자)도'. 같은 '받은 기록'
    규칙으로 고른다 — 자료 없음 카드는 전 기록일이 아니다(#38)."""
    import re
    recs = _records()
    real = sorted({r["date"] for r in recs if not r.get("nodata")}, reverse=True)
    assert len(real) >= 2
    stub = {"date": "2026-10-09", "nodata": True}
    html = di.render_page(recs + [stub], {}, _at("2026-10-12", 10, 0))
    m = re.search(r"최신 기준일 · 전 기록일</div><div class='v'>(.*?)</div>", html)
    assert m and m.group(1) == f"{real[0]}<span class='prev'>· 전 {real[1]}</span>", m
    assert di.prev_real([recs[0]]) is None
    one = di.render_page([r for r in recs if r["date"] == real[0]], {}, _at("2026-10-12", 10, 0))
    assert "<span class='prev'>· 전 없음</span>" in one
