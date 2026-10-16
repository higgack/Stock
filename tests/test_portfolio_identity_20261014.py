"""자산 기록 자동 매칭의 신원 확인 — 사용자 2026-10-14 "SK에코플랜트는 한국종목이야".

국내 비상장 SK에코플랜트가 보유종목 News 에 미국 'SKHY' 로 실렸다(원화 환율까지 곱해서). 자동 매칭 두 단
(yfinance 검색 · AI 번역)이 '값이 있는 티커' 면 통과시켜서다 — 그 티커의 원천 이름이 찾던 회사인지 본다.
어느 단이 그 오매칭을 냈는지는 VM 기록을 못 봐 단정하지 않는다(#165) — 두 단 다 같은 규칙으로 막는다."""
from __future__ import annotations

import json
import sys
import types

import pytest


@pytest.fixture
def pr(monkeypatch):
    import bot.portfolio_resolve as m
    store: dict = {}
    monkeypatch.setattr(m, "_naver_cache_load", lambda: store)
    monkeypatch.setattr(m, "_naver_cache_save", lambda d: None)
    return m


def _yf_search(monkeypatch, quotes):
    fake = types.ModuleType("yfinance")
    fake.Search = lambda q, max_results=6: types.SimpleNamespace(quotes=quotes)
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_same_company_needs_a_shared_identity_word(pr):
    assert pr.same_company("Synopsys", "Synopsys, Inc.")
    assert not pr.same_company("SK Ecoplant", "SK hynix Inc.")             # 'SK'(2자)·Inc 만으론 같은 회사가 아니다
    assert not pr.same_company("Corning Inc", "Apple Inc.")                 # 일반 낱말(inc)은 신원이 아니다
    assert not pr.same_company("", "Synopsys, Inc.") and not pr.same_company("Synopsys", "")


def test_yfinance_search_does_not_attach_a_us_ticker_to_a_hangul_name(pr, monkeypatch):
    _yf_search(monkeypatch, [{"quoteType": "EQUITY", "symbol": "SKHY", "exchange": "PCX", "longname": "SK Hynix"}])
    assert pr.resolve_via_yfinance("SK에코플랜트") is None
    # 한글이 섞인 이름은 영문 낱말이 겹쳐도 미국 티커로 받지 않는다(국내 회사의 미국 OTC 표기가 붙는다)
    _yf_search(monkeypatch, [{"quoteType": "EQUITY", "symbol": "SSNLF", "exchange": "OEM",
                              "longname": "Samsung Electronics Co., Ltd."}])
    assert pr.resolve_via_yfinance("Samsung 전자") is None


def test_yfinance_search_keeps_korean_listings_for_hangul_names(pr, monkeypatch):
    _yf_search(monkeypatch, [{"quoteType": "EQUITY", "symbol": "039030.KQ", "exchange": "KOE"}])
    assert pr.resolve_via_yfinance("이오테크닉스") == ("039030.KQ", "KR")


def test_yfinance_search_latin_name_needs_the_same_company(pr, monkeypatch):
    _yf_search(monkeypatch, [{"quoteType": "EQUITY", "symbol": "XYZ", "exchange": "NMS", "longname": "Other Co"},
                             {"quoteType": "EQUITY", "symbol": "SNPS", "exchange": "NMS",
                              "longname": "Synopsys, Inc."}])
    assert pr.resolve_via_yfinance("SYNOPSYS") == ("SNPS", "US")
    _yf_search(monkeypatch, [{"quoteType": "EQUITY", "symbol": "XYZ", "exchange": "NMS", "longname": "Other Co"}])
    assert pr.resolve_via_yfinance("FERMI AMERICA") is None


def test_yfinance_search_cache_key_drops_the_unverified_era(pr, monkeypatch):
    """옛 판 키(`yf:`)에 구워진 오매칭을 다시 쓰지 않는다 — 신원 확인 판은 다른 키(#18)."""
    store = {"yf:sk에코플랜트": {"ts": 9e12, "v": ["SKHY", "US"]}}
    monkeypatch.setattr(pr, "_naver_cache_load", lambda: store)
    _yf_search(monkeypatch, [])
    assert pr.resolve_via_yfinance("SK에코플랜트") is None


@pytest.fixture
def ar(monkeypatch, tmp_path):
    import bot.portfolio_auto_resolve as m
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(m, "_CACHE_PATH", tmp_path / "aliases.json")
    monkeypatch.setattr(m, "_USAGE", tmp_path / "usage.jsonl")
    monkeypatch.setattr(m, "_effective_key", lambda: "k")
    return m


def _llm(monkeypatch, answer: dict, names: dict):
    import bot.screener as sc
    monkeypatch.setattr(sc, "_call_pro", lambda *a, **k: (json.dumps(answer, ensure_ascii=False), 10, 10))
    fake = types.ModuleType("yfinance")

    class _T:
        def __init__(self, t):
            self.t = t
            self.fast_info = types.SimpleNamespace(last_price=10.0, previous_close=10.0)

        def get_info(self):
            return {"longName": names.get(self.t, "")}
    fake.Ticker = _T
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_auto_resolve_rejects_a_ticker_whose_name_is_another_company(ar, monkeypatch):
    _llm(monkeypatch, {"SK에코플랜트": {"ticker": "SKHY", "company": "SK Ecoplant"},
                       "시놉시스": {"ticker": "SNPS", "company": "Synopsys"}},
         {"SKHY": "SK Hynix Inc.", "SNPS": "Synopsys, Inc."})
    out = ar.batch_resolve(["SK에코플랜트", "시놉시스"])
    assert out == {"sk에코플랜트": None, "시놉시스": ("SNPS", "US")}
    # 2026-10-16 다시 씀(#222): 이름이 다른 회사인 것은 다시 물어도 같은 답이라 '최종' 실패로 사유와 함께 남는다
    cache = json.loads(ar._CACHE_PATH.read_text("utf-8"))
    assert cache["시놉시스"] == ["SNPS", "US"]
    assert cache["sk에코플랜트"]["final"] is True and "SK Hynix" in cache["sk에코플랜트"]["why"]


def test_auto_resolve_without_a_company_name_is_not_accepted(ar, monkeypatch):
    """옛 형식(티커 문자열만)은 신원을 확인할 수 없어 받지 않는다 — 틀린 티커보다 미매칭이 낫다."""
    _llm(monkeypatch, {"시놉시스": "SNPS"}, {"SNPS": "Synopsys, Inc."})
    assert ar.batch_resolve(["시놉시스"]) == {"시놉시스": None}


def test_auto_resolve_prompt_excludes_korean_companies(ar, monkeypatch):
    seen = []
    import bot.screener as sc
    monkeypatch.setattr(sc, "_call_pro", lambda key, prompt, **k: seen.append(prompt) or ("{}", 1, 1))
    ar.batch_resolve(["SK에코플랜트"])
    assert "한국 회사(비상장 포함" in seen[0] and '"company"' in seen[0]


def test_auto_resolve_cache_is_a_new_file():
    """옛 캐시(portfolio_auto_aliases.json)에 영구로 남은 오매칭을 읽지 않는다(#18)."""
    import bot.portfolio_auto_resolve as m
    assert m._CACHE_PATH.name == "portfolio_auto_aliases_v2.json"


def test_auto_resolve_tolerates_malformed_entries(ar, monkeypatch):
    _llm(monkeypatch, {"가가": {"ticker": 5, "company": "X"}, "나나": {"company": "Synopsys"},
                       "다다": {"ticker": "SNPS"}}, {"SNPS": "Synopsys, Inc."})
    assert ar.batch_resolve(["가가", "나나", "다다"]) == {"가가": None, "나나": None, "다다": None}
