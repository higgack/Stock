"""JP/CN_A/HK 52주 신고가 live 비교(intl_highlow.fetch_intl_highlow_live) 순수 로직 회귀.

EOD baseline(오늘 제외 52주 고저) × 네이버 현재가 비교 — 네트워크/yfinance 없이 mock
으로 판정 로직만(사용자 2026-06-26 저부하·실시간 신고가)."""
import tempfile
from pathlib import Path

import bot.intl_highlow as h
import bot.naver_ranking_client as nv
import bot.finviz_client as fc


def _setup(monkeypatch, base, live):
    monkeypatch.setattr(fc, "_CACHE_DIR", Path(tempfile.mkdtemp()))
    monkeypatch.setattr(fc, "_session_fresh", lambda *a, **k: False)  # 캐시 무시 재산출
    monkeypatch.setattr(h, "_load_baseline", lambda m: base)
    monkeypatch.setattr(nv, "world_live_map", lambda m, **k: live)


def test_live_high_low_classification(monkeypatch):
    base = {"7203.T": {"h52": 3000.0, "l52": 2000.0, "name": "도요타"},
            "6758.T": {"h52": 1500.0, "l52": 1000.0, "name": "소니"},
            "9999.T": {"h52": 500.0, "l52": 100.0, "name": "중간주"}}
    live = {"7203.T": {"price": 3100, "pct": 2.1, "name": "도요타", "vol": 1000, "mcap": 50000},
            "6758.T": {"price": 950, "pct": -3.0, "name": "소니", "vol": 500, "mcap": 30000},
            "9999.T": {"price": 300, "pct": 0.5, "name": "중간주", "vol": 10, "mcap": 100}}
    _setup(monkeypatch, base, live)
    out = h.fetch_intl_highlow_live("JP")
    assert [r["ticker"] for r in out["high"]] == ["7203.T"]   # price>=h52
    assert [r["ticker"] for r in out["low"]] == ["6758.T"]    # price<=l52
    # 중간주(고저 사이) 제외
    assert all(r["ticker"] != "9999.T" for r in out["high"] + out["low"])
    assert out["high"][0]["value"] == round(3100 * 1000 / 1e8, 2)
    assert "live" in out["source"]


def test_live_equal_boundary_is_new_high(monkeypatch):
    # 현재가 == 52주 고점(동률)도 신고가(시장 통용 ≥).
    base = {"X.HK": {"h52": 100.0, "l52": 50.0, "name": "X"}}
    live = {"X.HK": {"price": 100.0, "pct": 1.0, "name": "X", "vol": 5, "mcap": 9}}
    _setup(monkeypatch, base, live)
    out = h.fetch_intl_highlow_live("HK")
    assert [r["ticker"] for r in out["high"]] == ["X.HK"]


def test_live_excludes_hk_rmb_cny_counter(monkeypatch):
    # HK RMB 이중카운터(네이버 '… (CNY)')는 초저유동·통화불일치 → 신고저 제외
    # (2026-06-29: 82333.HK 장성자동차(CNY) 단독 신저가 오탐). 주카운터는 정상 판정.
    base = {"2333.HK": {"h52": 19.86, "l52": 8.98, "name": "장성자동차"},
            "82333.HK": {"h52": 17.63, "l52": 7.86, "name": "82333.HK"}}
    live = {"2333.HK": {"price": 8.5, "pct": -1.2, "name": "장성자동차", "vol": 9000, "mcap": 5000},
            "82333.HK": {"price": 7.86, "pct": 0.0, "name": "장성자동차 (CNY)", "vol": 500, "mcap": 181}}
    _setup(monkeypatch, base, live)
    out = h.fetch_intl_highlow_live("HK")
    tickers = [r["ticker"] for r in out["high"] + out["low"]]
    assert "82333.HK" not in tickers          # (CNY) 카운터 제외
    assert "2333.HK" in [r["ticker"] for r in out["low"]]  # 주카운터 정상(8.5<=8.98)


def test_live_none_when_no_baseline(monkeypatch):
    # baseline 없으면 None → 호출부가 기존 스캔 캐시로 폴백.
    _setup(monkeypatch, {}, {"7203.T": {"price": 1, "name": "x"}})
    assert h.fetch_intl_highlow_live("JP") is None


def test_live_none_when_no_naver(monkeypatch):
    _setup(monkeypatch, {"7203.T": {"h52": 1, "l52": 0, "name": "x"}}, {})
    assert h.fetch_intl_highlow_live("JP") is None


def test_live_only_supported_markets():
    # TW/US/KR 는 live 비교 대상 아님(네이버 worldstock 미수록 / 별도 소스).
    assert h.fetch_intl_highlow_live("TW") is None
    assert "JP" in h._LIVE_MARKETS and "CN_A" in h._LIVE_MARKETS and "HK" in h._LIVE_MARKETS
    assert "TW" not in h._LIVE_MARKETS and "KR" not in h._LIVE_MARKETS
