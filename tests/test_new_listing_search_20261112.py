"""신규 상장사 검색(사용자 2026-11-12 "동원파츠(0162D0)가 상장 3~4일이 지났는데
메인에서 이름으로 안 나온다 · 미국이나 다른 나라도 최대한 같이").

원인 셋: ① DART 회사 목록 디스크 TTL 30일 ② 메모리에 한 번 올린 목록은 상시
프로세스 수명 내내 재사용(디스크 TTL 무관) ③ KRX 시장 코드 목록 7일(.KS/.KQ).
"""

import json
import os
import time
from types import SimpleNamespace

import pytest

from bot import dart_client as dc
from bot import dashboard as dash
from bot import market as mk


def test_ttls_are_one_day():
    assert dc._CORPCODE_TTL_DAYS == 1
    assert mk._KR_CODES_CACHE_TTL == 24 * 3600


def _write(path, names, age_sec):
    path.write_text(json.dumps({
        "stock_to_corp": {c: "0" * 8 for _, c in names},
        "name_to_entries": {n: [{"name": n, "stock_code": c, "corp_code": "0" * 8}]
                            for n, c in names}}, ensure_ascii=False))
    t = time.time() - age_sec
    os.utime(path, (t, t))


def test_in_memory_list_expires_and_picks_up_new_listing(tmp_path, monkeypatch):
    cache = tmp_path / "corp.json"
    monkeypatch.setattr(dc, "_CORPCODE_CACHE", cache)
    _write(cache, [("삼성전자", "005930")], age_sec=dc._CORPCODE_TTL_DAYS * 86400 - 5)
    cli = dc.DartClient("")
    assert cli.find_by_name("동원파츠") == []
    # 다른 프로세스가 새 목록을 디스크에 썼다 + 메모리 사본의 수명이 지났다.
    _write(cache, [("삼성전자", "005930"), ("동원파츠", "0162D0")], age_sec=0)
    later = time.time() + 60          # 옛 사본(디스크 mtime + 1일)이 지난 시각
    monkeypatch.setattr(dc.time, "time", lambda: later)
    hits = cli.find_by_name("동원파츠")
    assert hits and hits[0]["stock_code"] == "0162D0"


def test_stale_fallback_does_not_redownload_every_call(tmp_path, monkeypatch):
    cache = tmp_path / "corp.json"
    monkeypatch.setattr(dc, "_CORPCODE_CACHE", cache)
    _write(cache, [("삼성전자", "005930")], age_sec=5 * 86400)
    calls = []
    monkeypatch.setattr(dc.requests, "get",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(OSError("x")))
    cli = dc.DartClient("KEY")
    for _ in range(5):
        assert cli.find_by_name("삼성전자")
    assert len(calls) == 1, calls


def test_name_falls_through_to_naver_then_yahoo(monkeypatch):
    from bot import portfolio_resolve as pr
    monkeypatch.setattr(dc, "get_dart", lambda: SimpleNamespace(find_by_name=lambda q: []))
    monkeypatch.setattr(pr, "resolve_via_naver",
                        lambda q: ("0162D0.KQ", "KR") if q == "동원파츠" else None)
    assert dash.resolve_name_to_ticker("동원파츠") == "0162D0.KQ"
    import yfinance as yf
    quotes = [{"quoteType": "EQUITY", "symbol": "9999.T", "longname": "Unrelated Corp"},
              {"quoteType": "EQUITY", "symbol": "7203.T", "longname": "Toyota Motor Corporation"}]
    monkeypatch.setattr(yf, "Search", lambda q, max_results=6: SimpleNamespace(quotes=quotes))
    assert dash.resolve_name_to_ticker("Toyota Motor") == "7203.T"
    # 같은 회사가 아니면 첫 결과라도 받지 않는다 · 한글 질의는 야후 대조 불가.
    assert dash.resolve_name_to_ticker("Nonexistent Widgets") is None
    assert dash._yahoo_search_any_market("토요타") is None


def test_not_found_page_explains_new_listing_lag():
    from bot.dashboard_server import render_search_error
    html = render_search_error("동원파츠")
    assert "하루 한 번" in html and "종목코드" in html
