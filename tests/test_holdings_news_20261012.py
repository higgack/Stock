"""🗞️ 보유종목 News (사용자 2026-10-12) — `bot/holdings_news.py` · portfolio_watch 글 수신함 · 배선.

재는 것: 이름 매칭(낱말 경계) · 보유 종목 묶기 · 하루 창 수집(이름 거르기·중복·텔레그램·본문) · AI 결과
검증(근거 번호) · 저장(본문 안 남김) · 주기 · 판정 · 화면 · 진단 · 비용 · nav · portfolio_watch 수신함.
원천·AI·텔레그램은 전부 가짜로 태운다(네트워크 0 — 루트 conftest 가 소켓도 막는다).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

KST = timezone(timedelta(hours=9))


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=KST)


@pytest.fixture
def hn(tmp_path, monkeypatch):
    import bot.holdings_news as m
    import bot.usage_tracker as ut
    d = tmp_path / "hn"
    monkeypatch.setattr(m, "_DIR", d)
    monkeypatch.setattr(m, "_DAYS", d / "days")
    monkeypatch.setattr(m, "_STATE", d / "state.json")
    monkeypatch.setattr(m, "TG_INBOX", d / "tg_inbox.jsonl")
    monkeypatch.setattr(m, "_TG_LOCK", d / ".tg_inbox.lock")
    monkeypatch.setattr(m, "_RUN_LOCK", d / ".run.lock")
    monkeypatch.setattr(ut, "USAGE_LOG", tmp_path / "usage.jsonl")
    return m


def _h(name, ticker, market, value=1_000_000.0, *, matched=True, source="pykrx"):
    return {"상품명": name, "ticker": ticker, "market": market, "matched": matched,
            "source": source, "평가금액": value}


def _model(*hs, as_of="2026-10-10"):
    return {"holdings": list(hs), "as_of": as_of}


def _it(title, ts=None, *, link="", summary="", date=None, naver_link="", source="언론"):
    d = {"title": title, "summary": summary, "link": link, "source": source,
         "ts": ts or "", "date": date or (ts[:10] if ts else "")}
    if naver_link:
        d["naver_link"] = naver_link
    return d


def _fetcher(table, default=None):
    calls = []

    def f(stock):
        calls.append(stock["key"])
        return table.get(stock["key"], default or ([], "naver", ""))
    f.calls = calls
    return f


def _tg(hn, *rows):
    """(id, ts, text, fwd) → 수신함."""
    hn.tg_append([{"id": i, "ts": ts, "chan": "@rag", "fwd": fwd, "text": text}
                  for i, ts, text, fwd in rows])


# ── 이름 매칭 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, alias, kind, hit", [
    ("KT는 오늘 배당을 늘렸다", "KT", "name", True),
    ("KT&G 실적 발표", "KT", "name", False),            # 영문 '&' 는 낱말의 일부
    ("두산의 자회사", "두산", "name", True),              # 조사 뒤 낱말 끝
    ("두산에너빌리티 수주", "두산", "name", False),        # '에' 뒤에 '너' 가 이어진다
    ("LG 그룹 지배구조", "LG", "name", True),
    ("LG에너지솔루션 증설", "LG", "name", False),
    ("삼성전자주가 반등", "삼성전자", "name", True),       # '주가' 꼬리
    ("삼성전자우 거래 급증", "삼성전자", "name", False),   # 우선주는 다른 종목
    ("외인삼성전자 순매수", "삼성전자", "name", False),    # 앞이 붙어 있으면 놓친다(넓히면 LG 가 샌다)
    ("$ON 급등", "ON", "ticker", True),
    ("ON 급등", "ON", "ticker", False),                 # 두 글자 이하 티커는 $ 가 붙어야
    ("LS 전선 수주", "LS", "name", True),               # 이름이면 $ 없이(국내 두 글자 회사)
    ("ServiceNow now 급등", "NOW", "ticker", False),     # 영문은 대문자 그대로만
    ("Sk하이닉스 HBM", "SK하이닉스", "name", True),       # 한글·혼합은 대소문자 무시
    ("(005930) 외국인 순매수", "005930", "code", True),
    ("10050059301 체결", "005930", "code", False),
    ("매출 7203억", "7203", "ticker", False),
])
def test_find_mentions_word_boundaries(hn, text, alias, kind, hit):
    assert bool(hn.find_mentions(text, alias, kind)) is hit


def test_mentioned_accepts_alias_pairs_and_plain_words(hn):
    text = "엔비디아와 NVDA 둘 다"
    assert hn.mentioned(text, [("엔비디아", "name"), ("NVDA", "ticker")]) == [(0, 4), (6, 10)]
    assert hn.mentioned(text, ["엔비디아"]) == [(0, 4)]


def test_aliases_and_search_name(hn):
    pref = {"name": "삼성전자우", "market": "KR", "ticker": "005935"}
    assert hn.search_name(pref) == "삼성전자"       # 뉴스는 본주 이름으로 쓴다
    assert hn.aliases(pref) == [("삼성전자우", "name"), ("삼성전자", "name"), ("005935", "code")]
    assert hn.search_name({"name": "현대차2우B", "market": "KR"}) == "현대차"
    assert hn.search_name({"name": "XX우", "market": "US"}) == "XX우"     # 우선주 규칙은 국내만
    assert hn.aliases({"name": "엔비디아", "market": "US", "ticker": "NVDA"}) == \
        [("엔비디아", "name"), ("NVDA", "ticker")]
    assert hn.aliases({"name": "도요타", "market": "JP", "ticker": "7203.T"})[-1] == ("7203", "ticker")
    assert hn.aliases({"name": "가", "market": "KR", "ticker": "000001"}) == [("000001", "code")]


def test_excerpt_widens_a_short_line_to_its_neighbours(hn):
    """종목 이름만 한 줄에 있고 내용이 다음 줄이면 그 줄까지 넘긴다 — 이름 줄만 넘기면 AI 가 근거를 못 본다."""
    text = "오늘의 메모\n삼성전자\nHBM4 퀄 테스트 통과 — 외국인 순매수 3일째"
    ex = hn.excerpt(text, hn.find_mentions(text, "삼성전자")[0])
    assert "HBM4 퀄 테스트 통과" in ex and "오늘의 메모" in ex


def test_excerpt_takes_the_mentioning_lines_only(hn):
    text = "\n".join(["시장 개요 " * 30, "삼성전자 HBM 공급 확대", "채권 금리 " * 30, "", "", "카카오 실적 부진"])
    ex = hn.excerpt(text, hn.find_mentions(text, "삼성전자")[0])
    assert "삼성전자 HBM 공급 확대" in ex and len(ex) <= hn.SNIPPET_CHARS
    assert "카카오" not in ex                          # 멀리 떨어진 다른 종목 대목은 안 넘긴다


# ── 종목 ─────────────────────────────────────────────────────────────────────
def test_universe_merges_brokers_and_skips_etf_and_unmatched(hn):
    stocks, skipped = hn.universe(_model(
        _h("삼성전자", "005930", "KR", 3e6),
        _h("삼성전자", "005930.KS", "KR", 2e6),            # 다른 증권사 — 같은 종목
        _h("엔비디아", "NVDA", "US", 6e6, source="alias"),
        _h("카카오", "035720", "KR", 1e6),
        _h("KODEX 200", "069500", "KR", 9e6, source="etf_meta"),
        _h("TIGER 미국테크", None, "KR", 1e6, matched=False, source="etf_brand"),
        _h("알수없는상품", None, None, 1e6, matched=False, source=None),
    ))
    # 평가금액 큰 순(키 순서와 다르게 둔다 — 같으면 정렬을 지워도 통과한다, #91c)
    assert [s["key"] for s in stocks] == ["US:NVDA", "KR:005930", "KR:035720"]
    assert stocks[1]["value"] == 5e6 and stocks[1]["sym"] == "005930"
    # 브랜드로만 알아본 ETF 는 티커가 없어도 ETF 다 — '티커 미확인' 으로 세면 화면이 틀린 이름을 붙인다
    assert skipped["etf"] == ["KODEX 200", "TIGER 미국테크"]
    assert skipped["unmatched"] == ["알수없는상품"]
    assert skipped["as_of"] == "2026-10-10"


def test_universe_reads_the_asset_dashboard_record(hn, monkeypatch):
    """자산 대시보드와 **같은 기록**을 읽는다(#38) — 인자 없이 부르면 portfolio.load()."""
    import bot.portfolio as pf
    monkeypatch.setattr(pf, "load", lambda: _model(_h("카카오", "035720", "KR")))
    assert [s["key"] for s in hn.universe()[0]] == ["KR:035720"]


# ── 원천 파싱 ─────────────────────────────────────────────────────────────────
def test_yf_item_reads_both_shapes(hn):
    old = {"title": "Old shape", "link": "https://x/1", "publisher": "Reuters",
           "providerPublishTime": 1760220000}
    new = {"content": {"title": "New shape", "summary": "s", "pubDate": "2026-10-11T22:00:00Z",
                       "provider": {"displayName": "Yahoo"},
                       "canonicalUrl": {"url": "https://x/2"}}}
    a, b = hn._yf_item(old), hn._yf_item(new)
    assert a["title"] == "Old shape" and a["source"] == "Reuters" and a["ts"].endswith("+00:00")
    assert b["title"] == "New shape" and b["link"] == "https://x/2" and b["source"] == "Yahoo"
    assert hn._item_ts(b) == _t("2026-10-12T07:00")        # UTC → KST
    assert hn._yf_item({"content": {"title": ""}}) is None and hn._yf_item("x") is None


def test_fetch_yf_says_when_it_could_not_read_any_item(hn, monkeypatch):
    import bot.finviz_client as fc
    monkeypatch.setattr(fc, "yf_paused", lambda: False)
    fake = types.ModuleType("yfinance")
    fake.Ticker = lambda t: types.SimpleNamespace(get_news=lambda count=10: [{"weird": 1}, {"also": 2}])
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    items, why = hn._fetch_yf("NVDA")
    assert items is None and "2건을 하나도 못 읽음" in why


def test_fetch_stock_news_ladder_and_reasons(hn, monkeypatch):
    kr = {"key": "KR:005930", "market": "KR", "ticker": "005930", "name": "삼성전자"}
    us = {"key": "US:NVDA", "market": "US", "ticker": "NVDA", "name": "엔비디아"}
    seen = []
    monkeypatch.setattr(hn, "_fetch_naver", lambda q: (None, "네이버 키 없음"))
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: (seen.append((q, mk)) or [{"title": "g"}], ""))
    monkeypatch.setattr(hn, "_fetch_yf", lambda t: (None, "yfinance 일시정지(YF_PAUSE)"))
    assert hn.fetch_stock_news(kr) == ([{"title": "g"}], "google", "네이버 키 없음")
    assert hn.fetch_stock_news(us) == ([{"title": "g"}], "google", "yfinance 일시정지(YF_PAUSE)")
    assert seen == [("삼성전자", "KR"), ("NVDA stock", "US")]
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: (None, "구글 실패"))
    assert hn.fetch_stock_news(kr) == ([], "", "네이버 키 없음 · 구글 실패")
    monkeypatch.setattr(hn, "_fetch_naver", lambda q: ([], ""))      # 0건도 답이다 — 폴백 안 함
    assert hn.fetch_stock_news(kr) == ([], "naver", "")


def test_fetch_naver_needs_both_keys(hn, monkeypatch):
    import bot.env_keys as ek
    monkeypatch.setattr(ek, "env_key", lambda n: "x" if n == "NAVER_CLIENT_ID" else "")
    assert hn._fetch_naver("삼성전자") == (None, "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음")


def test_extract_naver_body(hn):
    html = ("<html><article id='dic_area' class='x'><script>var a=1;</script>"
            "삼성전자가 <b>HBM4</b> 를 공급한다.&nbsp;끝 문장.<br>"
            "Copyright ⓒ 언론사 무단 전재</article></html>")
    b = hn.extract_naver_body(html)
    assert b.startswith("삼성전자가 HBM4 를 공급한다.") and "Copyright" not in b and "var a" not in b
    # article 태그가 없어도 dic_area 시작점에서 읽는다
    assert hn.extract_naver_body("<div id=\"dic_area\">본문 일부</div>").startswith("본문 일부")
    assert hn.extract_naver_body("<div>다른 구조</div>") == "" and hn.extract_naver_body("") == ""
    assert len(hn.extract_naver_body("<article id='dic_area'>" + "가" * 5000 + "</article>")) == hn.BODY_CHARS


def test_fetch_body_only_reads_naver_hosts(hn, monkeypatch):
    import requests

    import bot.finviz_client as fc
    monkeypatch.setattr(fc, "naver_paused", lambda: False)
    got = []

    def fake_get(url, **kw):
        got.append(url)
        return types.SimpleNamespace(status_code=200, text="<article id='dic_area'>본문</article>")
    monkeypatch.setattr(requests, "get", fake_get)
    assert hn._fetch_body("https://www.press.com/news/1") == ""        # 언론사 원문은 안 읽는다
    assert hn._fetch_body("https://n.news.naver.com/mnews/article/001/1") == "본문"
    assert got == ["https://n.news.naver.com/mnews/article/001/1"]
    monkeypatch.setattr(fc, "naver_paused", lambda: True)             # 일시정지면 안 친다
    assert hn._fetch_body("https://n.news.naver.com/mnews/article/001/2") == ""
    assert len(got) == 1


def test_news_clients_give_time_and_the_naver_copy(tmp_path, monkeypatch):
    """하루 창은 시각이 있어야 선다(날짜만이면 창 경계가 하루 단위로 뭉개진다) — 두 클라이언트가
    `ts` 를 싣고, 네이버는 본문을 읽을 사본 링크(`naver_link`)를 따로 싣는다."""
    import bot.google_news_client as gn
    import bot.naver_news_client as nn
    monkeypatch.setattr(nn, "_CACHE_DIR", tmp_path / "nn")
    monkeypatch.setattr(gn, "_CACHE_DIR", tmp_path / "gn")
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    pub = (datetime.now(KST) - timedelta(hours=2)).replace(microsecond=0)
    payload = {"items": [{"title": "삼성전자 <b>HBM</b>", "description": "d",
                          "originallink": "https://press/a", "link": "https://n.news.naver.com/x/1",
                          "pubDate": pub.strftime("%a, %d %b %Y %H:%M:%S +0900")}]}
    monkeypatch.setattr(nn.requests, "get", lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: payload))
    it = nn.fetch_news("삼성전자", days_back=28, max_items=10)[0]
    assert it["ts"] == pub.isoformat() and it["naver_link"] == "https://n.news.naver.com/x/1"
    assert it["link"] == "https://press/a"
    gpub = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)
    rss = ("<rss><channel><item><title>Nvidia hits record - Reuters</title><link>https://g/1</link>"
           f"<pubDate>{gpub.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item></channel></rss>")
    monkeypatch.setattr(gn.requests, "get", lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None, content=rss.encode()))
    g = gn.fetch_news("NVDA stock", lang="en-US", country="US", ceid="US:en")[0]
    assert g["ts"] == gpub.isoformat() and g["title"] == "Nvidia hits record"


# ── 수집 ─────────────────────────────────────────────────────────────────────
_NOW = "2026-10-12T07:15"


def _collect_fixture(hn):
    model = _model(_h("삼성전자", "005930", "KR", 9e6), _h("엔비디아", "NVDA", "US", 8e6, source="alias"),
                   _h("카카오", "035720", "KR", 7e6), _h("SK하이닉스", "000660", "KR", 6e6))
    prev = {"date": "2026-10-11", "window": {"until": "2026-10-11T07:15:00+09:00"},
            "stocks": [{"key": "KR:005930", "items": [{"k": "https://press/old"}]}]}
    sam = [
        _it("삼성전자, HBM4 공급 확대", "2026-10-12T06:00:00+09:00", link="https://press/a",
            naver_link="https://n.news.naver.com/mnews/article/001/1"),
        _it("삼성전자 노조 파업 예고", "2026-10-11T08:00:00+09:00", link="https://press/b",
            naver_link="https://n.news.naver.com/mnews/article/001/2"),
        _it("삼성전자 지난주 기사", "2026-10-10T08:00:00+09:00", link="https://press/c"),   # 창 앞
        _it("SK하이닉스 실적", "2026-10-12T05:00:00+09:00", link="https://press/d"),     # 이름 없음
        _it("삼성전자, HBM4 공급 확대!", "2026-10-12T05:30:00+09:00", link="https://press/e"),  # 같은 제목
        _it("삼성전자 미래 기사", "2026-10-12T08:00:00+09:00", link="https://press/f"),    # 창 뒤
        _it("삼성전자 날짜만 기사", date="2026-10-11", link="https://press/g"),            # 날짜만
        _it("삼성전자 어제 실린 기사", "2026-10-11T09:00:00+09:00", link="https://press/old"),
    ]
    table = {
        "KR:005930": (sam, "naver", ""),
        "US:NVDA": ([_it("Chip stocks rally", "2026-10-12T05:00:00+09:00", link="https://y/1")],
                    "yfinance", ""),
        "KR:035720": ([], "", "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음 · 구글 뉴스가 결과를 못 줌(실패 또는 0건)"),
        "KR:000660": ([_it("SK하이닉스 HBM 증설", "2026-10-12T04:00:00+09:00", link="https://g/2")],
                      "google", "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음"),
    }
    _tg(hn, (1, "2026-10-12T06:30:00+09:00", "오늘 삼성전자는 HBM 이 좋다\n카카오 실적 부진", False),
        (2, "2026-10-10T06:30:00+09:00", "삼성전자 옛 글", False),
        (3, "2026-10-12T06:40:00+09:00", "NVDA 신고가 갱신", True),
        (4, "2026-10-12T06:50:00+09:00", "무관한 글", False))
    bodies = []

    def body(link):
        bodies.append(link)
        return "본문 앞부분" if link.endswith("/1") else ""
    return model, [prev], _fetcher(table), body, bodies


def test_collect_window_filters_dedupe_telegram_and_body(hn):
    model, recs, fetch, body, bodies = _collect_fixture(hn)
    rec = hn.collect(_t(_NOW), model=model, records=recs, fetch=fetch, body=body, pace=0)
    assert rec["window"] == {"since": "2026-10-11T07:15:00+09:00", "until": "2026-10-12T07:15:00+09:00"}
    by = {s["key"]: s for s in rec["stocks"]}
    sam = by["KR:005930"]
    titles = [it["title"] for it in sam["items"]]
    assert titles[0].startswith("오늘 삼성전자는")                        # 텔레그램 06:30 이 맨 앞(시각순)
    assert titles[1:] == ["삼성전자, HBM4 공급 확대", "삼성전자 노조 파업 예고", "삼성전자 날짜만 기사"]
    assert [it["n"] for it in sam["items"]] == [1, 2, 3, 4]
    assert sam["items"][3]["dated_only"] is True and sam["status"] == "pending"
    a, b = sam["items"][1], sam["items"][2]
    assert a["_body"] == "본문 앞부분" and a["body_chars"] == 6 and "_body" not in b
    assert bodies == ["https://n.news.naver.com/mnews/article/001/1",
                      "https://n.news.naver.com/mnews/article/001/2"]
    src = rec["sources"]
    assert src["dropped_name"] == 1 and src["body_try"] == 2 and src["body_ok"] == 1
    assert src["naver"] == 1 and src["yfinance"] == 1 and src["google"] == 1
    assert src["fallback"] == {"네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음": 1}
    assert src["fail"] == 1 and list(src["fail_reasons"]) == [
        "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음 · 구글 뉴스가 결과를 못 줌(실패 또는 0건)"]
    assert src["telegram"] == {"inbox": 4, "window": 3, "matched": 3, "bad_lines": 0}
    nv = by["US:NVDA"]
    assert [it["src"] for it in nv["items"]] == ["telegram", "yfinance"]        # 해외는 이름으로 안 거른다
    assert nv["items"][0]["source"] == "텔레그램 · 포워드"
    kk = by["KR:035720"]                                           # 원천은 실패해도 텔레그램은 실린다
    assert kk["via"] == "" and "구글" in kk["via_why"] and [it["src"] for it in kk["items"]] == ["telegram"]
    assert "카카오 실적 부진" in kk["items"][0]["summary"]
    assert by["KR:000660"]["via"] == "google" and by["KR:000660"]["items"][0]["title"] == "SK하이닉스 HBM 증설"


def test_collect_survives_one_stocks_exception(hn):
    """한 종목의 예외가 그날 수집 전체를 멈추지 않는다 — 그 종목만 실패로 센다(#315)."""
    def fetch(st):
        if st["key"] == "KR:035720":
            raise RuntimeError("깨진 응답")
        return ([_it(f"{st['name']} 소식", "2026-10-12T06:00:00+09:00", link=f"https://p/{st['key']}")],
                "naver", "")
    rec = hn.collect(_t(_NOW), model=_model(_h("삼성전자", "005930", "KR"), _h("카카오", "035720", "KR")),
                     records=[], fetch=fetch, pace=0)
    by = {x["key"]: x for x in rec["stocks"]}
    assert by["KR:005930"]["status"] == "pending" and by["KR:035720"]["status"] == "empty"
    assert rec["sources"]["fail"] == 1
    assert list(rec["sources"]["fail_reasons"]) == ["수집 예외(RuntimeError: 깨진 응답)"]


def test_collect_window_start_rules(hn):
    model = _model(_h("삼성전자", "005930", "KR"))
    f = _fetcher({})
    now = _t(_NOW)
    first = hn.collect(now, model=model, records=[], fetch=f, pace=0)
    assert first["window"]["since"] == "2026-10-11T07:15:00+09:00"          # 기록 없으면 24시간
    old = [{"date": "2026-10-05", "window": {"until": "2026-10-05T07:15:00+09:00"}, "stocks": []}]
    capped = hn.collect(now, model=model, records=old, fetch=f, pace=0)
    assert capped["window"]["since"] == "2026-10-09T07:15:00+09:00"         # 며칠 멈췄어도 72시간까지만
    assert capped["stocks"][0]["status"] == "empty"
    assert hn.collect(now, model=_model(), records=[], fetch=f, pace=0) is None   # 쓸 것이 없다


def test_collect_caps_per_stock(hn):
    news = [_it(f"삼성전자 기사 {i}", f"2026-10-12T0{i}:00:00+09:00", link=f"https://p/{i}",
                naver_link=f"https://n.news.naver.com/a/{i}") for i in range(1, 9)]
    for i in range(6):
        _tg(hn, (100 + i, f"2026-10-12T06:{10 + i}:00+09:00", f"삼성전자 글 {i}", False))
    calls = []
    rec = hn.collect(_t(_NOW), model=_model(_h("삼성전자", "005930", "KR")), records=[],
                     fetch=_fetcher({"KR:005930": (news, "naver", "")}),
                     body=lambda link: calls.append(link) or "본문", pace=0)
    items = rec["stocks"][0]["items"]
    assert sum(it["src"] == "naver" for it in items) == hn.MAX_NEWS_PER_STOCK
    tg = [it for it in items if it["src"] == "telegram"]
    assert len(tg) == hn.MAX_TG_PER_STOCK and tg[0]["id"] == 105            # 최근 글부터
    assert len(calls) == hn.MAX_BODY_PER_STOCK


def test_collect_dedupes_against_the_last_two_records(hn):
    """날짜만 주는 원천은 창이 하루 넉넉해 같은 기사가 이틀 연달아 걸릴 수 있다 — 앞선 기록 **둘**과 대조."""
    prev2 = {"date": "2026-10-10", "window": {"until": "2026-10-10T07:15:00+09:00"},
             "stocks": [{"key": "KR:005930", "items": [{"k": "https://press/x"}]}]}
    prev1 = {"date": "2026-10-11", "window": {"until": "2026-10-11T07:15:00+09:00"},
             "stocks": [{"key": "KR:005930", "items": []}]}
    news = [_it("삼성전자 기사", date="2026-10-11", link="https://press/x")]
    rec = hn.collect(_t(_NOW), model=_model(_h("삼성전자", "005930", "KR")), records=[prev2, prev1],
                     fetch=_fetcher({"KR:005930": (news, "naver", "")}), pace=0)
    assert rec["stocks"][0]["items"] == []


def test_collect_body_reads_have_a_run_budget_and_only_naver_copies(hn, monkeypatch):
    monkeypatch.setattr(hn, "MAX_BODY_PER_RUN", 1)
    calls = []

    def news(stock):
        return ([_it(f"{stock['name']} 기사", "2026-10-12T06:00:00+09:00", link=f"https://p/{stock['key']}",
                     naver_link=f"https://n.news.naver.com/a/{stock['ticker']}")], "naver", "")
    rec = hn.collect(_t(_NOW), model=_model(_h("삼성전자", "005930", "KR"), _h("카카오", "035720", "KR")),
                     records=[], fetch=news, body=lambda l: calls.append(l) or "본문", pace=0)
    assert len(calls) == 1 and rec["sources"]["body_try"] == 1          # 실행 전체 상한
    calls.clear()
    other = [_it("삼성전자 기사", "2026-10-12T06:00:00+09:00", link="https://press/1",
                 naver_link="https://press.co.kr/orig/1")]                 # 네이버 사본이 아닌 링크
    hn.collect(_t(_NOW), model=_model(_h("삼성전자", "005930", "KR")), records=[],
               fetch=_fetcher({"KR:005930": (other, "naver", "")}), body=lambda l: calls.append(l) or "x",
               pace=0)
    assert calls == []


def test_collect_does_not_repeat_yesterdays_telegram(hn):
    _tg(hn, (7, "2026-10-12T06:00:00+09:00", "삼성전자 글", False))
    prev = {"date": "2026-10-11", "window": {"until": "2026-10-11T07:15:00+09:00"},
            "stocks": [{"key": "KR:005930", "items": [{"k": "tg:@rag:7"}]}]}
    rec = hn.collect(_t(_NOW), model=_model(_h("삼성전자", "005930", "KR")), records=[prev],
                     fetch=_fetcher({}), pace=0)
    assert rec["stocks"][0]["items"] == []


# ── Pros/Cons ────────────────────────────────────────────────────────────────
def _stock(key, n=3, *, status="pending"):
    return {"key": key, "market": "KR", "ticker": key.split(":")[1], "name": key, "value": 1.0,
            "items": [{"n": i, "src": "naver", "title": f"{key} 기사 {i}", "summary": "",
                       "ts": "2026-10-12T06:00+09:00", "link": f"https://p/{key}/{i}", "source": "x"}
                      for i in range(1, n + 1)],
            "status": status, "tries": 0, "pros": [], "cons": [], "relevant": None, "reason": ""}


def _rec(*stocks):
    return {"date": "2026-10-12", "stocks": list(stocks),
            "llm": {"calls": 0, "pt": 0, "ot": 0, "cost_usd": 0.0}}


def _llm(resp, *, pt=1000, ot=200, why=""):
    calls = []

    def f(prompt):
        calls.append(prompt)
        return (resp(prompt) if callable(resp) else resp), pt, ot, why
    f.calls = calls
    return f


def test_analyze_keeps_only_lines_with_real_refs(hn):
    s = _stock("KR:A", 3)
    out = {"stocks": [{"key": "KR:A", "relevant": [1, 2, "-1", "x"],
                       "pros": [{"t": "좋다", "refs": [1]}, {"t": "근거 없음", "refs": []},
                                {"t": "없는 번호", "refs": [9]}, {"t": "관련 없다고 본 항목", "refs": [3]},
                                {"t": " ", "refs": [1]}],
                       "cons": [{"t": "가" * 120, "refs": [2, 2, "2", "z"]}]}]}
    hn.analyze(_rec(s), llm=_llm(json.dumps(out, ensure_ascii=False)))
    assert s["status"] == "ok" and s["relevant"] == [1, 2]
    assert s["pros"] == [{"t": "좋다", "refs": [1]}]
    assert s["cons"] == [{"t": "가" * hn.LINE_CHARS + "…", "refs": [2]}]


def test_analyze_caps_lines_and_defaults_relevant(hn):
    s = _stock("KR:A", 2)
    pros = [{"t": f"줄 {i}", "refs": [1]} for i in range(6)]
    hn.analyze(_rec(s), llm=_llm(json.dumps({"stocks": [{"key": "KR:A", "pros": pros}]})))
    assert len(s["pros"]) == hn.MAX_PER_SIDE and s["relevant"] == [1, 2] and s["cons"] == []


def test_analyze_retries_then_gives_up(hn):
    s, t = _stock("KR:A"), _stock("KR:B")
    rec = _rec(s, t)
    only_b = json.dumps({"stocks": [{"key": "KR:B", "pros": [{"t": "b", "refs": [1]}]}]})
    for i in range(1, hn.MAX_ANALYZE_TRIES + 1):
        done = hn.analyze(rec, llm=_llm(only_b))
        assert s["tries"] == i and s["reason"] == "AI 응답에 이 종목이 없음"
    assert s["status"] == "failed" and done["failed"] == 1
    assert t["status"] == "ok" and t["tries"] == 0


def test_analyze_reasons_unreadable_and_call_failure(hn):
    s = _stock("KR:A")
    hn.analyze(_rec(s), llm=_llm("이건 JSON 이 아님"))
    assert s["reason"] == "AI 응답을 못 읽음" and s["status"] == "pending"
    u = _stock("KR:B")
    hn.analyze(_rec(u), llm=_llm(None, pt=0, ot=0, why="AI 호출 실패(TimeoutError: x)"))
    assert u["reason"] == "AI 호출 실패(TimeoutError: x)"


def test_analyze_without_key_says_so_and_spends_nothing(hn):
    rec = _rec(_stock("KR:A"), _stock("KR:B"), _stock("KR:C", status="ok"))
    f = _llm(None, pt=0, ot=0, why="no_key")
    done = hn.analyze(rec, llm=f)
    assert done == {"calls": 0, "analyzed": 0, "failed": 0, "no_key": True} and len(f.calls) == 1
    assert [s["reason"] for s in rec["stocks"][:2]] == ["AI 키(GOOGLE_API_KEY) 미설정 — 뉴스만 보여 줍니다"] * 2
    assert rec["stocks"][2]["reason"] == "" and rec["llm"]["calls"] == 0


def test_analyze_respects_the_call_budget_and_saves_each_batch(hn):
    rec = _rec(*[_stock(f"KR:{i:06d}", 1) for i in range(hn.BATCH_MAX * 2 + 5)])
    saves = []
    ok = lambda p: json.dumps({"stocks": [{"key": k, "pros": []} for k in  # noqa: E731
                                          (ln.split("key=")[1].split(" ")[0]
                                           for ln in p.splitlines() if ln.startswith("### key="))]})
    done = hn.analyze(rec, llm=_llm(ok), max_calls=2, on_progress=lambda r: saves.append(1))
    assert done["calls"] == 2 and len(saves) == 2 and done["analyzed"] == hn.BATCH_MAX * 2
    rest = [s for s in rec["stocks"] if s["status"] == "pending"]
    assert len(rest) == 5 and all(s["tries"] == 0 for s in rest)     # 상한으로 남은 건 실패가 아니다


def test_batches_split_by_size_and_count(hn):
    small = [_stock(f"KR:{i:06d}", 1) for i in range(hn.BATCH_MAX + 3)]
    assert [len(g) for g in hn.batches(small)] == [hn.BATCH_MAX, 3]          # 개수 상한
    big = _stock("KR:000001", 3)
    for it in big["items"]:
        it["summary"] = "가" * 400
    many = [dict(big, key=f"KR:{i:06d}") for i in range(9)]
    gs = hn.batches(many)
    assert len(gs) > 1 and all(len(g) < hn.BATCH_MAX for g in gs)          # 글자 상한이 먼저 묶음을 끊는다
    assert all(sum(len(hn._stock_block(x)) for x in g) <= hn.BATCH_CHARS for g in gs)
    assert sum(len(g) for g in gs) == 9


def test_usage_is_logged_for_the_cost_card(hn, tmp_path):
    rec = _rec(_stock("KR:A"))
    hn.analyze(rec, llm=_llm(json.dumps({"stocks": [{"key": "KR:A"}]}), pt=2000, ot=500))
    line = json.loads((tmp_path / "usage.jsonl").read_text(encoding="utf-8").strip())
    assert line["subsystem"] == "holdings_news" and line["model"] == hn._MODEL
    assert line["type"] == "llm_call" and line["cost_usd"] > 0
    assert rec["llm"]["pt"] == 2000 and rec["llm"]["cost_usd"] == line["cost_usd"]


def test_prompt_carries_body_but_saved_record_does_not(hn):
    s = _stock("KR:A", 1)
    s["items"][0]["_body"] = "비밀 아닌 본문 앞부분"
    s["items"][0]["body_chars"] = 11
    assert "본문: 비밀 아닌 본문 앞부분" in hn.build_prompt([s])
    rec = _rec(s)
    hn.save_record(rec)
    saved = (hn._DAYS / "2026-10-12.json").read_text(encoding="utf-8")
    assert "비밀 아닌 본문" not in saved and '"body_chars": 11' in saved
    assert rec["stocks"][0]["items"][0]["_body"]          # 메모리의 기록은 그대로(다음 묶음이 쓴다)


def test_parse_json_tolerates_fences(hn):
    assert hn._parse_json('```json\n{"stocks": []}\n```') == {"stocks": []}
    assert hn._parse_json("설명 {\"a\": 1} 끝") == {"a": 1}
    assert hn._parse_json("[1, 2]") is None and hn._parse_json("") is None


# ── 저장 ─────────────────────────────────────────────────────────────────────
def test_load_skips_a_record_with_a_malformed_date(hn):
    """날짜가 깨진 기록 하나가 페이지 전체를 막지 않게(렌더가 그 날짜를 날짜로 읽는다)."""
    hn.save_record({"date": "2026-10-12", "stocks": []})
    (hn._DAYS / "x.json").write_text(json.dumps({"date": "어제", "stocks": []}), encoding="utf-8")
    assert [r["date"] for r in hn.load_records()] == ["2026-10-12"]


def test_save_prunes_and_load_skips_broken(hn, monkeypatch):
    monkeypatch.setattr(hn, "KEEP_DAYS", 2)
    for d in ("2026-10-10", "2026-10-11", "2026-10-12"):
        hn.save_record({"date": d, "stocks": []})
    assert sorted(p.name for p in hn._DAYS.glob("*.json")) == ["2026-10-11.json", "2026-10-12.json"]
    (hn._DAYS / "2026-10-13.json").write_text("{깨짐", encoding="utf-8")
    assert [r["date"] for r in hn.load_records()] == ["2026-10-11", "2026-10-12"]


# ── 주기 ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def portfolio(monkeypatch):
    import bot.portfolio as pf
    box = {"m": _model(_h("삼성전자", "005930", "KR"), _h("엔비디아", "NVDA", "US", source="alias"))}
    monkeypatch.setattr(pf, "load", lambda: box["m"])
    return box


def _news_for_all(stock):
    return ([_it(f"{stock['name']} 소식", "2026-10-12T06:00:00+09:00", link=f"https://p/{stock['key']}")],
            "naver" if stock["market"] == "KR" else "yfinance", "")


def _answer_all(prompt):
    keys = [ln.split("key=")[1].split(" ")[0] for ln in prompt.splitlines() if ln.startswith("### key=")]
    return json.dumps({"stocks": [{"key": k, "pros": [{"t": "좋은 소식", "refs": [1]}]} for k in keys]})


def test_tick_waits_until_run_time(hn, portfolio):
    res = hn.tick(_t("2026-10-12T06:00"), llm=_llm(_answer_all), fetch=_news_for_all, pace=0)
    assert res == {"status": "wait"} and hn.load_records() == []
    assert hn.load_state()["last_tick"] == "2026-10-12T06:00:00+09:00"


def test_tick_collects_once_then_only_finishes_pending(hn, portfolio):
    f = _fetcher({}, ([_it("삼성전자 소식", "2026-10-12T06:00:00+09:00", link="https://p/1")], "naver", ""))
    half = _llm(lambda p: json.dumps({"stocks": [{"key": "KR:005930"}]}))
    res = hn.tick(_t("2026-10-12T07:15"), llm=half, fetch=f, body=lambda l: "", pace=0)
    assert res["status"] == "ok" and len(f.calls) == 2
    rec = hn.load_records()[0]
    assert {s["key"]: s["status"] for s in rec["stocks"]} == {"KR:005930": "ok", "US:NVDA": "pending"}
    st = hn.load_state()
    assert st["last_collect"] == "2026-10-12T07:15:00+09:00" and st["reason"] == ""
    res2 = hn.tick(_t("2026-10-12T07:30"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert len(f.calls) == 2                                           # 다시 모으지 않는다
    assert res2["analyzed"] == 1 and all(s["status"] == "ok" for s in hn.load_records()[0]["stocks"])
    assert hn.load_state()["last_collect"] == "2026-10-12T07:15:00+09:00"


def test_tick_without_holdings_says_none(hn, portfolio):
    portfolio["m"] = None
    assert hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=_news_for_all, pace=0) == {"status": "none"}
    st = hn.load_state()
    assert st["no_holdings"] is True and "보유 종목이 없음" in st["reason"]
    assert hn.status_verdict([], _t(_NOW), st)[0] == "none"


def test_tick_is_single_flight(hn, portfolio):
    assert hn._TICK.acquire(blocking=False)
    try:
        assert hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=_news_for_all, pace=0) == {"status": "busy"}
    finally:
        hn._TICK.release()


def test_tick_and_render_records_the_failure_and_still_renders(hn, monkeypatch):
    rendered = []
    monkeypatch.setattr(hn, "tick", lambda now=None: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(hn, "regenerate", lambda now=None: rendered.append(now) or True)
    assert hn.tick_and_render(_t(_NOW)) == {"status": "error"}
    st = hn.load_state()
    assert st["reason"] == "주기 실패(RuntimeError: boom)" and st["no_holdings"] is False
    assert rendered == [_t(_NOW)]


def test_tick_passes_pace_to_collect(hn, portfolio, monkeypatch):
    """테스트·진단이 원천 간격을 0 으로 줄 수 있어야 한다 — `time.sleep` 을 갈아 끼우면 공용 모듈이라
    다른 테스트까지 멈춘다(#439)."""
    seen = {}
    real = hn.collect
    monkeypatch.setattr(hn, "collect", lambda *a, **k: seen.update(k) or real(*a, **k))
    hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=_news_for_all, body=lambda l: "", pace=0)
    assert seen["pace"] == 0


# ── 판정 ─────────────────────────────────────────────────────────────────────
def _done(*stocks, sources=None):
    return {"date": "2026-10-12", "stocks": list(stocks),
            "sources": sources or {"naver": 1, "telegram": {"matched": 2}}}


@pytest.mark.parametrize("recs, st, now, kind, frag", [
    ([], {"no_holdings": True, "reason": "자산 기록 없음"}, "2026-10-12T09:00", "none", "보유 종목이 없음"),
    ([], {"reason": "주기 실패(X)"}, "2026-10-12T06:00", "wait", "직전 주기 실패: 주기 실패(X)"),
    ([], {"reason": "주기 실패(X)"}, "2026-10-12T09:00", "fail", "오늘 집계 못 함"),
    ([], {"last_tick": "2026-10-12T07:00:00+09:00"}, "2026-10-12T09:00", "fail", "봇 주기가 멈췄을 수"),
    ([], {"last_tick": "2026-10-12T08:55:00+09:00"}, "2026-10-12T09:00", "wait", "집계 중"),
    ([], {}, "2026-10-12T09:00", "wait", "아직 한 번도 돌리지 않음"),    # '집계 중' 은 재지 않은 주장(#165)
    ([_done({"status": "pending", "items": [1], "reason": "AI 키 없음"})], {}, "2026-10-12T09:00",
     "pending", "Pros/Cons 대기 1종목 — AI 키 없음"),
    ([_done({"status": "failed", "items": [1], "reason": "AI 응답을 못 읽음"})], {}, "2026-10-12T09:00",
     "fail", "실패 1종목"),
    ([_done({"status": "empty", "items": []}, sources={"fail": 2, "fail_reasons": {"원천 X": 2}})], {},
     "2026-10-12T09:00", "fail", "원천을 하나도 못 받음 — 원천 X"),
    ([_done({"status": "ok", "items": [1]}, {"status": "empty", "items": []})], {}, "2026-10-12T09:00",
     "ok", "보유 2종목 중 뉴스 1종목 · 텔레그램 언급 2건"),
])
def test_status_verdict_branches(hn, recs, st, now, kind, frag):
    k, line = hn.status_verdict(recs, _t(now), st)
    assert k == kind and frag in line, line


# ── 화면 ─────────────────────────────────────────────────────────────────────
def _page_rec():
    def it(n, src="naver", **kw):
        return {"n": n, "src": src, "title": kw.get("title", f"기사 {n}"), "summary": kw.get("summary", ""),
                "link": kw.get("link", f"https://p/{n}"), "source": kw.get("source", "언론"),
                "ts": "2026-10-12T06:00+09:00", "dated_only": False, **{k: v for k, v in kw.items()
                                                                        if k == "body_chars"}}
    ok = {"key": "KR:005930", "market": "KR", "ticker": "005930", "sym": "005930", "name": "삼성전자",
          "value": 9e7, "status": "ok", "relevant": [1, 2],
          "items": [it(1, body_chars=300), it(2, "telegram", title="삼성전자 글", summary="삼성전자 글 전체",
                                               link="", source="텔레그램"),
                    it(3, title="<script>alert(1)</script>")],
          "pros": [{"t": "HBM 공급 확대", "refs": [1]}], "cons": [{"t": "노조 리스크", "refs": [2]}]}
    none_ = {"key": "US:NVDA", "market": "US", "ticker": "NVDA", "sym": "NVDA", "name": "엔비디아",
             "value": 5e7, "status": "ok", "relevant": [], "items": [it(1)], "pros": [], "cons": []}
    fail = {"key": "JP:7203.T", "market": "JP", "ticker": "7203.T", "sym": "7203.T", "name": "도요타",
            "value": 3e7, "status": "failed", "reason": "AI 응답을 못 읽음", "items": [it(1)],
            "pros": [], "cons": []}
    pend = {"key": "KR:247540", "market": "KR", "ticker": "247540", "sym": "247540.KQ", "name": "에코프로비엠",
            "value": 2e7, "status": "pending", "reason": "다음 주기에", "items": [it(1)], "pros": [], "cons": []}
    quiet = {"key": "KR:035720", "market": "KR", "ticker": "035720", "name": "카카오", "value": 1e7,
             "status": "empty", "items": [], "pros": [], "cons": []}
    return {"date": "2026-10-12",
            "window": {"since": "2026-10-11T07:15:00+09:00", "until": "2026-10-12T07:15:00+09:00"},
            "universe": {"n": 5, "etf": ["KODEX 200"], "unmatched": ["알수없는상품"]},
            "sources": {"naver": 3, "yfinance": 1, "fallback": {"네이버 키 없음": 1}, "body_try": 2, "body_ok": 1,
                        "dropped_name": 4, "telegram": {"window": 9, "matched": 1}},
            "stocks": [ok, none_, fail, pend, quiet], "llm": {"calls": 2, "cost_usd": 0.0123}}


def test_render_cards_and_status(hn):
    rec = _page_rec()
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN")
    kind, line = hn.status_verdict([rec], _t("2026-10-12T09:00"), {})
    import html as _h
    assert f'data-kind="{kind}">{_h.escape(line)}</div>' in html            # 화면 = 판정 함수(#35)
    card = html.split("data-q='삼성전자 005930'")[1].split("data-hncard")[0]
    assert "👍 Pros · 호재 관점" in card and "HBM 공급 확대<span class='hn-ref'>[1]</span>" in card
    assert "노조 리스크" in card and "<li class='hn-off'>[3]" in card       # relevant 밖은 흐리게
    assert "그 회사 얘기가 아니라고 본 1건은 흐리게" in card
    assert "본문 앞부분을 AI 가 읽음" in card and "📨 텔레그램" in card
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html
    assert "관련 있다고 본 항목이 없음" in html.split("data-q='엔비디아 NVDA'")[1].split("data-hncard")[0]
    assert "Pros/Cons 못 만듦(3회 실패) — AI 응답을 못 읽음" in html
    assert "Pros/Cons 대기 — 다음 주기에" in html
    assert "새 뉴스 없음 1종목: 카카오" in html
    assert "ETF 1개" in html and "티커 미확인 1개" in html and "KODEX 200" in html
    assert "본문 읽음 1/2" in html and "제목·요약에 이름이 없어 뺀 검색 결과 4건" in html
    assert "대체 원천으로 받은 종목 1(네이버 키 없음)" in html and "AI 2회 · $0.0123" in html
    assert "<div class='v'>4 / 5</div>" in html


def test_render_links_only_http(hn):
    """원천이 준 링크가 `javascript:` 면 href 로 싣지 않는다 — escape 로는 그 스킴이 안 막힌다."""
    rec = _page_rec()
    rec["stocks"][0]["items"][0]["link"] = "javascript:alert(1)"
    rec["stocks"][0]["items"][2]["link"] = " HTTPS://ok.example/x "
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN")
    assert "javascript:" not in html and "기사 1" in html
    assert "href='HTTPS://ok.example/x'" in html


def test_render_lookup_links_follow_the_resolver(hn):
    """국내 6자리는 접미 없이(리졸버가 KS/KQ 를 가른다 — `.KS` 를 붙이면 코스닥이 빈 화면, #399)."""
    html = hn.render_page([_page_rec()], {}, _t("2026-10-12T09:00"), gen="GEN")
    assert "href='lookup/005930'" in html and "href='lookup/247540'" in html
    assert "lookup/247540.KS" not in html and "lookup/005930.KS" not in html
    assert "href='lookup/NVDA'" in html and "href='lookup/7203.T'" in html
    assert hn._lookup({"market": "KR", "ticker": "0001A0", "sym": "0001A0.KQ"}) == "lookup/0001A0.KQ"
    from bot.dashboard import resolve_name_to_ticker
    assert resolve_name_to_ticker("005930") in ("005930.KS", "005930.KQ")   # 링크가 실제로 풀린다


def test_render_shows_window_and_open_day_and_filters(hn, monkeypatch):
    monkeypatch.setattr(hn, "RENDER_DAYS", 2)
    recs = [dict(_page_rec(), date=d) for d in ("2026-10-10", "2026-10-11", "2026-10-12")]
    html = hn.render_page(recs, {}, _t("2026-10-12T09:00"), gen="GEN")
    assert "총 3일 기록 중 최근 2일 표시(그 앞은 보관만)" in html
    assert "📅 2026-10-12 (월)" in html and "📅 2026-10-10" not in html
    # 칸 하나씩 잘라서 본다 — 페이지 끝 JS 에도 'data-hncard' 선택자가 있다(#75)
    days = [c.split("</details></div>")[0] if i == 0 else c.split("</details>")[0]
            for i, c in enumerate(html.split("<details class='hn-day'")[1:])]
    assert len(days) == 2 and "data-open='1' open>" in days[0]
    # 지난 날은 머리만 — 본문은 펼칠 때 조각에서(하루 0.7MB × 30일 = 21MB 를 한 페이지에 싣지 않는다)
    assert "data-open='0' data-lazy='hn_d_2026-10-11.html'>" in days[1]
    assert "data-hncard" not in days[1] and "<div class='hn-lazy'>펼치면 불러옵니다…</div>" in days[1]
    assert "📅 2026-10-11 (일) · 뉴스 4/5종목" in days[1]                  # 머리는 그대로 센다
    assert "data-mk='KR' data-pro='1' data-con='1' data-tg='1'" in html
    assert "뉴스 창 10-11 07:15 ~ 10-12 07:15" in html
    assert "내용이 바뀐 시각 GEN" in html


def test_render_empty_page_says_when_it_runs(hn):
    html = hn.render_page([], {}, _t("2026-10-12T06:00"), gen="GEN")
    assert "아직 기록이 없습니다 — 매일 07:10 KST 이후 자동으로 모읍니다." in html


def test_inline_js_parses(hn, tmp_path):
    """생성한 JS 는 파이썬이 문법을 안 봐준다(#26) — 파서에 태운다."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    f = tmp_path / "hn.js"
    f.write_text(hn._HN_JS, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_cost_line_three_windows(hn, tmp_path):
    now = _t("2026-10-12T09:00")
    rows = [(0.001, now - timedelta(hours=1)), (0.002, now - timedelta(days=5)),
            (0.004, now - timedelta(days=20)), (1.0, now)]
    with open(tmp_path / "usage.jsonl", "w", encoding="utf-8") as fh:
        for i, (c, ts) in enumerate(rows):
            sub = "holdings_news" if i < 3 else "screener"
            fh.write(json.dumps({"type": "llm_call", "subsystem": sub, "cost_usd": c,
                                 "ts": ts.timestamp(), "note": "holdings_news 옆 화면"}) + "\n")
        fh.write("holdings_news 깨진 줄\n")
    from bot.usage_tracker import KRW_PER_USD
    exp = [round(x * KRW_PER_USD) for x in (0.001, 0.003, 0.007)]
    assert hn._cost_line(now) == f"₩{exp[0]:,} / ₩{exp[1]:,} / ₩{exp[2]:,}"


def test_regenerate_writes_only_when_content_changes(hn, tmp_path, monkeypatch):
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "arch")
    hn.save_record(_page_rec())
    assert hn.regenerate(_t("2026-10-12T09:00")) is True
    page = tmp_path / "arch" / "holdings_news.html"
    first = page.read_text(encoding="utf-8")
    assert "내용이 바뀐 시각 2026-10-12 09:00 KST" in first and "__updBanner" in first
    assert hn.regenerate(_t("2026-10-12T09:15")) is False                # 생성 시각만 달라졌다
    assert page.read_text(encoding="utf-8") == first
    rec = _page_rec()
    rec["stocks"][0]["pros"] = [{"t": "새 호재", "refs": [1]}]
    hn.save_record(rec)
    assert hn.regenerate(_t("2026-10-12T09:30")) is True and "새 호재" in page.read_text(encoding="utf-8")


def test_day_fragments_hold_only_the_past_days_in_view(hn, monkeypatch):
    monkeypatch.setattr(hn, "RENDER_DAYS", 3)
    recs = [dict(_page_rec(), date=d) for d in ("2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12")]
    fr = hn.day_fragments(recs)
    assert sorted(fr) == ["hn_d_2026-10-10.html", "hn_d_2026-10-11.html"]     # 최신일·창 밖은 없다
    body = fr["hn_d_2026-10-11.html"]
    assert body == hn._day_body(recs[2]) and "data-q='삼성전자 005930'" in body and "<details" not in body[:20]


def test_regenerate_writes_fragments_once_and_prunes_old_ones(hn, tmp_path, monkeypatch):
    import bot.dashboard as d
    arch = tmp_path / "arch"
    monkeypatch.setattr(d, "ARCHIVE_ROOT", arch)
    monkeypatch.setattr(hn, "RENDER_DAYS", 2)
    arch.mkdir()
    (arch / "hn_d_2026-09-01.html").write_text("옛 조각", encoding="utf-8")   # 창 밖으로 밀려난 날
    (arch / "other.html").write_text("남의 파일", encoding="utf-8")
    for dd in ("2026-10-10", "2026-10-11", "2026-10-12"):
        hn.save_record(dict(_page_rec(), date=dd))
    assert hn.regenerate(_t("2026-10-12T09:00")) is True
    names = sorted(p.name for p in arch.iterdir())
    assert names == ["hn_d_2026-10-11.html", "holdings_news.html", "holdings_news.html.sig", "other.html"]
    frag = arch / "hn_d_2026-10-11.html"
    assert "data-q='삼성전자 005930'" in frag.read_text(encoding="utf-8")
    stamp = frag.stat().st_mtime_ns
    time.sleep(0.01)
    rec = dict(_page_rec(), date="2026-10-12")
    rec["stocks"][0]["pros"] = [{"t": "오늘만 바뀜", "refs": [1]}]
    hn.save_record(rec)
    assert hn.regenerate(_t("2026-10-12T09:30")) is True                  # 페이지는 바뀌었다
    assert frag.stat().st_mtime_ns == stamp                               # 어제 조각은 같아서 안 쓴다


_DOM_HARNESS = r"""
// 가짜 DOM — holdings_news 페이지 JS 를 실제로 태운다(#253). argv: page.html frags.json script.js
const fs = require('fs');
const page = fs.readFileSync(process.argv[2], 'utf8');
const frags = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const src = fs.readFileSync(process.argv[4], 'utf8');
function mkcls() { const s = new Set(); return { add: x => s.add(x), remove: x => s.delete(x),
  toggle: (x, f) => { if (f === undefined) f = !s.has(x); if (f) s.add(x); else s.delete(x); }, contains: x => s.has(x) }; }
class El {
  constructor(tag, a, kids) { this.tag = tag; this.a = a || {}; this.kids = []; this.style = { display: '' };
    this.classList = mkcls(); this.ev = {}; this.open = false; this.textContent = ''; this.value = ''; this.parent = null;
    (kids || []).forEach(k => this.append(k)); }
  append(k) { k.parent = this; this.kids.push(k); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.a, k) ? this.a[k] : null; }
  addEventListener(t, f) { (this.ev[t] = this.ev[t] || []).push(f); }
  fire(t) { (this.ev[t] || []).forEach(f => f()); }
  all() { let o = []; for (const k of this.kids) { o.push(k); o = o.concat(k.all()); } return o; }
  querySelectorAll(sel) { return this.all().filter(e => matches(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  set outerHTML(h) { const p = this.parent, i = p.kids.indexOf(this), made = cardsOf(h);
    p.kids.splice(i, 1, ...made); made.forEach(m => { m.parent = p; }); this.parent = null; }
}
function matches(e, sel) {
  if (sel === '[data-hnf]') return e.getAttribute('data-hnf') !== null;
  if (sel === '[data-hncard]') return e.getAttribute('data-hncard') !== null;
  if (sel === 'details[data-hnday]') return e.tag === 'details' && e.getAttribute('data-hnday') !== null;
  if (sel === '.hn-lazy') return e.getAttribute('class') === 'hn-lazy';
  throw new Error('하네스가 모르는 선택자: ' + sel);
}
function attrs(s) { const a = {}; const re = /([a-z-]+)='([^']*)'/g; let x; while ((x = re.exec(s))) a[x[1]] = x[2]; return a; }
function cardsOf(h) { const out = []; const re = /<div (class='hn-card'[^>]*)>/g; let m;
  while ((m = re.exec(h))) out.push(new El('div', attrs(m[1]))); return out; }
const pills = []; { const re = /<span (class='pill[^']*' data-hnf='[^']*')>/g; let m; while ((m = re.exec(page))) pills.push(new El('span', attrs(m[1]))); }
const days = page.split("<details class='hn-day'").slice(1).map(chunk => {
  const head = chunk.slice(0, chunk.indexOf('>'));
  const d = new El('details', attrs(head), cardsOf(chunk));
  d.open = /\sopen(\s|$)/.test(head);
  if (chunk.includes("<div class='hn-lazy'>")) d.append(new El('div', { class: 'hn-lazy' }));
  return d; });
const root = new El('root', {}, pills.concat(days));
const q = new El('input', { id: 'hn-q' }), st = new El('div', { id: 'hn-st' });
st.textContent = (page.match(/<div id="hn-st" class="sub">([^<]*)<\/div>/) || [])[1] || '';
global.document = { getElementById: id => (id === 'hn-q' ? q : id === 'hn-st' ? st : null),
                    querySelectorAll: sel => root.querySelectorAll(sel) };
const fetched = [];
global.fetch = (url) => { fetched.push(url); return Promise.resolve(Object.prototype.hasOwnProperty.call(frags, url)
  ? { ok: true, text: () => Promise.resolve(frags[url]) } : { ok: false, status: 404 }); };
const flush = () => new Promise(r => setTimeout(r, 0));
eval(src);
const vis = () => root.querySelectorAll('[data-hncard]').filter(c => c.style.display !== 'none').map(c => c.getAttribute('data-q'));
const snap = () => ({ st: st.textContent, vis: vis(), days: days.map(d => ({ lazy: d.getAttribute('data-lazy'),
  display: d.style.display, open: d.open, note: (d.querySelector('.hn-lazy') || {}).textContent || '' })), fetched: fetched.slice() });
(async () => {
  const out = { base: snap() };
  q.value = '카카'; q.fire('input'); out.search = snap();
  days[1].open = true; days[1].fire('toggle'); await flush(); await flush(); out.loaded = snap();
  days[2].open = true; days[2].fire('toggle'); await flush(); await flush(); out.failed = snap();
  q.value = ''; pills.find(p => p.getAttribute('data-hnf') === 'XX').fire('click'); out.overseas = snap();
  pills.find(p => p.getAttribute('data-hnf') === 'all').fire('click'); out.cleared = snap();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(3); });
"""


def test_page_js_filters_loaded_days_and_loads_past_days_on_open(hn, tmp_path):
    """생성한 JS 는 **실행까지** 태운다(#253) — 가짜 DOM 위에서 검색·필터·지난 날 불러오기·실패 문구."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    today = _page_rec()
    yday = {**_page_rec(), "date": "2026-10-11",
            "stocks": [{**_page_rec()["stocks"][4], "key": "KR:035720", "items": [
                {"n": 1, "src": "naver", "title": "카카오 기사", "summary": "", "link": "https://p/k",
                 "source": "언론", "ts": "2026-10-11T06:00+09:00", "dated_only": False}],
                "status": "ok", "relevant": [1], "pros": [{"t": "p", "refs": [1]}], "cons": []}]}
    old = {**_page_rec(), "date": "2026-10-10"}
    recs = [today, yday, old]
    page = hn.render_page(recs, {}, _t("2026-10-12T09:00"), gen="GEN")
    frags = {k: v for k, v in hn.day_fragments(recs).items() if k.endswith("10-11.html")}   # 10-10 은 404
    (tmp_path / "page.html").write_text(page, encoding="utf-8")
    (tmp_path / "frags.json").write_text(json.dumps(frags, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "page.js").write_text(hn._HN_JS, encoding="utf-8")
    (tmp_path / "harness.js").write_text(_DOM_HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(tmp_path / "harness.js"), str(tmp_path / "page.html"), str(tmp_path / "frags.json"),
                        str(tmp_path / "page.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["base"]["st"] == "총 3일 기록" and len(o["base"]["vis"]) == 4
    s = o["search"]                                                       # '카카' — 오늘 칸엔 없다
    assert s["vis"] == [] and s["st"] == "조건에 맞는 카드 0개 · 아직 안 연 날 2일은 펼치면 같이 걸러집니다"
    assert s["days"][0]["display"] == "none" and s["days"][1]["display"] == "" and s["fetched"] == []
    ld = o["loaded"]                                                      # 어제를 펼치면 불러와 같이 거른다
    assert ld["fetched"] == ["hn_d_2026-10-11.html"] and ld["vis"] == ["카카오 035720"]
    assert ld["st"] == "조건에 맞는 카드 1개 · 아직 안 연 날 1일은 펼치면 같이 걸러집니다"
    fl = o["failed"]                                                      # 조각이 없으면 사람 말로
    assert fl["days"][2]["note"] == "불러오기 실패 — 접었다 다시 펼쳐 보세요."
    ov = o["overseas"]                                                    # 해외 필터
    assert sorted(ov["vis"]) == ["도요타 7203.T", "엔비디아 NVDA"]
    cl = o["cleared"]
    assert cl["st"] == "총 3일 기록" and [d["open"] for d in cl["days"]] == [True, False, False]
    assert len(cl["vis"]) == 5                                            # 불러온 어제 카드까지 다시 보인다


# ── 텔레그램 수신함 ──────────────────────────────────────────────────────────
class _Msg:
    def __init__(self, mid, text="", date=None, *, fwd=None, doc=None, name=""):
        self.id = mid
        self.message = text
        self.date = date or datetime.now(timezone.utc)
        self.fwd_from = fwd
        self.document = doc
        self.file = types.SimpleNamespace(name=name, ext="") if name else None


def test_tg_record_shapes(hn):
    m = _Msg(5, "  삼성전자 글  ", datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc), fwd=object())
    r = hn.tg_record(m, "@rag")
    assert r == {"id": 5, "ts": "2026-10-12T07:00:00+09:00", "chan": "@rag", "fwd": True, "text": "삼성전자 글"}
    assert hn.tg_record(_Msg(6, ""), "@rag") is None                   # 사진·파일만인 메시지
    assert len(hn.tg_record(_Msg(7, "가" * 9000), "@rag")["text"]) == hn.TG_TEXT_CAP


def test_tg_inbox_dedupes_and_counts_bad_lines(hn):
    _tg(hn, (1, "2026-10-12T06:00:00+09:00", "옛 글", False), (2, "2026-10-12T05:00:00+09:00", "b", False))
    _tg(hn, (1, "2026-10-12T06:00:00+09:00", "같은 글 다시", False))
    with open(hn.TG_INBOX, "a", encoding="utf-8") as fh:
        fh.write("{깨짐\n" + json.dumps({"id": 3, "ts": "언제", "chan": "@rag", "text": "x"}) + "\n")
    recs, bad = hn._tg_read()
    assert [r["id"] for r in recs] == [2, 1] and recs[1]["text"] == "같은 글 다시" and bad == 2


def test_tg_prune_keeps_recent_and_drops_bad(hn):
    now = _t(_NOW)
    _tg(hn, (1, (now - timedelta(days=20)).isoformat(), "옛 글", False),
        (2, (now - timedelta(days=1)).isoformat(), "새 글", False))
    with open(hn.TG_INBOX, "a", encoding="utf-8") as fh:
        fh.write("{깨짐\n")
    assert hn.tg_prune(now) == 1
    assert hn._tg_read() == ([json.loads(hn.TG_INBOX.read_text(encoding="utf-8"))], 0)
    stamp = hn.TG_INBOX.stat().st_mtime_ns
    time.sleep(0.01)
    assert hn.tg_prune(now) == 0 and hn.TG_INBOX.stat().st_mtime_ns == stamp   # 바꿀 게 없으면 안 쓴다


def test_portfolio_watch_capture(hn):
    import bot.portfolio_watch as pw
    assert pw._capture([_Msg(1, "삼성전자 글"), _Msg(2, "")], "@rag") == 1
    assert [r["id"] for r in hn._tg_read()[0]] == [1]


def test_portfolio_watch_capture_failure_is_loud(hn, monkeypatch, caplog):
    import bot.portfolio_watch as pw
    monkeypatch.setattr(hn, "tg_append", lambda recs: (_ for _ in ()).throw(OSError("disk")))
    with caplog.at_level("WARNING", logger="bot.portfolio_watch"):
        assert pw._capture([_Msg(1, "x")], "@rag") == 0
    assert any("보유종목 News 수신함 기록 실패(1건): disk" in r.getMessage() for r in caplog.records)


def _fake_telethon(monkeypatch, client):
    tl = types.ModuleType("telethon")
    tl.TelegramClient = lambda *a, **k: client
    errs = types.ModuleType("telethon.errors")

    class _E(Exception):
        seconds = 0
    errs.SessionPasswordNeededError = _E
    errs.FloodWaitError = _E
    tl.errors = errs
    monkeypatch.setitem(sys.modules, "telethon", tl)
    monkeypatch.setitem(sys.modules, "telethon.errors", errs)


class _Client:
    def __init__(self, new, old):
        self.new, self.old, self.calls = new, old, []

    async def start(self):
        return None

    async def disconnect(self):
        return None

    def iter_messages(self, chan, **kw):
        self.calls.append(kw)
        src = self.new if "min_id" in kw else self.old

        async def gen():
            for m in src:
                yield m
        return gen()


def test_portfolio_watch_feeds_the_inbox_and_seeds_once(hn, tmp_path, monkeypatch):
    """새 글은 그 주기에, 이 기능 전에 '본 것' 으로 넘긴 최근 사흘 글은 **한 번만** 거슬러 담는다 —
    사흘 넘은 글·자산 문서·이번 주기 새 글은 첫 채움에 안 들어간다."""
    import bot.portfolio_watch as pw
    monkeypatch.setattr(pw, "_SEEN_PATH", tmp_path / "seen.json")
    for k, v in (("TG_USER_API_ID", "123"), ("TG_USER_API_HASH", "h"), ("TG_RAG_CHANNEL", "@rag"),
                 ("TG_USER_SESSION", str(tmp_path / "sess"))):
        monkeypatch.setenv(k, v)
    pw._save_seen({"initialized": True, "ids": [100], "last_msg_id": 100})
    now = datetime.now(timezone.utc)
    new = [_Msg(101, "삼성전자 새 글", now), _Msg(102, "", now)]
    old = [_Msg(104, "두 번 읽는 사이에 온 글", now),          # 이번 주기 새 글 — 다음 주기 몫(첫 채움 아님)
           _Msg(101, "삼성전자 새 글", now), _Msg(100, "어제 글", now - timedelta(days=1)),
           _Msg(95, "10월 자산 파일", now, doc=object(), name="2026.zip"),   # 자산 문서의 캡션
           _Msg(90, "닷새 전 글", now - timedelta(days=5))]
    c = _Client(new, old)
    _fake_telethon(monkeypatch, c)
    assert asyncio.run(pw._run_async()) == 0
    assert sorted(r["id"] for r in hn._tg_read()[0]) == [100, 101]
    st = pw._load_seen()
    assert st["tg_seeded"] is True and st["last_msg_id"] == 102
    assert c.calls == [{"min_id": 100, "limit": pw._NEW_MSG_CAP}, {"limit": pw._TG_SEED_LIMIT}]
    c2 = _Client([_Msg(103, "카카오 글", now)], old)
    _fake_telethon(monkeypatch, c2)
    assert asyncio.run(pw._run_async()) == 0
    assert c2.calls == [{"min_id": 102, "limit": pw._NEW_MSG_CAP}]     # 첫 채움은 다시 안 한다
    assert sorted(r["id"] for r in hn._tg_read()[0]) == [100, 101, 103]
    assert c2.calls and 104 not in {r["id"] for r in hn._tg_read()[0]}


# ── 진단 ─────────────────────────────────────────────────────────────────────
def _tree(p):
    return sorted((str(x.relative_to(p)), x.stat().st_mtime_ns) for x in p.rglob("*")) if p.exists() else []


def test_why_is_read_only_and_ends_with_a_verdict(hn, portfolio, tmp_path):
    hn.save_record(_page_rec())
    before = _tree(tmp_path)
    out = []
    rc = hn.why(_t("2026-10-12T09:00"), out=out.append)
    assert _tree(tmp_path) == before                                    # 아무것도 안 쓴다(#264)
    assert out[0].startswith("# holdings_news --why · 지문 ")
    assert out[-1].startswith("판정: ⏳ ") and rc == 0                    # pending → 대기
    assert any(ln.startswith("① 보유 종목: 2종목(국내 1 · 해외 1)") for ln in out)
    hn._save_state({"reason": "주기 실패(X)"})
    rc2 = hn.why(_t("2026-10-13T09:00"), out=(out2 := []).append)
    assert rc2 == 1 and out2[-1].startswith("판정: ❌ 오늘 집계 못 함")


def test_why_says_when_the_inbox_is_empty(hn, portfolio, monkeypatch):
    monkeypatch.delenv("TG_RAG_CHANNEL", raising=False)
    out = []
    hn.why(_t("2026-10-12T09:00"), out=out.append)
    tg = [ln for ln in out if ln.startswith("③")][0]
    assert "TG_RAG_CHANNEL 미설정" in tg and "portfolio-watch.timer" in tg


def test_probe_writes_nothing_of_ours(hn, portfolio, monkeypatch, tmp_path):
    monkeypatch.setattr(hn, "fetch_stock_news", lambda s: (
        [_it(f"{s['name']} 기사", "2026-10-12T06:00:00+09:00", link="https://p/1",
             naver_link="https://n.news.naver.com/a/1")], "naver", ""))
    monkeypatch.setattr(hn, "_fetch_body", lambda link: "본문내용")
    out = []
    assert hn.probe(None, out=out.append) == 0
    assert not hn._DIR.exists()                                         # 이 보드의 기록·상태는 안 쓴다
    assert any("본문(네이버 호스팅 1건): 4자" in ln for ln in out) and out[-1] == "✅ 2/2종목 원천 응답"
    o2 = []
    assert hn.probe(["999999"], out=o2.append) == 0                    # 보유 밖 티커도 잰다
    assert any("(KR:999999)" in ln for ln in o2)


def test_main_dispatches(hn, monkeypatch):
    dotenv = pytest.importorskip("dotenv")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)    # 테스트가 운영 .env 를 싣지 않게
    got = []
    monkeypatch.setattr(hn, "why", lambda: got.append("why") or 0)
    monkeypatch.setattr(hn, "probe", lambda t: got.append(("probe", t)) or 0)
    monkeypatch.setattr(hn, "tick_and_render", lambda: got.append("run") or {"status": "ok"})
    assert hn.main([]) == 0 and hn.main(["--probe", "AAPL"]) == 0 and hn.main(["--run"]) == 0
    assert got == ["why", ("probe", ["AAPL"]), "run"]


# ── 배선 ─────────────────────────────────────────────────────────────────────
def test_nav_puts_it_right_after_asia_on_the_hub():
    from bot.dashboard import _render_index, _render_market_page, _render_portfolio_page
    hub = _render_market_page({})
    asia = 'href="asia.html">🏯 ASIA</a>'
    a = hub.index(asia) + len(asia)
    b = hub.index('<a href="holdings_news.html">🗞️ 보유종목 News</a>')
    assert a < b and "<a" not in hub[a:b], hub[a:b]                      # ASIA 바로 다음(사이에 다른 링크 없음)
    idx = _render_index([])[0]
    assert idx.index("asia.html") < idx.index("holdings_news.html") < idx.index("screener.html")
    assert 'href="holdings_news.html"' in _render_portfolio_page(None)


def test_cost_card_has_its_own_bucket(monkeypatch):
    import bot.dashboard as d
    now = time.time()
    monkeypatch.setattr(d, "_read_usage_records", lambda *a, **k: [
        {"type": "llm_call", "model": "gemini-2.5-flash-lite", "cost_usd": 0.5, "ts": now,
         "subsystem": "holdings_news"},
        {"type": "llm_call", "model": "gemini-2.5-flash", "cost_usd": 0.25, "ts": now}])
    monkeypatch.setattr(d, "_read_usage_rollup_usd", lambda: 0.0)
    st = dict(d._compute_stats([]))
    assert st["month_cost_by_sub_usd"]["보유종목 News"] == 0.5
    assert st["month_cost_by_sub_usd"]["분석"] == 0.25                    # 종목분석에 접히지 않는다
    st["total"] = 1
    assert "보유종목 News" in d._render_stats_panel(st)


def test_usage_report_has_its_own_line(monkeypatch):
    pytest.importorskip("telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t" * 12)                  # 모듈 import 가 요구한다
    import bot.telegram_bot as tb
    import bot.usage_tracker as ut
    rows = [{"type": "llm_call", "model": "gemini-2.5-flash-lite", "cost_usd": 1.0, "ts": time.time(),
             "subsystem": "holdings_news"}]
    monkeypatch.setattr(ut, "load_records", lambda *a, **k: rows)
    monkeypatch.setattr(tb, "_count_watchdog_restarts_24h", lambda: 0)
    out = tb._build_usage_report()
    line = [ln for ln in out.splitlines() if "보유종목 News" in ln]
    assert line and "₩0" not in line[0], out
    an = [ln for ln in out.splitlines() if ln.strip().startswith("• 분석")]
    assert an and "₩0" in an[0].replace(" ", ""), an             # 분석 버킷에서 빠진다


def test_help_and_periodic_task_are_wired():
    import ast
    src = open("bot/telegram_bot.py", encoding="utf-8").read()
    tree = ast.parse(src)
    help_txt = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "_HELP_TEXT" for t in n.targets)][0]
    assert "🗞️보유종목 News(매일 07:10" in ast.literal_eval(help_txt.value)
    starts = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)
              and ast.unparse(n.func) == "asyncio.create_task"]
    assert "asyncio.create_task(_periodic_holdings_news())" in starts
    fn = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
          and n.name == "_periodic_holdings_news"][0]
    body = ast.unparse(fn)
    assert "asyncio.to_thread(_hn_tick)" in body and "asyncio.wait_for" in body


@pytest.mark.parametrize("recs, st, mark", [
    ([_done({"status": "ok", "items": [1]})], {}, "✅"),
    ([], {}, "⚠️"),
    ([_done({"status": "pending", "items": [1]})], {}, "⚠️"),
    ([_done({"status": "failed", "items": [1]})], {}, "❌"),
    ([], {"no_holdings": True}, "❓"),
])
def test_board_audit_line(recs, st, mark):
    from bot.scripts.board_audit import _holdings_news_line
    ln = _holdings_news_line(recs, st, _t("2026-10-12T09:00") if recs else _t("2026-10-12T06:00"))
    assert ln.startswith(f"   {mark} 기록 {len(recs)}일 · "), ln


def test_board_audit_line_survives_errors(monkeypatch):
    import bot.holdings_news as m
    from bot.scripts.board_audit import _holdings_news_line
    monkeypatch.setattr(m, "status_verdict", lambda *a: (_ for _ in ()).throw(ValueError("x")))
    assert _holdings_news_line([], {}) == "   ❓ 조회 실패 ValueError: x"


def test_module_docs_name_every_path_it_writes():
    """저장 위치가 문서와 갈리면 운영자가 엉뚱한 파일을 본다(#55)."""
    import bot.holdings_news as m
    doc = m.__doc__ or ""
    for p in ("days/YYYY-MM-DD.json", "state.json", "tg_inbox.jsonl"):
        assert p in doc
    assert os.path.basename(str(m.TG_INBOX)) == "tg_inbox.jsonl"
