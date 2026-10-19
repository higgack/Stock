"""🗞️ 보유종목 News — 저녁 보강 수집(사용자 2026-10-19 "뉴스받는건 하루에 두번으로 12시간 간격, 19:00 에도").

재는 것: 저녁 시각 뒤 한 번만 모은다 · 아침에 실린 항목은 다시 싣지 않는다 · 새 항목에 다음 번호(옛 번호 그대로) ·
새 항목이 붙은 종목만 다시 정리 · 아침 정리 보관('아침 정리' 라벨) · 창 끝이 옮겨져 다음 날 아침이 거기서 이어진다 ·
실패는 그 일만 건너뛰고 한 시간 뒤 다시 · 판정 줄이 대기·실패·멈춤을 말한다. 원천·AI 는 가짜(네트워크 0).
"""
from __future__ import annotations

import json

from tests.test_holdings_news_20261012 import (  # noqa: F401 — 픽스처는 이름으로 잡힌다
    _answer_all, _fetcher, _h, _it, _llm, _model, _t, hn, portfolio, ws_snaps)


def _two_runs(hn, portfolio, *, evening):
    """아침 07:15 수집(삼성 기사 1건 · 엔비디아 기사 1건) 뒤 `evening` 시각의 주기. 저녁 원천은 삼성에만 새 기사를
    하나 더 준다(아침 기사도 다시 준다 — 창이 겹치므로)."""
    am = _it("삼성전자 아침 소식", "2026-10-12T06:00:00+09:00", link="https://p/am")
    pm = _it("삼성전자 오후 공시", "2026-10-12T15:00:00+09:00", link="https://p/pm")
    nv = _it("엔비디아 소식", "2026-10-12T06:30:00+09:00", link="https://p/nv")
    box = {"kr": [am], "us": [nv]}

    def f(stock):
        f.calls.append(stock["key"])
        return (box["kr"], "naver", "") if stock["market"] == "KR" else (box["us"], "yfinance", "")
    f.calls = []
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    box["kr"] = [pm, am]
    llm = _llm(_answer_all)
    res = hn.tick(_t(evening), llm=llm, fetch=f, body=lambda l: "", pace=0)
    return f, llm, res


def test_evening_topup_adds_only_new_items_and_reanalyzes_only_those_stocks(hn, portfolio):
    f, llm, res = _two_runs(hn, portfolio, evening="2026-10-12T19:05")
    assert f.calls == ["KR:005930", "US:NVDA", "KR:005930", "US:NVDA"]          # 저녁에 한 번 더 모은다
    rec = hn.load_records()[0]
    st = {s["key"]: s for s in rec["stocks"]}
    sam = st["KR:005930"]
    assert [(it["n"], it["title"]) for it in sam["items"]] == [(2, "삼성전자 오후 공시"), (1, "삼성전자 아침 소식")]
    assert sam["items"][0]["topup"] == "2026-10-12T19:05+09:00" and "topup" not in sam["items"][1]
    assert sam["status"] == "ok" and "prev_pc" not in sam                    # 다시 정리를 마쳤다 — 보관본은 버린다
    assert len(st["US:NVDA"]["items"]) == 1                                  # 새 항목 없음 — 다시 정리 안 함
    assert len(llm.calls) == 1 and "key=KR:005930" in llm.calls[0] and "key=US:NVDA" not in llm.calls[0]
    assert "[1]" in llm.calls[0] and "[2]" in llm.calls[0]                   # 아침 뉴스와 함께 다시 정리
    assert rec["window"]["until"] == "2026-10-12T19:05:00+09:00"
    assert rec["topups"][0]["items"] == 1 and rec["topups"][0]["stocks"] == 1 and rec["topups"][0]["asked"] == 2
    assert rec["sources"]["naver"] == 1                                      # 원천 계수를 두 번 세지 않는다(#45)
    assert res["analyzed"] == 1
    hn.tick(_t("2026-10-12T19:20"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert len(f.calls) == 4                                                 # 하루 한 번
    html = hn.render_page(hn.load_records(), {}, _t("2026-10-12T19:30"), gen="GEN", portfolio={})
    assert "저녁 보강(19:05)" in html and "19:05 저녁 보강 새 항목 1건/1종목" in html


def test_no_topup_before_the_evening_slot(hn, portfolio):
    f, llm, _ = _two_runs(hn, portfolio, evening="2026-10-12T18:55")
    assert len(f.calls) == 2 and llm.calls == []
    assert "topups" not in hn.load_records()[0]


def test_morning_collect_after_the_evening_slot_skips_the_topup(hn):
    """봇이 낮 내내 멈춰 아침 수집이 저녁 뒤에 돌았으면 그 창이 이미 저녁까지 덮는다."""
    rec = {"date": "2026-10-12", "window": {"since": "2026-10-11T19:00:00+09:00",
                                            "until": "2026-10-12T19:30:00+09:00"}}
    assert not hn.topup_due(rec, _t("2026-10-12T19:45"))
    rec["window"]["until"] = "2026-10-12T07:15:00+09:00"
    assert hn.topup_due(rec, _t("2026-10-12T19:00")) and not hn.topup_due(rec, _t("2026-10-12T18:59"))
    assert not hn.topup_due({**rec, "date": "2026-10-11"}, _t("2026-10-12T19:30"))   # 어제 기록엔 안 붙인다
    assert not hn.topup_due({"date": "2026-10-12"}, _t("2026-10-12T19:30"))          # 창 끝을 모르면 안 한다


def test_next_morning_window_continues_from_the_evening_topup(hn, portfolio):
    f, _, _ = _two_runs(hn, portfolio, evening="2026-10-12T19:05")
    hn.tick(_t("2026-10-13T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    rec = [r for r in hn.load_records() if r["date"] == "2026-10-13"][0]
    assert rec["window"]["since"] == f"2026-10-12T{19 - hn.WINDOW_OVERLAP_H:02d}:05:00+09:00"
    # 어제 저녁에 실린 기사는 오늘 아침에 다시 싣지 않는다
    assert all(it["title"] != "삼성전자 오후 공시" for s in rec["stocks"] for it in s.get("items") or [])


def test_reanalysis_keeps_the_morning_analysis_until_it_finishes(hn, portfolio):
    """다시 정리가 일시 오류로 밀리면 대기 · 그날을 못 마치면(final) 아침 정리를 '아침 정리' 라벨로 보여 준다."""
    am = _it("삼성전자 아침 소식", "2026-10-12T06:00:00+09:00", link="https://p/am")
    pm = _it("삼성전자 오후 공시", "2026-10-12T15:00:00+09:00", link="https://p/pm")
    portfolio["m"] = _model(_h("삼성전자", "005930", "KR"))
    box = {"v": [am]}
    f = _fetcher({}, None)
    f2 = lambda s: (box["v"], "naver", "")                               # noqa: E731
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=f2, body=lambda l: "", pace=0)
    box["v"] = [pm, am]
    hn.tick(_t("2026-10-12T19:05"), llm=_llm("", why="503", kind="transient"), fetch=f2, body=lambda l: "",
            pace=0)
    s = hn.load_records()[0]["stocks"][0]
    assert s["status"] == "pending" and s["reason"] == "503"
    assert s["prev_pc"]["kind"] == "topup" and s["prev_pc"]["pros"] == [{"t": "좋은 소식", "refs": [1]}]
    assert f.calls == []
    s["reason"] = hn.TOPUP_REASON
    pend = hn._card(s)
    assert "Pros/Cons 대기 — 저녁 수집에서 새 뉴스가 붙음 — 아침 뉴스와 함께 다시 정리" in pend
    fin = hn._card(s, final=True)
    assert "아침 정리를 보여 줍니다" in fin and "호재 관점(아침 정리)" in fin and "옛 기준" not in fin


def test_requeue_still_labels_the_old_rule(hn):
    s = {"key": "KR:A", "name": "A", "ticker": "A", "market": "KR", "items": [], "status": "failed",
         "reason": "x", "prev_pc": {"pros": [{"t": "p", "refs": [1]}], "cons": [], "kind": "requeue"}}
    html = hn._card(s)
    assert "옛 기준 정리를 보여 줍니다" in html and "호재 관점(옛 기준)" in html


def test_topup_failure_waits_an_hour_and_the_verdict_says_so(hn, portfolio, monkeypatch):
    f, _, _ = _two_runs(hn, portfolio, evening="2026-10-12T07:30")
    n0 = len(f.calls)

    def boom(*a, **k):
        raise RuntimeError("원천 다운")
    monkeypatch.setattr(hn, "top_up", boom)
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    st = hn.load_state()
    assert st["topup_fail"]["at"] == "2026-10-12T19:05:00+09:00" and "원천 다운" in st["topup_fail"]["why"]
    kind, line = hn.status_verdict(hn.load_records(), _t("2026-10-12T19:10"), st)
    assert "저녁 보강 실패(RuntimeError: 원천 다운)" in line and f"{hn.TOPUP_RETRY_MIN}분 뒤 다시" in line
    calls = []
    monkeypatch.setattr(hn, "top_up", lambda *a, **k: calls.append(1))
    hn.tick(_t("2026-10-12T19:50"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert calls == []                                                       # 한 시간 안에는 다시 안 한다
    hn.tick(_t("2026-10-12T20:06"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert calls == [1] and len(f.calls) == n0
    assert "topup_fail" not in hn.load_state()


def test_verdict_waits_for_the_topup_then_flags_a_stalled_tick(hn, portfolio):
    _two_runs(hn, portfolio, evening="2026-10-12T07:30")
    recs = hn.load_records()
    kind, line = hn.status_verdict(recs, _t("2026-10-12T19:05"), {"last_tick": "2026-10-12T19:00:00+09:00"})
    assert kind == "ok" and "19:00 저녁 보강 대기(다음 주기)" in line
    kind, line = hn.status_verdict(recs, _t("2026-10-12T20:00"), {"last_tick": "2026-10-12T19:00:00+09:00"})
    assert kind == "fail" and "저녁 보강이 안 됐고" in line
    kind, line = hn.status_verdict(recs, _t("2026-10-12T18:00"), {"last_tick": "2026-10-12T17:55:00+09:00"})
    assert "저녁 보강" not in line


def test_topup_outage_is_a_failure_of_the_day(hn, portfolio):
    am = _it("삼성전자 아침 소식", "2026-10-12T06:00:00+09:00", link="https://p/am")
    portfolio["m"] = _model(*[_h(f"종목{i}", f"00000{i}", "KR") for i in range(1, 5)])
    box = {"ok": True}

    def f(stock):
        return ([am], "naver", "") if box["ok"] else ([], "", "HTTP 503")
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    box["ok"] = False
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    rec = hn.load_records()[0]
    assert rec["topups"][0]["fail"] == 4 and rec["topups"][0]["outage"]
    kind, line = hn.status_verdict([rec], _t("2026-10-12T19:10"), {"last_tick": "2026-10-12T19:05:00+09:00"})
    assert kind == "fail" and "저녁 보강 뉴스 원천을 하나도 못 받음" in line


def test_topup_on_a_stock_with_no_morning_news_just_queues_it(hn):
    rec = {"date": "2026-10-12", "window": {"since": "2026-10-11T07:15:00+09:00",
                                            "until": "2026-10-12T07:15:00+09:00"},
           "sources": {"telegram": {"matched": 0}},
           "stocks": [{"key": "KR:005930", "market": "KR", "ticker": "005930", "name": "삼성전자", "value": 1.0,
                       "items": [], "status": "empty", "tries": 0, "pros": [], "cons": [], "relevant": None,
                       "reason": ""}]}
    pm = _it("삼성전자 오후 공시", "2026-10-12T15:00:00+09:00", link="https://p/pm")
    ent = hn.top_up(rec, _t("2026-10-12T19:05"), records=[rec], model=_model(_h("삼성전자", "005930", "KR")),
                    fetch=lambda s: ([pm], "naver", ""), body=lambda l: "", pace=0, us_etf=set(), ko=lambda c: "")
    s = rec["stocks"][0]
    assert ent["items"] == 1 and s["status"] == "pending" and s["reason"] == "" and "prev_pc" not in s
    assert [it["n"] for it in s["items"]] == [1]
    assert json.dumps(rec, ensure_ascii=False)                                # 저장 가능한 모양


def test_topup_on_a_still_pending_stock_keeps_its_morning_bodies(hn, portfolio):
    """아침 정리가 아직 대기 중인 종목에 저녁 항목이 붙어도 아침 본문(사이드카)을 잃지 않는다."""
    am = _it("삼성전자 아침 소식", "2026-10-12T06:00:00+09:00", link="https://n.news.naver.com/a/1")
    pm = _it("삼성전자 오후 공시", "2026-10-12T15:00:00+09:00", link="https://n.news.naver.com/a/2")
    portfolio["m"] = _model(_h("삼성전자", "005930", "KR"))
    box = {"v": [am]}
    f = lambda s: (box["v"], "naver", "")                               # noqa: E731
    body = lambda l: "본문 " + l[-1] * 100                                # noqa: E731
    hn.tick(_t("2026-10-12T07:15"), llm=_llm("", why="503", kind="transient"), fetch=f, body=body, pace=0)
    side = json.loads(hn._bodies_path("2026-10-12").read_text(encoding="utf-8"))["KR:005930"]
    assert len(side) == 1
    box["v"] = [pm, am]
    llm = _llm("", why="503", kind="transient")
    hn.tick(_t("2026-10-12T19:05"), llm=llm, fetch=f, body=body, pace=0)
    side = json.loads(hn._bodies_path("2026-10-12").read_text(encoding="utf-8"))["KR:005930"]
    assert len(side) == 2                                                    # 아침 본문 + 저녁 본문
    assert "본문 1111" in llm.calls[0] and "본문 2222" in llm.calls[0]


def test_topup_on_a_pending_stock_only_adds_items(hn):
    """아직 정리 전인 종목 — 항목만 늘어난다. 시도 수·사유를 지우면 실패 상한(`MAX_ANALYZE_TRIES`)이 저녁마다 다시
    시작되고, 일시 오류 사유가 '저녁 수집' 으로 바뀌어 거짓 사유가 된다(#82)."""
    rec = {"date": "2026-10-12", "window": {"since": "2026-10-11T07:15:00+09:00",
                                            "until": "2026-10-12T07:15:00+09:00"},
           "sources": {"telegram": {"matched": 0}},
           "stocks": [{"key": "KR:005930", "market": "KR", "ticker": "005930", "name": "삼성전자", "value": 1.0,
                       "items": [{"n": 1, "src": "naver", "title": "아침", "k": "https://p/am",
                                  "ts": "2026-10-12T06:00+09:00"}],
                       "status": "pending", "tries": 2, "pros": [], "cons": [], "relevant": None,
                       "reason": "AI 응답을 못 읽음"}]}
    pm = _it("삼성전자 오후 공시", "2026-10-12T15:00:00+09:00", link="https://p/pm")
    hn.top_up(rec, _t("2026-10-12T19:05"), records=[rec], model=_model(_h("삼성전자", "005930", "KR")),
              fetch=lambda s: ([pm], "naver", ""), body=lambda l: "", pace=0, us_etf=set(), ko=lambda c: "")
    s = rec["stocks"][0]
    assert s["status"] == "pending" and s["tries"] == 2 and s["reason"] == "AI 응답을 못 읽음"
    assert [it["n"] for it in s["items"]] == [2, 1] and "prev_pc" not in s


# ── 독립 리뷰 반영(2026-10-19) ───────────────────────────────────────────────
def _outage_evening(hn, portfolio, n=4):
    am = _it("아침 소식", "2026-10-12T06:00:00+09:00", link="https://p/am")
    portfolio["m"] = _model(*[_h(f"종목{i}", f"00000{i}", "KR") for i in range(1, n + 1)])
    box = {"ok": True}

    def f(stock):
        f.calls.append(stock["key"])
        return ([am], "naver", "") if box["ok"] else ([], "", "HTTP 503")
    f.calls = []
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    box["ok"] = False
    return f, box


def test_outage_topup_keeps_the_window_and_retries_up_to_the_cap(hn, portfolio):
    """H1 — 원천 장애로 끝난 보강이 창 끝을 옮기면 아침 창 끝 ~ 그 시각 기사를 영영 못 모은다. 옮기지 않고 한 시간 뒤
    다시, 그날 `TOPUP_MAX_TRIES` 번까지 — 그 뒤엔 다음 날 아침 창이 그 시간대를 덮는다."""
    f, box = _outage_evening(hn, portfolio)
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    rec = hn.load_records()[0]
    assert rec["window"]["until"] == "2026-10-12T07:15:00+09:00" and rec["topups"][0]["outage"]
    kind, line = hn.status_verdict([rec], _t("2026-10-12T19:10"), {"last_tick": "2026-10-12T19:05:00+09:00"})
    assert kind == "fail" and "창을 옮기지 않음(60분 뒤 다시 · 남은 시도 2)" in line
    assert not hn.topup_due(rec, _t("2026-10-12T19:50"))
    assert hn.topup_due(rec, _t("2026-10-12T20:05"))
    for at in ("2026-10-12T20:05", "2026-10-12T21:05"):
        hn.tick(_t(at), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    rec = hn.load_records()[0]
    assert len(rec["topups"]) == 3 and not hn.topup_due(rec, _t("2026-10-12T23:00"))
    _, line = hn.status_verdict([rec], _t("2026-10-12T23:00"), {"last_tick": "2026-10-12T23:00:00+09:00"})
    assert "오늘 시도를 다 씀 — 다음 날 아침 수집이 이 시간대를 모음" in line
    box["ok"] = True
    hn.tick(_t("2026-10-13T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    nxt = [r for r in hn.load_records() if r["date"] == "2026-10-13"][0]
    assert nxt["window"]["since"] == "2026-10-12T01:15:00+09:00"                # 아침 창 끝에서 이어진다


def test_outage_retry_that_succeeds_moves_the_window(hn, portfolio):
    f, box = _outage_evening(hn, portfolio)
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    box["ok"] = True
    hn.tick(_t("2026-10-12T20:06"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    rec = hn.load_records()[0]
    assert rec["window"]["until"] == "2026-10-12T20:06:00+09:00" and not rec["topups"][-1]["outage"]
    assert not hn.topup_due(rec, _t("2026-10-12T22:00"))
    kind, _ = hn.status_verdict([rec], _t("2026-10-12T22:00"), {"last_tick": "2026-10-12T22:00:00+09:00"})
    assert kind == "ok"


def test_a_few_failed_stocks_reopen_from_the_morning_window_next_day(hn, portfolio):
    """H1 — 장애 기준 밑의 종목별 실패는 창을 옮기되 그 종목만 옛 창 끝을 남겨 다음 날 아침이 거기서 연다."""
    portfolio["m"] = _model(*[_h(f"종목{i}", f"00000{i}", "KR") for i in range(1, 6)])
    am = _it("아침 소식", "2026-10-12T06:00:00+09:00", link="https://p/am")
    noon = _it("종목1 낮 소식", "2026-10-12T10:00:00+09:00", link="https://p/noon")
    box = {"fail": False}

    def f(stock):
        if box["fail"] and stock["key"] == "KR:000001":
            return [], "", "HTTP 503"
        return ([noon, am] if stock["key"] == "KR:000001" else [am]), "naver", ""
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=lambda s: ([am], "naver", ""),
            body=lambda l: "", pace=0)
    box["fail"] = True
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    rec = hn.load_records()[0]
    assert rec["window"]["until"] == "2026-10-12T19:05:00+09:00" and not rec["topups"][0]["outage"]
    assert rec["window"]["miss"] == {"KR:000001": "2026-10-12T01:15:00+09:00"}   # 그때 열려던 창 시작
    assert rec["topups"][0]["miss"] == 1 and rec["topups"][0]["retry"] is False
    box["fail"] = False
    hn.tick(_t("2026-10-13T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    nxt = [r for r in hn.load_records() if r["date"] == "2026-10-13"][0]
    one = [s for s in nxt["stocks"] if s["key"] == "KR:000001"][0]
    assert [it["title"] for it in one["items"]] == ["종목1 낮 소식"]           # 13:05 앞 기사도 그 종목은 모은다
    assert all(not s["items"] for s in nxt["stocks"] if s["key"] != "KR:000001")


def test_card_does_not_call_unseen_evening_items_unrelated(hn):
    """M2 — 아침 정리로 되돌아간 카드에서 저녁 항목은 AI 가 본 적이 없다 — 흐리거나 '무관' 으로 세지 않는다."""
    items = [{"n": 2, "src": "naver", "title": "저녁", "ts": "2026-10-12T15:00+09:00", "topup": "2026-10-12T19:05+09:00"},
             {"n": 1, "src": "naver", "title": "아침", "ts": "2026-10-12T06:00+09:00"}]
    s = {"key": "KR:A", "name": "A", "ticker": "A", "market": "KR", "items": items, "status": "failed",
         "reason": "x", "prev_pc": {"pros": [{"t": "p", "refs": [1]}], "cons": [], "relevant": [1], "kind": "topup",
                                    "max_n": 1}}
    html = hn._card(s)
    lis = html.split("<ol>")[1].split("</ol>")[0].split("<li")[1:]
    assert "class='hn-off'" not in lis[0] and "아직 정리 안 됨" in lis[0] and "저녁 보강(19:05)" in lis[0]
    assert "아직 정리 안 됨" not in lis[1] and "무관하다고 본" not in html
    s["prev_pc"]["relevant"] = []                                            # 아침 항목을 무관하다고 봤다면 그건 센다
    assert "무관하다고 본 1건" in hn._card(s)


def test_verdict_names_the_preserved_analysis_kind(hn):
    """L1 — 실패 줄이 보관 정리의 까닭을 갈라 적는다."""
    def st(k, kind):
        return {"key": k, "name": k, "status": "failed", "reason": "r", "items": [{"n": 1}],
                "prev_pc": {"kind": kind}}
    rec = {"date": "2026-10-12", "stocks": [st("A", "topup"), st("B", "requeue")], "sources": {}}
    _, line = hn.status_verdict([rec], _t("2026-10-12T12:00"), {"last_tick": "2026-10-12T12:00:00+09:00"})
    assert "그중 1종목은 옛 기준 정리 · 1종목은 저녁 보강 전 정리 표시" in line


def test_body_reload_failure_leaves_the_stock_queued(hn):
    """L8 — 본문 다시 읽기가 던져도 항목과 '대기' 가 함께 남는다(항목만 붙고 ok 로 남으면 다시 정리되지 않는다)."""
    rec = {"date": "2026-10-12", "window": {"since": "2026-10-11T07:15:00+09:00",
                                            "until": "2026-10-12T07:15:00+09:00"},
           "sources": {"telegram": {"matched": 0}},
           "stocks": [{"key": "KR:005930", "market": "KR", "ticker": "005930", "name": "삼성전자", "value": 1.0,
                       "items": [{"n": 1, "src": "naver", "title": "아침", "k": "https://n.news.naver.com/a/1",
                                  "link": "https://n.news.naver.com/a/1", "body_chars": 300,
                                  "ts": "2026-10-12T06:00+09:00"}],
                       "status": "ok", "tries": 0, "pros": [{"t": "p", "refs": [1]}], "cons": [], "relevant": [1],
                       "reason": ""}]}
    pm = _it("삼성전자 오후 공시", "2026-10-12T15:00:00+09:00", link="https://p/pm")

    def body(link):
        if link.endswith("/a/1"):
            raise RuntimeError("boom")
        return ""
    hn.top_up(rec, _t("2026-10-12T19:05"), records=[rec], model=_model(_h("삼성전자", "005930", "KR")),
              fetch=lambda s: ([pm], "naver", ""), body=body, pace=0, us_etf=set(), ko=lambda c: "")
    s = rec["stocks"][0]
    assert s["status"] == "pending" and len(s["items"]) == 2 and s["prev_pc"]["max_n"] == 1


# ── 델타 리뷰 반영(2026-10-19) ───────────────────────────────────────────────
def test_a_stock_failing_twice_still_reopens_from_where_it_first_missed(hn, portfolio):
    """아침에 못 받고 저녁에도 못 받은 종목 — `miss` 가 이어져 다음 날 아침에 첫 구간부터 연다(한 번만 넘기면 사라진다)."""
    portfolio["m"] = _model(*[_h(f"종목{i}", f"00000{i}", "KR") for i in range(1, 6)])
    old = _it("종목1 전날 낮 소식", "2026-10-11T12:00:00+09:00", link="https://p/old")
    box = {"fail": True}

    def f(stock):
        if box["fail"] and stock["key"] == "KR:000001":
            return [], "", "HTTP 503"
        return ([old] if stock["key"] == "KR:000001" else []), "naver", ""
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    r1 = hn.load_records()[0]
    assert r1["window"]["miss"] == {"KR:000001": "2026-10-11T07:15:00+09:00"}
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert hn.load_records()[0]["window"]["miss"] == {"KR:000001": "2026-10-11T07:15:00+09:00"}   # 그대로 이어진다
    box["fail"] = False
    hn.tick(_t("2026-10-13T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    nxt = [r for r in hn.load_records() if r["date"] == "2026-10-13"][0]
    one = [s for s in nxt["stocks"] if s["key"] == "KR:000001"][0]
    assert [it["title"] for it in one["items"]] == ["종목1 전날 낮 소식"] and "miss" not in nxt["window"]


def test_topup_success_clears_the_miss(hn, portfolio):
    """아침에 못 받은 종목을 저녁 보강이 놓친 구간(전날 07:15~)부터 연다 — 저녁 창(01:15~) 밖 기사도 모은다."""
    portfolio["m"] = _model(*[_h(f"종목{i}", f"00000{i}", "KR") for i in range(1, 6)])
    noon = _it("종목1 낮 소식", "2026-10-11T20:00:00+09:00", link="https://p/noon")
    box = {"fail": True}

    def f(stock):
        if box["fail"] and stock["key"] == "KR:000001":
            return [], "", "HTTP 503"
        return ([noon] if stock["key"] == "KR:000001" else []), "naver", ""
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    box["fail"] = False
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    rec = hn.load_records()[0]
    assert "miss" not in rec["window"]
    one = [s for s in rec["stocks"] if s["key"] == "KR:000001"][0]
    assert [it["title"] for it in one["items"]] == ["종목1 낮 소식"]


def test_zero_result_market_is_reported_but_not_retried(hn, portfolio):
    """'시장 전부 0건' 은 늘 그런 시장이면 매일 세 번 다시 받게 된다 — 사실로만 적고 창은 옮긴다."""
    portfolio["m"] = _model(*[_h(f"종목{i}", f"00000{i}", "KR") for i in range(1, 5)])
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=lambda s: ([], "naver", ""), body=lambda l: "",
            pace=0)
    hn.tick(_t("2026-10-12T19:05"), llm=_llm(_answer_all), fetch=lambda s: ([], "naver", ""), body=lambda l: "",
            pace=0)
    rec = hn.load_records()[0]
    t = rec["topups"][0]
    assert t["outage"] and t["retry"] is False and rec["window"]["until"] == "2026-10-12T19:05:00+09:00"
    assert not hn.topup_due(rec, _t("2026-10-12T21:00"))
    _, line = hn.status_verdict([rec], _t("2026-10-12T21:00"), {"last_tick": "2026-10-12T21:00:00+09:00"})
    assert "창을 옮기지 않음" not in line


def test_a_late_opened_tab_still_gets_a_live_window(hn, ws_snaps, monkeypatch):
    """종가가 기한(마감 + 수집 + 재생성)을 넘겨도 아직 안 왔으면 지난 기한을 싣지 않는다 — 지금 연 탭이 한 번도 안 묻는다."""
    import bot.world_sectors as ws
    monkeypatch.setattr(ws, "market_day_status", lambda mk, d, now, state=None: "pending")
    ws_snaps("KR", "2026-10-08", [("KRX:000001", 1.0)])
    c = hn.cum_lookup(_t("2026-10-07T18:00").timestamp(), _t("2026-10-12T20:00"))({"market": "KR", "ticker": "000001"})
    assert c["live_d"] == "2026-10-12" and c["live_x"] == int(_t("2026-10-12T23:15").timestamp())


def test_second_topup_labels_the_preserved_analysis_as_the_previous_one(hn):
    rec = {"date": "2026-10-12", "window": {"since": "2026-10-11T07:15:00+09:00",
                                            "until": "2026-10-12T19:05:00+09:00"},
           "sources": {"telegram": {"matched": 0}},
           "stocks": [{"key": "KR:005930", "market": "KR", "ticker": "005930", "name": "삼성전자", "value": 1.0,
                       "items": [{"n": 2, "src": "naver", "title": "저녁1", "k": "https://p/e1",
                                  "ts": "2026-10-12T15:00+09:00", "topup": "2026-10-12T19:05+09:00"},
                                 {"n": 1, "src": "naver", "title": "아침", "k": "https://p/am",
                                  "ts": "2026-10-12T06:00+09:00"}],
                       "status": "ok", "tries": 0, "pros": [{"t": "p", "refs": [2]}], "cons": [], "relevant": [1, 2],
                       "reason": ""}]}
    e2 = _it("삼성전자 밤 공시", "2026-10-12T19:30:00+09:00", link="https://p/e2")
    hn.top_up(rec, _t("2026-10-12T20:06"), records=[rec], model=_model(_h("삼성전자", "005930", "KR")),
              fetch=lambda s: ([e2], "naver", ""), body=lambda l: "", pace=0, us_etf=set(), ko=lambda c: "")
    s = rec["stocks"][0]
    assert s["prev_pc"]["evening"] is True and s["prev_pc"]["max_n"] == 2
    s.update(status="failed", reason="x")
    html = hn._card(s)
    assert "직전 정리를 보여 줍니다" in html and "호재 관점(직전 정리)" in html and "아침 정리" not in html


def test_verdict_sums_all_topups_and_says_about_misses(hn):
    rec = {"date": "2026-10-12", "stocks": [{"key": "A", "status": "ok", "items": [{"n": 1}]}], "sources": {},
           "window": {"until": "2026-10-12T20:06:00+09:00", "miss": {"A": "2026-10-12T01:15:00+09:00"}},
           "topups": [{"at": "2026-10-12T19:05+09:00", "items": 30, "stocks": 10, "outage": "x", "retry": True},
                      {"at": "2026-10-12T20:06+09:00", "items": 2, "stocks": 1, "outage": "", "retry": False,
                       "miss": 1}]}
    _, line = hn.status_verdict([rec], _t("2026-10-12T21:00"), {"last_tick": "2026-10-12T21:00:00+09:00"})
    assert "20:06 저녁 보강 새 항목 32건/11종목(2회)" in line
    assert "원천을 못 받은 1종목은 다음 수집이 놓친 구간부터 다시 모음" in line
