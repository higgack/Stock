"""자산 자동 매칭의 일시 실패가 영구 미매칭으로 굳지 않게 — 사용자 2026-10-16 "ASML 홀딩(ADR)·아날로그 디바이시스·
에버스핀 테크놀로지·에흐르 테스트 시스템스 는 티커가 확실히 있는데 왜 미매칭?".

옛 판(af51e49)은 AI 호출 실패·응답 해석 실패·Yahoo 이름 조회 실패를 전부 사유 없는 영구 None 으로 구웠고, 다시 묻는
경로가 업로드뿐이었다. VM 에서 그 넷이 어느 갈래로 실패했는지는 재지 못했다(#165) — 그래서 갈래마다 사유를 남기고,
일시 실패는 시간이 지나면 다시 묻고, 저장된 자산 기록을 업로드 없이 다시 매칭한다."""
from __future__ import annotations

import json
import sys
import types

import pytest


@pytest.fixture
def ar(monkeypatch, tmp_path):
    import bot.portfolio_auto_resolve as m
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "_CACHE_PATH", tmp_path / "aliases.json")
    monkeypatch.setattr(m, "_USAGE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(m, "_effective_key", lambda: "k")
    return m


def _yf(monkeypatch, names: dict, *, info_fails=False, search=None):
    fake = types.ModuleType("yfinance")

    class _T:
        def __init__(self, t):
            self.t = t
            self.fast_info = types.SimpleNamespace(last_price=10.0, previous_close=10.0)

        def get_info(self):
            if info_fails:
                raise RuntimeError("Invalid Crumb")
            return {"longName": names.get(self.t, "")}
    fake.Ticker = _T
    fake.Search = lambda q, max_results=6: types.SimpleNamespace(quotes=(search or {}).get(q, []))
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def _llm(monkeypatch, answer, calls=None):
    import bot.screener as sc

    def f(*a, **k):
        if calls is not None:
            calls.append(a[1] if len(a) > 1 else k.get("prompt"))
        if isinstance(answer, Exception):
            raise answer
        return (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False), 10, 10)
    monkeypatch.setattr(sc, "_call_pro", f)


def test_ai_call_failure_is_retried_after_the_wait_not_cached_forever(ar, monkeypatch):
    calls: list = []
    _llm(monkeypatch, RuntimeError("503"), calls)
    _yf(monkeypatch, {"ADI": "Analog Devices, Inc."})
    assert ar.batch_resolve(["아날로그 디바이시스"], now=1000.0) == {"아날로그디바이시스": None}
    v = json.loads(ar._CACHE_PATH.read_text("utf-8"))["아날로그디바이시스"]
    assert v["final"] is False and "AI 호출 실패(RuntimeError)" in v["why"]
    # 대기 중엔 묻지 않는다(비용 상한)
    assert ar.batch_resolve(["아날로그 디바이시스"], now=1000.0 + ar._RETRY_SEC - 1) == {"아날로그디바이시스": None}
    assert len(calls) == 1
    # 시각이 지나면 다시 묻고 이번엔 붙는다
    _llm(monkeypatch, {"아날로그 디바이시스": {"ticker": "ADI", "company": "Analog Devices"}}, calls)
    assert ar.batch_resolve(["아날로그 디바이시스"], now=1000.0 + ar._RETRY_SEC) == {"아날로그디바이시스": ("ADI", "US")}
    assert len(calls) == 2


def test_unparseable_ai_answer_is_a_transient_failure(ar, monkeypatch):
    _llm(monkeypatch, "죄송합니다, 다음과 같습니다: ...")
    _yf(monkeypatch, {})
    ar.batch_resolve(["에버스핀 테크놀로지"], now=5.0)
    v = ar._load()["에버스핀테크놀로지"]
    assert v == {"why": "AI 응답을 JSON 으로 못 읽음", "final": False, "ts": 5.0}


def test_old_reasonless_none_is_asked_again(ar, monkeypatch):
    """v2 판이 구운 사유 없는 None 은 무엇으로 실패했는지 모른다 — 한 번 다시 묻는다."""
    ar._CACHE_PATH.write_text(json.dumps({"asml홀딩": None}), "utf-8")
    _llm(monkeypatch, {"ASML 홀딩(ADR)": {"ticker": "ASML", "company": "ASML Holding"}})
    _yf(monkeypatch, {"ASML": "ASML Holding N.V."})
    assert ar.batch_resolve(["ASML 홀딩(ADR)"]) == {"asml홀딩": ("ASML", "US")}


def test_model_key_is_matched_after_normalisation(ar, monkeypatch):
    """모델이 키의 괄호·공백을 바꿔 써도 같은 종목이다 — 옛 판은 '응답에 없음' 으로 영구 None 이었다."""
    _llm(monkeypatch, {"ASML 홀딩 ADR": {"ticker": "ASML", "company": "ASML Holding"}})
    _yf(monkeypatch, {"ASML": "ASML Holding N.V."})
    assert ar.batch_resolve(["ASML 홀딩(ADR)"]) == {"asml홀딩": ("ASML", "US")}


def test_missing_key_in_answer_is_transient(ar, monkeypatch):
    _llm(monkeypatch, {"다른 종목": None})
    _yf(monkeypatch, {})
    ar.batch_resolve(["에버스핀 테크놀로지"], now=1.0)
    assert ar._load()["에버스핀테크놀로지"]["final"] is False


def test_explicit_null_is_final(ar, monkeypatch):
    calls: list = []
    _llm(monkeypatch, {"SK에코플랜트": None}, calls)
    _yf(monkeypatch, {})
    ar.batch_resolve(["SK에코플랜트"], now=1.0)
    v = ar._load()["sk에코플랜트"]
    assert v["final"] is True and "식별 못 함" in v["why"]
    ar.batch_resolve(["SK에코플랜트"], now=1.0 + 10 * ar._RETRY_SEC)
    assert len(calls) == 1                                     # 최종 실패는 다시 묻지 않는다


def test_yahoo_info_failure_falls_back_to_search_name(ar, monkeypatch):
    """`get_info` 는 Yahoo 가 자주 거절한다(Invalid Crumb) — 검색 결과의 이름으로 신원을 본다."""
    _llm(monkeypatch, {"에흐르 테스트 시스템스": {"ticker": "AEHR", "company": "Aehr Test Systems"}})
    _yf(monkeypatch, {}, info_fails=True,
        search={"AEHR": [{"symbol": "AEHR", "longname": "Aehr Test Systems"}]})
    assert ar.batch_resolve(["에흐르 테스트 시스템스"]) == {"에흐르테스트시스템스": ("AEHR", "US")}


def test_yahoo_name_unavailable_is_transient_not_a_mismatch(ar, monkeypatch):
    _llm(monkeypatch, {"데스티니 테크 100": {"ticker": "DXYZ", "company": "Destiny Tech100"}})
    _yf(monkeypatch, {}, info_fails=True, search={})
    ar.batch_resolve(["데스티니 테크 100"], now=2.0)
    v = ar._load()["데스티니테크100"]
    assert v["final"] is False and "회사 이름을 못 받음" in v["why"]


def test_no_api_key_records_nothing(ar, monkeypatch):
    monkeypatch.setattr(ar, "_effective_key", lambda: "")
    assert ar.batch_resolve(["아날로그 디바이시스"]) == {"아날로그디바이시스": None}
    assert ar._load() == {}


def test_entry_state_and_reason(ar):
    assert ar.entry_state(["ADI", "US"])[0] == "hit"
    assert ar.entry_state({"why": "x", "final": True}) == ("final", "x")
    st, why = ar.entry_state({"why": "x", "final": False, "ts": 0.0}, now=10.0)
    assert st == "wait" and "다음 재시도" in why and "KST" in why
    assert ar.entry_state({"why": "x", "final": False, "ts": 0.0}, now=ar._RETRY_SEC)[0] == "ask"
    assert ar.entry_state(None)[0] == "ask"


def test_aehr_alias_covers_the_banksalad_spelling():
    from bot.portfolio_resolve import resolve_overseas
    assert resolve_overseas("에흐르 테스트 시스템스") == ("AEHR", "US")
    assert resolve_overseas("에흐르 테스트 시스템즈") == ("AEHR", "US")


@pytest.fixture
def pf(monkeypatch, tmp_path, ar):
    import bot.portfolio as p
    monkeypatch.setattr(p, "PORTFOLIO_PATH", tmp_path / "portfolio.json")
    return p


def _model():
    return {"_saved_ts": 123.0, "matched_count": 1,
            "holdings": [{"상품명": "엔비디아", "ticker": "NVDA", "market": "US", "matched": True, "source": "alias"},
                         {"상품명": "아날로그 디바이시스", "ticker": None, "market": None, "matched": False,
                          "source": None},
                         {"상품명": "SK에코플랜트", "ticker": None, "market": None, "matched": False, "source": None}],
            "unmatched_names": ["아날로그 디바이시스", "SK에코플랜트"]}


def test_rematch_attaches_without_touching_upload_time_or_backup(pf, ar, monkeypatch):
    pf.PORTFOLIO_PATH.write_text(json.dumps(_model(), ensure_ascii=False), "utf-8")
    bak = pf.PORTFOLIO_PATH.with_suffix(".json.bak")
    bak.write_text('{"prev": true}', "utf-8")
    _llm(monkeypatch, {"아날로그 디바이시스": {"ticker": "ADI", "company": "Analog Devices"}, "SK에코플랜트": None})
    _yf(monkeypatch, {"ADI": "Analog Devices, Inc."})
    assert pf.rematch_unmatched() == 1
    m = json.loads(pf.PORTFOLIO_PATH.read_text("utf-8"))
    h = {x["상품명"]: x for x in m["holdings"]}
    assert h["아날로그 디바이시스"]["ticker"] == "ADI" and h["아날로그 디바이시스"]["matched"] is True
    assert m["_saved_ts"] == 123.0                             # 업로드 시각 그대로
    assert bak.read_text("utf-8") == '{"prev": true}'          # 전일대비 기준이 읽는 직전 업로드 그대로
    assert m["unmatched_names"] == ["SK에코플랜트"] and m["matched_count"] == 2
    assert "식별 못 함" in m["unmatched_why"]["SK에코플랜트"]


def test_rematch_is_offline_when_nothing_is_due(pf, ar, monkeypatch):
    pf.PORTFOLIO_PATH.write_text(json.dumps(_model(), ensure_ascii=False), "utf-8")
    ar._CACHE_PATH.write_text(json.dumps({"아날로그디바이시스": {"why": "w", "final": True},
                                          "sk에코플랜트": {"why": "w2", "final": True}}, ensure_ascii=False), "utf-8")
    _llm(monkeypatch, RuntimeError("must not be called"))
    assert pf.rematch_unmatched() == 0


def test_summary_lists_the_reason(pf, ar):
    ar._CACHE_PATH.write_text(json.dumps({"sk에코플랜트": {"why": "AI: 식별 못 함", "final": True}},
                                         ensure_ascii=False), "utf-8")
    holdings = [{"상품명": "SK에코플랜트", "matched": False, "source": None},
                {"상품명": "에버스핀 테크놀로지", "matched": False, "source": None}]
    why = pf._unmatched_why(holdings)
    txt = pf.format_summary_text({"unmatched_names": [h["상품명"] for h in holdings], "unmatched_why": why})
    assert "  · SK에코플랜트 — AI: 식별 못 함" in txt.splitlines()
    assert "  · 에버스핀 테크놀로지 — 아직 AI 매칭을 묻지 않음" in txt.splitlines()


def test_build_model_records_reasons(pf, ar, monkeypatch):
    _llm(monkeypatch, RuntimeError("503"))
    _yf(monkeypatch, {})
    unres = {"ticker": None, "market": None, "matched": False, "source": None}
    m = pf.build_model({"holdings": [{"상품명": "에버스핀 테크놀로지", "평가금액": 1.0, "투자원금": 1.0}]},
                       resolve=lambda n: dict(unres))
    assert "AI 호출 실패" in m["unmatched_why"]["에버스핀 테크놀로지"]


def test_watch_loop_calls_rematch():
    import ast
    import inspect
    import bot.portfolio_watch as w
    src = inspect.getsource(w._run_async)
    names = {n.func.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_rematch" in names


def test_watch_rematch_regenerates_only_when_something_attached(monkeypatch):
    import bot.portfolio as p
    import bot.portfolio_watch as w
    import bot.dashboard as d
    regen = []
    monkeypatch.setattr(d, "regenerate_portfolio_index", lambda: regen.append(1))
    monkeypatch.setattr(p, "rematch_unmatched", lambda: 0)
    w._rematch()
    assert regen == []
    monkeypatch.setattr(p, "rematch_unmatched", lambda: 2)
    w._rematch()
    assert regen == [1]


def test_why_cli_is_read_only(pf, ar, monkeypatch, capsys):
    pf.PORTFOLIO_PATH.write_text(json.dumps(_model(), ensure_ascii=False), "utf-8")
    ar._CACHE_PATH.write_text(json.dumps({"sk에코플랜트": {"why": "AI: 식별 못 함", "final": True}},
                                         ensure_ascii=False), "utf-8")
    _llm(monkeypatch, RuntimeError("must not be called"))
    before = ar._CACHE_PATH.read_text("utf-8")
    lines: list = []
    assert ar.why(out=lines.append) == 0
    assert "  · SK에코플랜트 — ⛔ 최종: AI: 식별 못 함" in lines
    assert "  · 아날로그 디바이시스 — 🔁 다음 재매칭에 묻는다: 아직 AI 매칭을 묻지 않음" in lines
    assert ar._CACHE_PATH.read_text("utf-8") == before
