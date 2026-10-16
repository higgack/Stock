"""자산 자동 매칭의 일시 실패가 영구 미매칭으로 굳지 않게 — 사용자 2026-10-16 "ASML 홀딩(ADR)·아날로그 디바이시스·
에버스핀 테크놀로지·에흐르 테스트 시스템스 는 티커가 확실히 있는데 왜 미매칭?".

옛 판(af51e49)은 AI 호출 실패·응답 해석 실패·Yahoo 이름 조회 실패를 전부 사유 없는 영구 None 으로 구웠고, 다시 묻는
경로가 업로드뿐이었다. VM 에서 그 넷이 어느 갈래로 실패했는지는 재지 못했다(#165) — 그래서 갈래마다 사유를 남기고,
일시 실패는 시간이 지나면 다시 묻고, 저장된 자산 기록을 업로드 없이 다시 매칭한다."""
from __future__ import annotations

import json
import sys
import time
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


def test_explicit_null_is_final_but_expires(ar, monkeypatch):
    """AI 의 답은 확률적이라 '최종' 도 영구가 아니다 — _FINAL_SEC(30일) 동안만 안 묻는다(독립 리뷰 H1)."""
    calls: list = []
    _llm(monkeypatch, {"SK에코플랜트": None}, calls)
    _yf(monkeypatch, {})
    ar.batch_resolve(["SK에코플랜트"], now=1.0)
    v = ar._load()["sk에코플랜트"]
    assert v["final"] is True and "식별 못 함" in v["why"]
    ar.batch_resolve(["SK에코플랜트"], now=1.0 + 10 * ar._RETRY_SEC)
    assert len(calls) == 1                                     # 6시간 간격으론 다시 안 묻는다
    ar.batch_resolve(["SK에코플랜트"], now=1.0 + ar._FINAL_SEC)
    assert len(calls) == 2
    assert ar._FINAL_SEC >= 7 * 86400                          # 최종 실패가 일시 실패처럼 자주 묻히지 않게


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
    st, why = ar.entry_state({"why": "x", "final": True, "ts": 0.0}, now=10.0)
    assert st == "final" and why.startswith("x · 다음 재시도")
    assert ar.entry_state({"why": "x", "final": True, "ts": 0.0}, now=ar._FINAL_SEC)[0] == "ask"
    assert ar.entry_state({"why": "x", "final": True})[0] == "ask"      # 시각 없는 최종 = 판정 근거 없음
    assert ar.entry_state({"why": "x", "final": False})[0] == "ask"
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
    ar._CACHE_PATH.write_text(json.dumps({"아날로그디바이시스": {"why": "w", "final": True, "ts": time.time()},
                                          "sk에코플랜트": {"why": "w2", "final": True, "ts": time.time()}}, ensure_ascii=False), "utf-8")
    _llm(monkeypatch, RuntimeError("must not be called"))
    assert pf.rematch_unmatched() == 0


def test_summary_lists_the_reason(pf, ar):
    ar._CACHE_PATH.write_text(json.dumps({"sk에코플랜트": {"why": "AI: 식별 못 함", "final": True, "ts": time.time()}},
                                         ensure_ascii=False), "utf-8")
    holdings = [{"상품명": "SK에코플랜트", "matched": False, "source": None},
                {"상품명": "에버스핀 테크놀로지", "matched": False, "source": None}]
    why = pf._unmatched_why(holdings)
    txt = pf.format_summary_text({"unmatched_names": [h["상품명"] for h in holdings], "unmatched_why": why})
    assert any(x.startswith("  · SK에코플랜트 — AI: 식별 못 함 · 다음 재시도") for x in txt.splitlines())
    assert "  · 에버스핀 테크놀로지 — 아직 AI 매칭을 묻지 않음" in txt.splitlines()


def test_build_model_records_reasons(pf, ar, monkeypatch):
    _llm(monkeypatch, RuntimeError("503"))
    _yf(monkeypatch, {})
    unres = {"ticker": None, "market": None, "matched": False, "source": None}
    m = pf.build_model({"holdings": [{"상품명": "에버스핀 테크놀로지", "평가금액": 1.0, "투자원금": 1.0}]},
                       resolve=lambda n: dict(unres))
    assert "AI 호출 실패" in m["unmatched_why"]["에버스핀 테크놀로지"]


def test_watch_run_rematches_even_when_telegram_fails(monkeypatch):
    """재매칭은 텔레그램 쪽 결과와 무관하게 돈다 — 세션 잠금으로 일찍 끝나거나 예외가 나도(독립 리뷰 M3).
    (옛 판은 `_run_async` 안의 '불리나' 만 AST 로 쟀다 — 조기 반환 경로를 못 봤다, #20)"""
    import bot.portfolio_watch as w
    calls = []
    monkeypatch.setattr(w, "_rematch", lambda: calls.append(1))

    async def early():
        return 0
    monkeypatch.setattr(w, "_run_async", early)
    assert w.run() == 0 and calls == [1]

    async def boom():
        raise RuntimeError("session locked")
    monkeypatch.setattr(w, "_run_async", boom)
    with pytest.raises(RuntimeError):
        w.run()
    assert calls == [1, 1]


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
    ar._CACHE_PATH.write_text(json.dumps({"sk에코플랜트": {"why": "AI: 식별 못 함", "final": True, "ts": time.time()}},
                                         ensure_ascii=False), "utf-8")
    _llm(monkeypatch, RuntimeError("must not be called"))
    before = ar._CACHE_PATH.read_text("utf-8")
    lines: list = []
    assert ar.why(out=lines.append) == 0
    assert any(x.startswith("  · SK에코플랜트 — ⛔ 최종: AI: 식별 못 함 · 다음 재시도") for x in lines)
    assert "  · 아날로그 디바이시스 — 🔁 다음 재매칭에 묻는다: 아직 AI 매칭을 묻지 않음" in lines
    assert ar._CACHE_PATH.read_text("utf-8") == before


def test_short_names_verify_by_core_name():
    """신원 낱말(3자+)이 없는 짧은 이름은 일반 낱말을 뺀 이름 전체로 본다(독립 리뷰 H1: AT&T·HP·3M 이 늘 '다른 회사')."""
    from bot.portfolio_resolve import same_company
    assert same_company("AT&T", "AT&T Inc.") and same_company("HP", "HP Inc.") and same_company("3M", "3M Company")
    assert not same_company("HP", "HPE Inc.")
    assert not same_company("SK Ecoplant", "SK hynix Inc.")
    assert not same_company("", "") and not same_company("Inc", "Inc")


def test_mismatch_reason_does_not_claim_the_names_differ(ar, monkeypatch):
    _llm(monkeypatch, {"구글": {"ticker": "GOOGL", "company": "Google"}})
    _yf(monkeypatch, {"GOOGL": "Alphabet Inc."})
    ar.batch_resolve(["구글"], now=1.0)
    v = ar._load()["구글"]
    assert v["final"] is True and "신원 낱말이 겹치지 않음" in v["why"] and "≠" not in v["why"]


@pytest.mark.parametrize("ans,names,needle", [
    ({"가가": {"ticker": "ZZZ", "company": "Zeta"}}, None, "시세 없음"),
    ({"가가": {"ticker": "ZZZ", "company": "Zeta"}}, "boom", "Yahoo 확인 실패"),
    ({"가가": {"ticker": "BAD TICK", "company": "Zeta"}}, {}, "모양이 이상함"),
    ({"가가": "ZZZ"}, {}, "형식이 다름"),
])
def test_transient_branches_are_not_final(ar, monkeypatch, ans, names, needle):
    _llm(monkeypatch, ans)
    fake = types.ModuleType("yfinance")

    class _T:
        def __init__(self, t):
            if names == "boom":
                raise RuntimeError("x")
            self.fast_info = types.SimpleNamespace(last_price=None if names is None else 1.0, previous_close=None)

        def get_info(self):
            return {}
    fake.Ticker = _T
    fake.Search = lambda *a, **k: types.SimpleNamespace(quotes=[])
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    ar.batch_resolve(["가가"], now=3.0)
    v = ar._load()["가가"]
    assert needle in v["why"] and v["final"] is False


def test_non_us_ticker_is_not_stored_as_us(ar, monkeypatch):
    _llm(monkeypatch, {"도요타": {"ticker": "7203.T", "company": "Toyota Motor"}})
    _yf(monkeypatch, {"7203.T": "Toyota Motor Corporation"})
    assert ar.batch_resolve(["도요타"], now=1.0) == {"도요타": None}
    assert "미국 밖" in ar._load()["도요타"]["why"]


def test_search_fallback_only_trusts_the_same_symbol(ar, monkeypatch):
    """검색 결과의 **다른 심볼** 이름으로 신원을 확인하면 남의 회사가 붙는다(독립 리뷰 M4)."""
    _llm(monkeypatch, {"가가": {"ticker": "ZZZ", "company": "Synopsys"}})
    _yf(monkeypatch, {}, info_fails=True, search={"ZZZ": [{"symbol": "SNPS", "longname": "Synopsys, Inc."}]})
    assert ar.batch_resolve(["가가"], now=1.0) == {"가가": None}


def test_numbered_answer_keys_are_matched(ar, monkeypatch):
    _llm(monkeypatch, {"1. 아날로그 디바이시스": {"ticker": "ADI", "company": "Analog Devices"}})
    _yf(monkeypatch, {"ADI": "Analog Devices, Inc."})
    assert ar.batch_resolve(["아날로그 디바이시스"]) == {"아날로그디바이시스": ("ADI", "US")}


def test_duplicate_spellings_are_asked_once(ar, monkeypatch):
    seen: list = []
    _llm(monkeypatch, {}, seen)
    _yf(monkeypatch, {})
    ar.batch_resolve(["ASML 홀딩(ADR)", "ASML 홀딩"], now=1.0)
    assert seen and seen[0].count("ASML 홀딩") == 1


def test_batch_is_recorded_before_the_ai_call(ar, monkeypatch):
    """묻기 전에 '조회 중' 을 남긴다 — 프로세스가 중간에 죽으면 다음 주기가 곧바로 다시 묻지 않게(독립 리뷰 H2)."""
    import bot.screener as sc

    def die(*a, **k):
        raise SystemExit("killed by unit timeout")
    monkeypatch.setattr(sc, "_call_pro", die)
    with pytest.raises(SystemExit):
        ar.batch_resolve(["아날로그 디바이시스"], now=50.0)
    disk = json.loads(ar._CACHE_PATH.read_text("utf-8"))
    assert disk["아날로그디바이시스"]["final"] is False and "중단" in disk["아날로그디바이시스"]["why"]
    calls: list = []
    _llm(monkeypatch, {}, calls)
    monkeypatch.setattr(ar, "_cache", None)
    ar.batch_resolve(["아날로그 디바이시스"], now=60.0)
    assert calls == []


def test_verify_budget_leaves_the_rest_transient(ar, monkeypatch):
    _llm(monkeypatch, {"가가": {"ticker": "AAA", "company": "Alpha"}, "나나": {"ticker": "BBB", "company": "Beta"}})
    _yf(monkeypatch, {"AAA": "Alpha Inc.", "BBB": "Beta Inc."})
    clock = iter([0.0, 0.0, 1e9, 1e9])
    monkeypatch.setattr(ar.time, "monotonic", lambda: next(clock))
    out = ar.batch_resolve(["가가", "나나"], now=1.0)
    assert out == {"가가": ("AAA", "US"), "나나": None}
    assert "예산" in ar._load()["나나"]["why"] and ar._load()["나나"]["final"] is False


def test_each_verification_is_saved(ar, monkeypatch):
    """확인 하나마다 디스크에 남긴다 — 두 번째 확인 중에 죽어도 첫 번째는 남는다(H2)."""
    _llm(monkeypatch, {"가가": {"ticker": "AAA", "company": "Alpha"}, "나나": {"ticker": "BBB", "company": "Beta"}})
    fake = types.ModuleType("yfinance")

    class _T:
        def __init__(self, t):
            if t == "BBB":
                raise SystemExit("killed")
            self.fast_info = types.SimpleNamespace(last_price=1.0, previous_close=1.0)

        def get_info(self):
            return {"longName": "Alpha Inc."}
    fake.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    with pytest.raises(SystemExit):
        ar.batch_resolve(["가가", "나나"], now=1.0)
    assert json.loads(ar._CACHE_PATH.read_text("utf-8"))["가가"] == ["AAA", "US"]


def test_miss_reason_branches(ar, monkeypatch):
    assert ar.miss_reason("A") == "이름이 너무 짧아 AI 매칭을 묻지 않음"
    monkeypatch.setattr(ar, "_effective_key", lambda: "")
    assert ar.miss_reason("에버스핀 테크놀로지") == "AI 키가 없어 매칭을 묻지 못함"
    monkeypatch.setattr(ar, "_effective_key", lambda: "k")
    assert ar.miss_reason("에버스핀 테크놀로지") == "아직 AI 매칭을 묻지 않음"
    ar._CACHE_PATH.write_text(json.dumps({"에버스핀테크놀로지": ["MRAM", "US"]}), "utf-8")
    monkeypatch.setattr(ar, "_cache", None)
    assert ar.miss_reason("에버스핀 테크놀로지") == ""


def test_rematch_persists_a_reason_only_change(pf, ar, monkeypatch):
    """붙은 종목이 없어도 사유가 바뀌면 남긴다(`if fixed:` 로 줄이면 텔레그램 요약이 낡은 사유를 적는다)."""
    pf.PORTFOLIO_PATH.write_text(json.dumps(_model(), ensure_ascii=False), "utf-8")
    _llm(monkeypatch, RuntimeError("429"))
    _yf(monkeypatch, {})
    assert pf.rematch_unmatched(now=5.0) == 0
    m = json.loads(pf.PORTFOLIO_PATH.read_text("utf-8"))
    assert "AI 호출 실패" in m["unmatched_why"]["아날로그 디바이시스"] and "_rematch_ts" not in m


def test_rematch_does_not_revert_a_concurrent_upload(pf, ar, monkeypatch):
    """묻는 동안 새 업로드가 끼면 그것을 옛 모델로 덮지 않는다(독립 리뷰 M1)."""
    pf.PORTFOLIO_PATH.write_text(json.dumps(_model(), ensure_ascii=False), "utf-8")
    import bot.screener as sc
    newer = {**_model(), "_saved_ts": 999.0, "holdings": []}

    def ai(*a, **k):
        pf.PORTFOLIO_PATH.write_text(json.dumps(newer, ensure_ascii=False), "utf-8")
        return (json.dumps({"아날로그 디바이시스": {"ticker": "ADI", "company": "Analog Devices"}},
                           ensure_ascii=False), 1, 1)
    monkeypatch.setattr(sc, "_call_pro", ai)
    _yf(monkeypatch, {"ADI": "Analog Devices, Inc."})
    assert pf.rematch_unmatched() == 0
    assert json.loads(pf.PORTFOLIO_PATH.read_text("utf-8")) == newer


def test_rematch_marks_the_model_for_the_pnl_ledger(pf, ar, monkeypatch, tmp_path):
    """같은 업로드라도 재매칭으로 티커가 붙으면 전일대비 기준 장부가 그날 항목을 다시 적는다 — 안 그러면 내일
    그 종목이 '기준 날 뒤 새로 생긴 종목' 으로 센다(독립 리뷰 M2)."""
    import bot.holdings_news as hn
    from datetime import datetime
    monkeypatch.setattr(hn, "_base_path", lambda: tmp_path / "pnl_base.json")
    model = {**_model(), "_saved_ts": datetime(2026, 10, 16, 19, 46, tzinfo=hn._KST).timestamp()}
    for h in model["holdings"]:
        h["평가금액"] = 100.0
    now = datetime(2026, 10, 16, 22, 0, tzinfo=hn._KST)
    hn.pnl_base(now, model, bak={})
    led = json.loads((tmp_path / "pnl_base.json").read_text("utf-8"))
    assert not any("ADI" in k for k in led["2026-10-16"]["vals"])
    pf.PORTFOLIO_PATH.write_text(json.dumps(model, ensure_ascii=False), "utf-8")
    _llm(monkeypatch, {"아날로그 디바이시스": {"ticker": "ADI", "company": "Analog Devices"}, "SK에코플랜트": None})
    _yf(monkeypatch, {"ADI": "Analog Devices, Inc."})
    assert pf.rematch_unmatched(now=7.0) == 1
    m2 = json.loads(pf.PORTFOLIO_PATH.read_text("utf-8"))
    assert m2["_rematch_ts"] == 7.0 and m2["_saved_ts"] == model["_saved_ts"]
    hn.pnl_base(now, m2, bak={})
    led = json.loads((tmp_path / "pnl_base.json").read_text("utf-8"))
    assert any("ADI" in k for k in led["2026-10-16"]["vals"]) and led["2026-10-16"]["rev"] == 7.0


def test_rematch_uses_a_code_alias_before_asking_the_ai(pf, ar, monkeypatch):
    """업로드 뒤 더한 별칭은 재매칭이 네트워크 없이 붙인다 — 에흐르 '스' 판이 AI·Yahoo 429 에 막혀 있었다(2026-10-16 VM)."""
    m = _model()
    m["holdings"][1]["상품명"] = "에흐르 테스트 시스템스"
    m["unmatched_names"][0] = "에흐르 테스트 시스템스"
    pf.PORTFOLIO_PATH.write_text(json.dumps(m, ensure_ascii=False), "utf-8")
    asked = []
    _llm(monkeypatch, {"SK에코플랜트": None}, calls=asked)
    assert pf.rematch_unmatched() == 1
    h = {x["상품명"]: x for x in json.loads(pf.PORTFOLIO_PATH.read_text("utf-8"))["holdings"]}
    assert (h["에흐르 테스트 시스템스"]["ticker"], h["에흐르 테스트 시스템스"]["source"]) == ("AEHR", "alias")
    assert asked and all("에흐르" not in str(c) for c in asked)  # 별칭으로 붙은 이름은 AI 에 안 묻는다
