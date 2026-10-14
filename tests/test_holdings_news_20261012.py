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
    # 이 파일의 시계(07:15 tick · 06:00 대기 등)는 집계 시각 07:10 을 두고 짰다. 사용자가 시각을 바꾸는 것
    # (2026-10-13 07:10→08:00)은 값이지 계약이 아니므로, 시각에 걸린 계약은 그 시각에 묶어 잰다(#425).
    monkeypatch.setattr(m, "RUN_HOUR", 7)
    monkeypatch.setattr(m, "RUN_MIN", 10)
    monkeypatch.setattr(m, "_DIR", d)
    monkeypatch.setattr(m, "_DAYS", d / "days")
    monkeypatch.setattr(m, "_STATE", d / "state.json")
    monkeypatch.setattr(m, "TG_INBOX", d / "tg_inbox.jsonl")
    monkeypatch.setattr(m, "_TG_LOCK", d / ".tg_inbox.lock")
    monkeypatch.setattr(m, "_RUN_LOCK", d / ".run.lock")
    monkeypatch.setattr(ut, "USAGE_LOG", tmp_path / "usage.jsonl")
    monkeypatch.setattr(ut, "ROLLUP_PATH", tmp_path / "usage_rollup.json")
    # 바깥 조회 둘(미국 ETF 목록·DART 한글명)은 **원천 쪽에서** 막는다 — 샌드박스에선 프록시가 소켓 가드를
    # 지나 실제로 나갔다(#312). 래퍼(`_us_etf`·`_dart_ko`)는 그대로 둬서 같이 잰다.
    import bot.dart_client as dc
    import bot.us_symbol_names as us
    monkeypatch.setattr(us, "us_etf_symbols", lambda **k: set())
    monkeypatch.setattr(dc, "get_dart", lambda: types.SimpleNamespace(news_search_name=lambda c: None))
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


_LONG = "삼성전자가 HBM4 를 공급한다. " + "공급 일정과 물량 설명이 이어진다. " * 12     # 200자 넘는 본문


def test_extract_naver_body(hn):
    html = ("<html><article id='dic_area' class='x'><script>var a=1;</script>"
            f"{_LONG.replace('HBM4', '<b>HBM4</b>')}&nbsp;끝 문장.<br>"
            "Copyright ⓒ 언론사 무단 전재</article></html>")
    b = hn.extract_naver_body(html)
    assert b.startswith("삼성전자가 HBM4 를 공급한다.") and b.endswith("끝 문장.")
    assert "Copyright" not in b and "var a" not in b
    # article 태그가 없어도 dic_area 시작점에서 읽는다
    assert hn.extract_naver_body(f"<div id=\"dic_area\">본문 일부 {_LONG}</div>").startswith("본문 일부")
    assert hn.extract_naver_body("<div>다른 구조</div>") == "" and hn.extract_naver_body("") == ""
    assert len(hn.extract_naver_body("<article id='dic_area'>" + "가" * 5000 + "</article>")) == hn.BODY_CHARS


def test_extract_naver_body_keeps_text_after_a_photo_credit(hn):
    """맨 위 사진 출처('ⓒ 뉴스1 DB')에서 자르면 본문이 사진 설명 한 줄로 줄어든 채 '읽음' 으로 세졌다
    (리뷰 2026-10-12 합성 화면 실측) — 저작권 꼬리는 `FOOT_MIN` 글자 뒤에서만 자른다."""
    html = (f"<article id='dic_area'>삼성전자 서초사옥 ⓒ 뉴스1 DB<br>{_LONG}"
            "<br>Copyright ⓒ 뉴스1. All rights reserved. 무단 전재 및 재배포 금지</article>")
    b = hn.extract_naver_body(html)
    assert b.startswith("삼성전자 서초사옥 ⓒ 뉴스1 DB 삼성전자가 HBM4") and "All rights" not in b
    assert hn.extract_naver_body("<article id='dic_area'>삼성전자 서초사옥 ⓒ 뉴스1 DB</article>") == ""
    # 너무 짧게 뽑히면(사진 설명만·구조 변경) 본문으로 세지 않는다
    assert hn.extract_naver_body("<article id='dic_area'>" + "가" * (hn.BODY_MIN - 1) + "</article>") == ""
    assert hn.extract_naver_body("<article id='dic_area'>" + "가" * hn.BODY_MIN + "</article>") == "가" * 80


def test_fetch_body_only_reads_naver_hosts(hn, monkeypatch):
    import requests

    import bot.finviz_client as fc
    monkeypatch.setattr(fc, "naver_paused", lambda: False)
    got = []

    def fake_get(url, **kw):
        got.append(url)
        return types.SimpleNamespace(status_code=200, text=f"<article id='dic_area'>{_LONG}</article>")
    monkeypatch.setattr(requests, "get", fake_get)
    assert hn._fetch_body("https://www.press.com/news/1") == ""        # 언론사 원문은 안 읽는다
    assert hn._fetch_body("https://n.news.naver.com/mnews/article/001/1") == _LONG.strip()
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
        "KR:035720": ([], "", "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음 · 구글 뉴스 실패(로그 참조)"),
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
    # 직전 창 끝(10-11 07:15)보다 6시간 앞에서 연다 — 늦게 닿은 입력을 놓치지 않게(겹친 건 지난 기록과 대조)
    assert rec["window"] == {"since": "2026-10-11T01:15:00+09:00", "until": "2026-10-12T07:15:00+09:00"}
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
        "네이버 검색 키(NAVER_CLIENT_ID/SECRET) 없음 · 구글 뉴스 실패(로그 참조)"]
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


def _llm(resp, *, pt=1000, ot=200, why="", kind=None):
    """가짜 `call_llm` — (글, 입력, 출력, 사유, 갈래). 갈래를 안 주면 사유로 정한다(no_key · 거부 · 정상)."""
    calls = []

    def f(prompt):
        calls.append(prompt)
        k = kind if kind is not None else ("no_key" if why == "no_key" else "rejected" if why else "")
        return (resp(prompt) if callable(resp) else resp), pt, ot, why, k
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
    hn.analyze(_rec(u), llm=_llm(None, pt=0, ot=0, why="AI 호출 실패(ClientError: 400 bad)", kind="rejected"))
    assert u["reason"] == "AI 호출 실패(ClientError: 400 bad)" and u["tries"] == 1   # 거부는 시도로 센다


def test_analyze_without_key_says_so_and_spends_nothing(hn):
    rec = _rec(_stock("KR:A"), _stock("KR:B"), _stock("KR:C", status="ok"))
    f = _llm(None, pt=0, ot=0, why="no_key")
    done = hn.analyze(rec, llm=f)
    assert done == {"calls": 0, "analyzed": 0, "failed": 0, "no_key": True, "transient": False}
    assert len(f.calls) == 1
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
    assert res["status"] == "wait" and res["calls"] == 0 and hn.load_records() == []
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
                                                                        if k in ("body_chars", "body_used")}}
    ok = {"key": "KR:005930", "market": "KR", "ticker": "005930", "sym": "005930", "name": "삼성전자",
          "value": 9e7, "status": "ok", "relevant": [1, 2],
          "items": [it(1, body_chars=300, body_used=True), it(2, "telegram", title="삼성전자 글", summary="삼성전자 글 전체",
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
    assert "종목·투자와 무관하다고 본 1건은 흐리게" in card              # 2026-10-13: 다른 회사 + 투자와 무관한 소식
    assert "본문 앞부분을 AI 가 읽음" in card and "📨 텔레그램" in card
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html
    assert "투자와 관련 있다고 본 항목이 없음" in html.split("data-q='엔비디아 NVDA'")[1].split("data-hncard")[0]
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
    fr = hn.day_fragments(recs, _t("2026-10-12T09:00"))
    assert sorted(fr) == ["hn_d_2026-10-10.html", "hn_d_2026-10-11.html"]     # 최신일·창 밖은 없다
    body = fr["hn_d_2026-10-11.html"]
    assert body == hn._day_body(recs[2], now=_t("2026-10-12T09:00")) and "data-q='삼성전자 005930'" in body and "<details" not in body[:20]


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
const cards = () => root.querySelectorAll('[data-hncard]');
const vis = () => cards().filter(c => c.style.display !== 'none').map(c => c.getAttribute('data-q'));
const snap = () => ({ st: st.textContent, vis: vis(), hits: cards().filter(c => c.classList.contains('hn-hit'))
  .map(c => c.getAttribute('data-q')), days: days.map(d => ({ lazy: d.getAttribute('data-lazy'),
  display: d.style.display, open: d.open, note: (d.querySelector('.hn-lazy') || {}).textContent || '' })), fetched: fetched.slice() });
const steps = process.argv[5] ? JSON.parse(fs.readFileSync(process.argv[5], 'utf8')) : null;
(async () => {
  if (steps) {                     // [동작, 인자] 목록 — 시나리오를 테스트가 정한다
    const out = {};
    for (const [op, arg] of steps) {
      if (op === 'search') { q.value = arg; q.fire('input'); }
      else if (op === 'pill') { pills.find(p => p.getAttribute('data-hnf') === arg).fire('click'); }
      else if (op === 'open' || op === 'close') { days[arg].open = op === 'open'; days[arg].fire('toggle');
        await flush(); await flush(); }
      else if (op === 'snap') { out[arg] = snap(); }
      else throw new Error('하네스가 모르는 동작: ' + op);
    }
    console.log(JSON.stringify(out));
    return;
  }
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
    frags = {k: v for k, v in hn.day_fragments(recs, _t("2026-10-12T09:00")).items() if k.endswith("10-11.html")}   # 10-10 은 404
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
def test_nav_puts_it_right_before_asia_on_the_hub():
    """사용자 2026-10-12 'ASIA 옆' → 2026-10-13 'ASIA 앞으로'(#222 다시 쓴다 — 남는 보장: 사이에 다른 링크 없음)."""
    from bot.dashboard import _render_index, _render_market_page, _render_portfolio_page
    hub = _render_market_page({})
    me = '<a href="holdings_news.html">🗞️ 보유종목 News</a>'
    a = hub.index(me) + len(me)
    b = hub.index('<a href="asia.html">🏯 ASIA</a>')
    assert a < b and "<a" not in hub[a:b], hub[a:b]                      # ASIA 바로 앞(사이에 다른 링크 없음)
    idx = _render_index([])[0]
    assert idx.index("holdings_news.html") < idx.index("asia.html") < idx.index("screener.html")
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
    import bot.holdings_news as m
    # 도움말의 시각은 제품 상수에서 — 리터럴로 박으면 사용자가 시각을 바꿀 때마다 무관한 빨간불(#67).
    assert f"🗞️보유종목 News(매일 {m.RUN_HOUR:02d}:{m.RUN_MIN:02d}" in ast.literal_eval(help_txt.value)
    # 가이드의 '15분마다 확인' 은 봇의 실제 주기와 같아야 한다(#55).
    tick = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "_HOLDINGS_NEWS_TICK_SEC" for t in n.targets)][0]
    assert ast.literal_eval(tick.value) == m.TICK_MIN * 60
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
    for p in ("days/YYYY-MM-DD.json", "days/bodies_YYYY-MM-DD.json", "state.json", "tg_inbox.jsonl"):
        assert p in doc
    assert os.path.basename(str(m.TG_INBOX)) == "tg_inbox.jsonl"
    assert m._bodies_path("2026-10-12").relative_to(m._DIR).as_posix() == "days/bodies_2026-10-12.json"


# ── 독립 리뷰 반영(2026-10-12) ───────────────────────────────────────────────
def test_excerpt_centres_on_a_mention_deep_in_a_paragraph(hn):
    """앞에서부터 자르면 긴 머리말 뒤에 나온 종목이 잘려 AI 가 근거를 못 본다(리뷰 실측: 800자 뒤 언급
    → 이름도 내용도 없는 발췌)."""
    text = "가" * 800 + " 삼성전자 HBM4 퀄 통과 " + "나" * 800
    ex = hn.excerpt(text, hn.find_mentions(text, "삼성전자")[0])
    assert "삼성전자 HBM4 퀄 통과" in ex and len(ex) <= hn.SNIPPET_CHARS
    two = "시황 " * 200 + "\n삼성전자\nHBM4 공급 확대"                  # 긴 머리줄 + 이름만 한 줄
    ex2 = hn.excerpt(two, hn.find_mentions(two, "삼성전자")[0])
    assert "삼성전자 HBM4 공급 확대" in ex2 and len(ex2) <= hn.SNIPPET_CHARS
    t = "머리말 " * 45 + "삼성전자 신고가"                                 # 카드 제목(120자)
    title = hn.excerpt(t, hn.find_mentions(t, "삼성전자")[0], 120)
    assert "삼성전자 신고가" in title and len(title) <= 120


def test_collect_telegram_item_carries_the_mention(hn):
    _tg(hn, (9, "2026-10-12T06:30:00+09:00", "시장 정리 " * 120 + "삼성전자 HBM4 퀄 통과", False))
    rec = hn.collect(_t(_NOW), model=_model(_h("삼성전자", "005930", "KR")), records=[],
                     fetch=_fetcher({}), pace=0, us_etf=set(), ko=lambda c: "")
    it = rec["stocks"][0]["items"][0]
    assert "삼성전자 HBM4 퀄 통과" in it["summary"] and "삼성전자" in it["title"]


def _harness(hn, tmp_path, recs, steps, *, frags_ok=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    page = hn.render_page(recs, {}, _t("2026-10-12T09:00"), gen="GEN")
    frags = {k: v for k, v in hn.day_fragments(recs, _t("2026-10-12T09:00")).items() if frags_ok is None or k in frags_ok}
    for name, body in (("page.html", page), ("frags.json", json.dumps(frags, ensure_ascii=False)),
                       ("page.js", hn._HN_JS), ("harness.js", _DOM_HARNESS),
                       ("steps.json", json.dumps(steps, ensure_ascii=False))):
        (tmp_path / name).write_text(body, encoding="utf-8")
    r = subprocess.run([node, *(str(tmp_path / n) for n in ("harness.js", "page.html", "frags.json", "page.js",
                                                            "steps.json"))],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _three_days():
    today = _page_rec()
    yday = {**_page_rec(), "date": "2026-10-11"}
    old = {**_page_rec(), "date": "2026-10-10"}
    return [today, yday, old]


def test_page_js_keeps_a_past_day_open_after_loading_it(hn, tmp_path):
    """검색·필터 없이 지난 날을 펼치면 조각을 불러온 뒤에도 **열려 있어야** 한다 — 불러오기가 `run()` 을
    부르고, 필터가 없을 때 기본 접힘으로 되돌리면 두 번 눌러야 열린다(리뷰 실측)."""
    o = _harness(hn, tmp_path, _three_days(), [["open", 1], ["snap", "opened"], ["search", "삼성"],
                                               ["search", ""], ["snap", "cleared"]])
    op = o["opened"]
    assert op["fetched"] == ["hn_d_2026-10-11.html"] and [d["open"] for d in op["days"]] == [True, True, False]
    assert sorted(op["vis"]) == sorted(["삼성전자 005930", "엔비디아 NVDA", "도요타 7203.T",
                                        "에코프로비엠 247540"] * 2)
    # 필터를 걸었다가 지우면 그때만 기본 접힘으로 돌아간다(필터가 연 날을 닫는다)
    assert [d["open"] for d in o["cleared"]["days"]] == [True, False, False]


def _pill_rec():
    def st(key, mk, *, pro=False, con=False, tg=False):
        items = [{"n": 1, "src": "telegram" if tg else "naver", "title": f"{key} 글", "summary": "",
                  "link": "", "source": "x", "ts": "2026-10-12T06:00+09:00", "dated_only": False}]
        return {"key": f"{mk}:{key}", "market": mk, "ticker": key, "sym": key, "name": key, "value": 1.0,
                "status": "ok", "relevant": [1], "items": items,
                "pros": [{"t": "p", "refs": [1]}] if pro else [], "cons": [{"t": "c", "refs": [1]}] if con else []}
    return {"date": "2026-10-12", "stocks": [st("A1", "KR", pro=True), st("B2", "US", con=True, tg=True),
                                             st("C3", "JP", pro=True, con=True), st("D4", "KR", tg=True)]}


@pytest.mark.parametrize("pill, want", [
    ("KR", ["A1 A1", "D4 D4"]), ("XX", ["B2 B2", "C3 C3"]), ("pro", ["A1 A1", "C3 C3"]),
    ("con", ["B2 B2", "C3 C3"]), ("tg", ["B2 B2", "D4 D4"]), ("all", ["A1 A1", "B2 B2", "C3 C3", "D4 D4"]),
])
def test_page_js_pills_filter_by_their_own_attribute(hn, tmp_path, pill, want):
    """필터마다 **제 속성**만 본다 — 카드 넷의 속성 조합을 서로 다르게 둬야 다른 칸을 보는 변형이 잡힌다(#91c)."""
    o = _harness(hn, tmp_path, [_pill_rec()], [["pill", pill], ["snap", "s"]])
    assert sorted(o["s"]["vis"]) == want


def test_page_js_search_highlight_is_removed_when_cleared(hn, tmp_path):
    o = _harness(hn, tmp_path, [_pill_rec()], [["search", "C3"], ["snap", "on"], ["search", ""], ["snap", "off"]])
    assert o["on"]["hits"] == ["C3 C3"] and o["off"]["hits"] == [] and len(o["off"]["vis"]) == 4


def test_page_js_retries_a_failed_load(hn, tmp_path):
    """불러오기에 실패한 날은 접었다 다시 펼치면 **다시 묻는다** — 진행 표시를 안 풀면 영영 못 연다."""
    o = _harness(hn, tmp_path, _three_days(), [["open", 2], ["close", 2], ["open", 2], ["snap", "s"]],
                 frags_ok=set())
    assert o["s"]["fetched"] == ["hn_d_2026-10-10.html"] * 2
    assert o["s"]["days"][2]["note"] == "불러오기 실패 — 접었다 다시 펼쳐 보세요."


# ── 원천 장애 판정(리뷰 Medium 3) ──────────────────────────────────────────────
def _src_rec(*stocks, fail=0, reasons=None, **got):
    return {"date": "2026-10-12", "stocks": list(stocks),
            "sources": {"fail": fail, "fail_reasons": reasons or ({"원천 X": fail} if fail else {}),
                        "telegram": {"matched": 0}, **got}}


def _s(mk, *, failed=False, raw=3):
    return {"market": mk, "status": "ok", "items": [1] if raw and not failed else [],
            "via": "" if failed else "naver", "via_why": "원천 X" if failed else "", "raw_n": 0 if failed else raw}


@pytest.mark.parametrize("rec, kind, frag", [
    # 몇 종목의 일시 실패는 장애가 아니다 — 그래도 ✅ 줄이 그 수를 말한다(#41)
    (_src_rec(_s("KR", failed=True), *[_s("KR")] * 9, fail=1, naver=9), "ok", "원천 못 받은 종목 1"),
    # 보유의 25% 이상(3종목 이상)이 못 받으면 장애 — 전멸만 잡던 판정은 312/379 실패도 ✅ 였다(리뷰 실측)
    (_src_rec(*[_s("KR", failed=True)] * 3, *[_s("KR")] * 7, fail=3, naver=7), "fail", "원천 실패 3/10종목 — 원천 X"),
    # 한 시장이 통째로 실패(3종목 이상)면 비율이 낮아도 장애
    (_src_rec(*[_s("KR", failed=True)] * 3, *[_s("US")] * 17, fail=3, yfinance=17), "fail",
     "KR 보유 3종목 전부 원천 실패 — 원천 X"),
    # 시장 종목이 적으면(2) 통째 실패도 장애로 치지 않는다(일시 실패 둘이 매일 ❌ 가 되지 않게)
    (_src_rec(*[_s("KR", failed=True)] * 2, *[_s("US")] * 10, fail=2, yfinance=10), "ok", "원천 못 받은 종목 2"),
    # 시장 전부가 0건 — yfinance 는 깨진 응답을 빈 목록으로 숨긴다
    (_src_rec(*[_s("US", raw=0)] * 4, *[_s("KR")] * 4, naver=4, yfinance=4), "fail",
     "US 보유 4종목 전부 원천이 0건을 줌"),
    (_src_rec(*[_s("US", raw=0)] * 2, *[_s("KR")] * 4, naver=4, yfinance=2), "ok", "보유 6종목"),
])
def test_status_verdict_counts_partial_outages(hn, rec, kind, frag):
    k, line = hn.status_verdict([rec], _t("2026-10-12T09:00"), {})
    assert k == kind and frag in line, line


def test_status_verdict_outage_wins_over_pending(hn):
    rec = _src_rec(*[_s("KR", failed=True)] * 3, {**_s("KR"), "status": "pending", "reason": "AI 대기"},
                   fail=3, naver=1)
    k, line = hn.status_verdict([rec], _t("2026-10-12T09:00"), {})
    assert k == "fail" and line.endswith("Pros/Cons 대기 1종목"), line


@pytest.mark.parametrize("ago, kind", [(44, "wait"), (46, "fail")])
def test_status_verdict_stale_tick_boundary(hn, ago, kind):
    now = _t("2026-10-12T09:00")
    st = {"last_tick": (now - timedelta(minutes=ago)).isoformat()}
    assert hn.status_verdict([], now, st)[0] == kind


# ── DART 한글명 · 영문 이름 대소문자(리뷰 Medium 4) ─────────────────────────────
def test_with_ko_only_asks_for_korean_stocks_without_hangul(hn):
    asked = []
    ko = lambda c: asked.append(c) or "네이버"                          # noqa: E731
    nv = hn.with_ko({"name": "NAVER", "market": "KR", "ticker": "035420"}, ko)
    assert nv["ko"] == "네이버" and asked == ["035420"]
    assert hn.with_ko({"name": "삼성전자", "market": "KR", "ticker": "005930"}, ko) == \
        {"name": "삼성전자", "market": "KR", "ticker": "005930"}
    assert "ko" not in hn.with_ko({"name": "NVIDIA", "market": "US", "ticker": "NVDA"}, ko)
    assert asked == ["035420"]                                       # 한글 이름·해외는 묻지 않는다
    assert hn.search_name(nv) == "네이버"                              # 국내 뉴스 검색은 한글 이름으로
    assert hn.aliases(nv) == [("NAVER", "name"), ("Naver", "name"), ("네이버", "name"), ("035420", "code")]


def test_ascii_name_matches_its_case_variants(hn):
    s = {"name": "S-Oil", "market": "KR", "ticker": "010950", "ko": "에쓰오일"}
    for text in ("S-OIL, 정제마진 개선", "S-Oil 배당", "에쓰오일 실적"):
        assert hn.mentioned(text, hn.aliases(s)), text
    assert not hn.mentioned("s-oil 소문자는 안 잡는다", hn.aliases(s))
    assert hn.aliases({"name": "NVDA", "market": "US", "ticker": "NVDA"})[-1] == ("NVDA", "ticker")
    assert not hn.find_mentions("Now 급등", "NOW", "ticker")          # 티커는 대소문자 그대로만


def test_collect_uses_the_dart_korean_name(hn):
    """'NAVER' 로는 국내 뉴스가 거의 안 걸린다(2026-06-08 NAVER 0-news) — 검색·이름 거르기·AI 머리가 한글
    이름도 쓰고, 몇 종목을 확인했는지 화면이 센다."""
    seen = []

    def fetch(st):
        seen.append(hn.search_name(st))
        return ([_it("네이버, 웹툰 해외 매출 확대", "2026-10-12T06:00:00+09:00", link="https://p/1"),
                 _it("다른 회사 기사", "2026-10-12T05:00:00+09:00", link="https://p/2")], "naver", "")
    rec = hn.collect(_t(_NOW), model=_model(_h("NAVER", "035420", "KR")), records=[], fetch=fetch, pace=0,
                     us_etf=set(), ko=lambda c: "네이버")
    s = rec["stocks"][0]
    assert seen == ["네이버"] and s["ko"] == "네이버" and [it["title"] for it in s["items"]] == ["네이버, 웹툰 해외 매출 확대"]
    assert rec["sources"]["ko_try"] == 1 and rec["sources"]["ko_ok"] == 1 and rec["sources"]["dropped_name"] == 1
    assert "### key=KR:035420 · NAVER / 네이버(035420, 한국)" in hn.build_prompt([s])
    assert "영문 이름 국내 종목 한글명 확인 1/1" in hn._day_meta(rec)
    miss = hn.collect(_t(_NOW), model=_model(_h("NAVER", "035420", "KR")), records=[], fetch=fetch, pace=0,
                      us_etf=set(), ko=lambda c: "")
    assert "ko" not in miss["stocks"][0] and "한글명 확인 0/1" in hn._day_meta(miss)


def test_dart_ko_takes_only_hangul_and_survives_errors(hn, monkeypatch, caplog):
    import bot.dart_client as dc
    box = {"v": "네이버"}
    monkeypatch.setattr(dc, "get_dart", lambda: types.SimpleNamespace(news_search_name=lambda c: box["v"]))
    assert hn._dart_ko("035420") == "네이버"
    box["v"] = "NAVER"                                                 # 영문만 오면 '' — 검색어로 못 쓴다
    assert hn._dart_ko("035420") == ""
    monkeypatch.setattr(dc, "get_dart", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with caplog.at_level("WARNING", logger="bot.holdings_news"):
        assert hn._dart_ko("035420") == ""
    assert any("DART 한글명 조회 실패 035420: x" in r.getMessage() for r in caplog.records)
    # 수집이 기본값으로 실제 `_dart_ko` 를 쓴다(주입 없이)
    monkeypatch.setattr(dc, "get_dart", lambda: types.SimpleNamespace(news_search_name=lambda c: "네이버"))
    rec = hn.collect(_t(_NOW), model=_model(_h("NAVER", "035420", "KR")), records=[], fetch=_fetcher({}),
                     pace=0, us_etf=set())
    assert rec["stocks"][0]["ko"] == "네이버"


def test_us_etf_wrapper_feeds_collect_and_why(hn, portfolio, monkeypatch):
    """수집은 목록을 받고(네트워크 허용), `--why` 는 캐시만 본다(읽기 전용) — 같은 래퍼, 인자만 다르다."""
    import bot.us_symbol_names as us
    asked = []
    monkeypatch.setattr(us, "us_etf_symbols", lambda **k: asked.append(k) or {"NVDA"})
    rec = hn.collect(_t(_NOW), model=_model(_h("엔비디아", "NVDA", "US", source="alias"),
                                            _h("삼성전자", "005930", "KR")), records=[], fetch=_fetcher({}), pace=0)
    assert [s["key"] for s in rec["stocks"]] == ["KR:005930"] and rec["universe"]["etf"] == ["엔비디아"]
    out = []
    hn.why(_t("2026-10-12T09:00"), out=out.append)
    assert asked == [{"cache_only": False}, {"cache_only": True}]
    assert any("미국 ETF 목록 1종(캐시)" in ln for ln in out)
    monkeypatch.setattr(us, "us_etf_symbols", lambda **k: (_ for _ in ()).throw(OSError("disk")))
    assert hn._us_etf() == set()


# ── 기록 읽기 상한(리뷰 Medium 5) ─────────────────────────────────────────────
def test_load_records_reads_only_the_newest_files(hn):
    for d in ("2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"):
        hn.save_record({"date": d, "stocks": []})
    (hn._DAYS / "2026-10-07.json").write_text(json.dumps({"date": "2026-10-12", "stocks": []}), encoding="utf-8")
    (hn._DAYS / "x.json").write_text(json.dumps({"date": "2026-10-13", "stocks": []}), encoding="utf-8")
    # 이름이 날짜가 아닌 파일은 셈에도 '최근' 자리에도 안 낀다(이름순으로 맨 뒤에 서서 최신 하루를 밀어낸다)
    assert [r["date"] for r in hn.load_records(limit=2)] == ["2026-10-11", "2026-10-12"]
    assert [r["date"] for r in hn.load_records()][0] == "2026-10-08"    # 이름과 안의 날짜가 다르면 건너뜀
    assert hn.count_records() == 6 and hn.load_records(limit=0) == []


def test_tick_and_regenerate_read_a_bounded_number_of_days(hn, portfolio, monkeypatch, tmp_path):
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "arch")
    seen = []
    real = hn.load_records
    monkeypatch.setattr(hn, "load_records", lambda limit=None: seen.append(limit) or real(limit))
    hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=_news_for_all, body=lambda l: "", pace=0)
    hn.regenerate(_t(_NOW))
    assert seen == [3, hn.RENDER_DAYS]


def test_render_total_counts_kept_days_not_loaded_ones(hn):
    html = hn.render_page([_page_rec()], {}, _t("2026-10-12T09:00"), gen="GEN", total=45)
    assert f"총 45일 기록 중 최근 {hn.RENDER_DAYS}일 표시(그 앞은 보관만)" in html


# ── 창 겹침(리뷰 Medium 6) ────────────────────────────────────────────────────
def test_next_window_overlaps_to_catch_late_arrivals(hn):
    """07:14 글이 07:15 집계 **뒤에** 수신함에 닿으면 어느 날에도 안 실렸다(리뷰 실측) — 다음 창은 직전 창
    끝보다 6시간 앞에서 열고, 이미 실린 항목은 지난 기록과 대조해 뺀다."""
    model = _model(_h("삼성전자", "005930", "KR"))
    day1 = hn.collect(_t("2026-10-12T07:15"), model=model, records=[], fetch=_fetcher({}), pace=0, us_etf=set())
    _tg(hn, (1, "2026-10-12T07:00:00+09:00", "삼성전자 일찍 온 글", False))
    day1 = hn.collect(_t("2026-10-12T07:15"), model=model, records=[], fetch=_fetcher({}), pace=0, us_etf=set())
    assert [it["id"] for it in day1["stocks"][0]["items"]] == [1]
    _tg(hn, (2, "2026-10-12T07:14:00+09:00", "삼성전자 늦게 닿은 글", False))   # 집계 뒤에 수신함에 들어옴
    day2 = hn.collect(_t("2026-10-13T07:15"), model=model, records=[_public_copy(hn, day1)], fetch=_fetcher({}),
                      pace=0, us_etf=set())
    assert day2["window"]["since"] == "2026-10-12T01:15:00+09:00"
    assert [it["id"] for it in day2["stocks"][0]["items"]] == [2]        # 늦은 글은 실리고 어제 글은 안 겹친다


def _public_copy(hn, rec):
    return json.loads(json.dumps(hn._public(rec), ensure_ascii=False))


def test_in_window_end_is_exclusive(hn):
    since, until = _t("2026-10-11T07:15"), _t("2026-10-12T07:15")
    assert hn._in_window({"ts": "2026-10-12T07:15:00+09:00"}, since, until) is False   # 끝 시각은 다음 창 몫
    assert hn._in_window({"ts": "2026-10-11T07:15:00+09:00"}, since, until) is True


# ── AI 일시 오류(리뷰 Medium 7) ───────────────────────────────────────────────
def test_llm_fail_kind(hn):
    from google.genai import errors as ge
    body = {"error": {"code": 0, "message": "m", "status": "S"}}
    assert hn._llm_fail_kind(ge.ServerError(503, body)) == "transient"
    assert hn._llm_fail_kind(ge.ClientError(429, body)) == "transient"
    assert hn._llm_fail_kind(ge.ClientError(400, body)) == "rejected"
    assert hn._llm_fail_kind(ge.ClientError(403, body)) == "rejected"
    assert hn._llm_fail_kind(TimeoutError("t")) == "transient"
    assert hn._llm_fail_kind(type("ReadTimeout", (Exception,), {})("t")) == "transient"
    assert hn._llm_fail_kind(type("ConnectError", (Exception,), {})("c")) == "transient"
    assert hn._llm_fail_kind(ValueError("bug")) == "rejected"


class _FakeModels:
    def __init__(self, errs):
        self.errs, self.calls = list(errs), []

    def generate_content(self, model, contents, config=None):
        self.calls.append(config is not None)
        if self.errs:
            raise self.errs.pop(0)
        return types.SimpleNamespace(text='{"stocks": []}', usage_metadata=types.SimpleNamespace(
            prompt_token_count=10, candidates_token_count=2))


def _fake_genai(monkeypatch, errs, key="k" * 20):
    import bot.genai_factory as gf
    models = _FakeModels(errs)
    monkeypatch.setattr(gf, "effective_key", lambda: key)
    monkeypatch.setattr(gf, "make_client", lambda *a, **k: types.SimpleNamespace(models=models))
    return models


def test_call_llm_retries_without_config_only_on_400(hn, monkeypatch):
    from google.genai import errors as ge
    body = {"error": {"code": 400, "message": "bad config", "status": "INVALID_ARGUMENT"}}
    m = _fake_genai(monkeypatch, [ge.ClientError(400, body)])
    text, pt, ot, why, kind = hn.call_llm("p")
    assert (text, pt, ot, why, kind) == ('{"stocks": []}', 10, 2, "", "") and m.calls == [True, False]
    m = _fake_genai(monkeypatch, [ge.ServerError(503, {"error": {"code": 503, "message": "UNAVAILABLE"}})])
    text, _, _, why, kind = hn.call_llm("p")
    assert text is None and kind == "transient" and why.startswith("AI 일시 오류(ServerError: 503")
    assert m.calls == [True]                                          # 503 을 같은 주기에 두 번 두드리지 않는다
    m = _fake_genai(monkeypatch, [ge.ClientError(403, {"error": {"code": 403, "message": "denied"}})])
    assert hn.call_llm("p")[3:] == ("AI 호출 실패(ClientError: 403 None. {'error': {'code': 403, 'message': "
                                    "'denied'}})", "rejected") and m.calls == [True]
    assert _fake_genai(monkeypatch, [], key="") and hn.call_llm("p") == (None, 0, 0, "no_key", "no_key")


def test_call_llm_masks_the_key_in_the_reason(hn, monkeypatch):
    """사유는 화면에 실린다 — 원천 오류 문구에 키가 되읊어져도 가린다(§Secrets)."""
    key = "AIza" + "x" * 30
    _fake_genai(monkeypatch, [RuntimeError(f"bad key {key} rejected")], key=key)
    why = hn.call_llm("p")[3]
    assert key not in why and "***" in why


def test_transient_ai_errors_do_not_fail_the_day(hn):
    """30분 장애(일시 오류 세 주기)가 그날 분석을 통째로 '실패' 로 굳혔다(리뷰 실측) — 일시 오류는 시도로
    세지 않고 그 주기를 바로 멈춘다(남은 묶음을 같은 장애에 다시 던지지 않는다)."""
    rec = _rec(*[_stock(f"KR:{i:06d}", 1) for i in range(hn.BATCH_MAX + 2)])          # 묶음 둘
    down = _llm(None, pt=0, ot=0, why="AI 일시 오류(ServerError: 503 UNAVAILABLE)", kind="transient")
    for _ in range(hn.MAX_ANALYZE_TRIES + 1):
        done = hn.analyze(rec, llm=down)
        assert done["transient"] and done["calls"] == 1
    assert len(down.calls) == hn.MAX_ANALYZE_TRIES + 1
    ss = rec["stocks"]
    assert all(s["status"] == "pending" and s["tries"] == 0 for s in ss)
    # 저장본엔 사유만 — '다음 주기에 다시' 는 그날이 아직 이어서 정리될 때 화면이 붙인다(델타 리뷰 M1).
    assert ss[0]["reason"] == "AI 일시 오류(ServerError: 503 UNAVAILABLE)" and ss[0]["retry"] is True
    done = hn.analyze(rec, llm=_llm(_answer_all))                     # 회복하면 바로 정리된다
    assert done["analyzed"] == len(ss) and all(s["status"] == "ok" for s in ss)


def test_tick_stops_on_a_transient_error_before_yesterday(hn, portfolio):
    """일시 오류면 그 주기는 거기서 멈춘다 — 같은 장애에 어제 대기분까지 던지지 않는다."""
    hn.save_record(_pending_day("2026-10-11"))
    down = _llm(None, pt=0, ot=0, why="AI 일시 오류(ServerError: 503)", kind="transient")
    res = hn.tick(_t(_NOW), llm=down, fetch=_news_for_all, body=lambda l: "", pace=0)
    assert res["transient"] and len(down.calls) == 1
    y = [r for r in hn.load_records() if r["date"] == "2026-10-11"][0]["stocks"][0]
    assert y["reason"] == "" and y["status"] == "pending"


def test_rejected_ai_calls_count_tries_and_keep_going(hn):
    rec = _rec(*[_stock(f"KR:{i:06d}", 1) for i in range(hn.BATCH_MAX + 2)])
    bad = _llm(None, pt=0, ot=0, why="AI 호출 실패(ClientError: 400 too long)", kind="rejected")
    done = hn.analyze(rec, llm=bad)
    assert done["calls"] == 2 and not done["transient"]                # 거부는 그 묶음만 — 다음 묶음은 간다
    assert all(s["tries"] == 1 for s in rec["stocks"])


# ── 지난 날 대기(리뷰 Low 8) ──────────────────────────────────────────────────
def _pending_day(date, key="KR:005930"):
    s = _stock(key, 1)
    return {"date": date, "window": {"until": f"{date}T07:15:00+09:00"}, "stocks": [s],
            "llm": {"calls": 0, "pt": 0, "ot": 0, "cost_usd": 0.0}}


def test_tick_finishes_the_latest_previous_days_pending(hn, portfolio):
    """자정 무렵 늦은 집계·장애로 남은 어제의 '대기' 는 오늘만 보는 주기로는 영영 안 끝났다(리뷰) — 오늘 먼저,
    남은 상한으로 직전 기록 **하나**의 대기분도 정리한다(그보다 앞선 날은 안 돈다)."""
    hn.save_record(_pending_day("2026-10-10", "KR:000001"))
    hn.save_record(_pending_day("2026-10-11"))
    res = hn.tick(_t("2026-10-12T06:00"), llm=_llm(_answer_all), fetch=_news_for_all, pace=0)
    assert res["status"] == "wait" and res["analyzed"] == 1
    by = {r["date"]: r["stocks"][0]["status"] for r in hn.load_records()}
    assert by == {"2026-10-10": "pending", "2026-10-11": "ok"}


def test_tick_resumes_yesterday_after_today(hn, portfolio):
    hn.save_record(_pending_day("2026-10-11"))
    seen = []

    def llm(prompt):
        seen.append([ln.split("key=")[1].split(" ")[0] for ln in prompt.splitlines() if ln.startswith("### key=")])
        return _answer_all(prompt), 10, 2, "", ""
    hn.tick(_t(_NOW), llm=llm, fetch=_news_for_all, body=lambda l: "", pace=0)
    assert seen == [["KR:005930", "US:NVDA"], ["KR:005930"]]           # 오늘 둘 → 어제 하나


def test_older_pending_days_say_they_did_not_finish(hn):
    now = _t("2026-10-12T09:00")
    recs = [_pending_day("2026-10-10"), _pending_day("2026-10-11"), {**_pending_day("2026-10-12")}]
    fr = hn.day_fragments(recs, now)
    assert "Pros/Cons 대기 — 다음 주기에 정리" in fr["hn_d_2026-10-11.html"]      # 직전 날은 아직 이어서 정리
    assert "Pros/Cons 정리를 마치지 못함 — 사유 미기록 · 뉴스만 표시" in fr["hn_d_2026-10-10.html"]
    page = hn.render_page(recs, {}, now, gen="GEN")
    assert "Pros/Cons 대기 — 다음 주기에 정리" in page.split("<details class='hn-day'")[1]


# ── 본문이 AI 에 다 닿는다(리뷰 Low 9) ─────────────────────────────────────────
def test_prompt_carries_the_whole_body(hn):
    s = _stock("KR:A", 1)
    s["items"][0]["summary"] = "요" * 500
    s["items"][0]["_body"] = "가" * (hn.BODY_CHARS - 10) + "끝까지닿는본문"
    p = hn.build_prompt([s])
    assert "끝까지닿는본문" in p and "요" * 300 in p            # 줄 상한(요약)과 본문 상한이 따로다


# ── yfinance 0건(리뷰 Low 10) ─────────────────────────────────────────────────
def test_yfinance_empty_also_asks_google(hn, monkeypatch):
    us = {"key": "US:NVDA", "market": "US", "ticker": "NVDA", "name": "엔비디아"}
    monkeypatch.setattr(hn, "_fetch_yf", lambda t: ([], ""))
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: ([{"title": "g"}], ""))
    assert hn.fetch_stock_news(us) == ([{"title": "g"}], "google", "yfinance 0건")
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: (None, "구글 실패"))
    assert hn.fetch_stock_news(us) == ([], "yfinance", "")               # 둘 다 0건 — 실패로 세지 않는다
    monkeypatch.setattr(hn, "_fetch_yf", lambda t: ([{"title": "y"}], ""))
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: pytest.fail("yfinance 가 줬으면 구글은 안 묻는다"))
    assert hn.fetch_stock_news(us) == ([{"title": "y"}], "yfinance", "")


def test_collect_records_raw_counts(hn):
    rec = hn.collect(_t(_NOW), model=_model(_h("엔비디아", "NVDA", "US", source="alias")), records=[],
                     fetch=_fetcher({"US:NVDA": ([_it("old", "2026-10-01T06:00:00+09:00", link="https://y/0")],
                                                  "yfinance", "")}), pace=0, us_etf=set())
    s = rec["stocks"][0]
    assert s["raw_n"] == 1 and s["items"] == []                         # 원천은 줬는데 창 밖 — 0건과 다르다


# ── 미국 ETF(리뷰 Low 11) ─────────────────────────────────────────────────────
_NASDAQ_LISTED = ("Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
                  "AIRR|First Trust RBA American Industrial Renaissance ETF|G|N|N|100|Y|N\n"
                  "NVDA|NVIDIA Corporation - Common Stock|Q|N|N|100|N|N\n"
                  "File Creation Time: 1012202622:01|||||||")
_OTHER_LISTED = ("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
                 "DRAM|Roundhill Memory ETF|P|DRAM|Y|100|N|DRAM\n"
                 "IBM|International Business Machines Corporation Common Stock|N|IBM|N|100|N|IBM\n"
                 "File Creation Time: 1012202622:01|||||||")


def test_parse_etf_symbols_reads_both_files(hn):
    from bot.us_symbol_names import parse_etf_symbols
    assert parse_etf_symbols(_NASDAQ_LISTED) == {"AIRR"} and parse_etf_symbols(_OTHER_LISTED) == {"DRAM"}
    assert parse_etf_symbols("Symbol|Security Name\nX|y") == set() and parse_etf_symbols("") == set()


def test_us_etf_symbols_caches_only_a_complete_list(tmp_path, monkeypatch):
    import httpx

    import bot.us_symbol_names as us
    monkeypatch.setattr(us, "_ETF_CACHE_FILE", tmp_path / "etf.json")
    many = "\n".join(f"E{i:03d}|ETF {i}|G|N|N|100|Y|N" for i in range(120))
    pages = {us._URLS[0]: _NASDAQ_LISTED.replace("NVDA|", many + "\nNVDA|"), us._URLS[1]: _OTHER_LISTED}
    hits = []

    def get(url, **kw):
        hits.append(url)
        if url not in pages:
            raise httpx.ConnectError("down")
        return types.SimpleNamespace(status_code=200, text=pages[url])
    monkeypatch.setattr(httpx, "get", get)
    full = us.us_etf_symbols()
    assert {"AIRR", "DRAM", "E000"} <= full and (tmp_path / "etf.json").exists()
    assert us.us_etf_symbols() == full and len(hits) == 2               # 캐시 — 다시 안 받는다
    (tmp_path / "etf.json").unlink()
    del pages[us._URLS[1]]
    part = us.us_etf_symbols()
    assert part == set() and not (tmp_path / "etf.json").exists()      # 한쪽만이면 굽지도 넘기지도 않는다(#280)
    assert us.us_etf_symbols(cache_only=True) == set()                  # 진단 — 캐시 없으면 묻지 않고 빈 set
    assert len(hits) == 4


def test_universe_counts_us_etfs_as_etf(hn):
    stocks, skipped = hn.universe(_model(_h("first trust rba", "AIRR", "US", source="alias"),
                                         _h("엔비디아", "NVDA", "US", source="alias")), us_etf={"AIRR"})
    assert [s["key"] for s in stocks] == ["US:NVDA"] and skipped["etf"] == ["first trust rba"]
    assert skipped["us_etf_n"] == 1


def test_day_meta_says_when_the_us_etf_list_was_missing(hn):
    rec = hn.collect(_t(_NOW), model=_model(_h("엔비디아", "NVDA", "US", source="alias")), records=[],
                     fetch=_fetcher({}), pace=0, us_etf=set())
    assert "미국 ETF 목록을 못 받아 ETF 가 회사로 섞였을 수 있음" in hn._day_meta(rec)
    rec2 = hn.collect(_t(_NOW), model=_model(_h("엔비디아", "NVDA", "US", source="alias")), records=[],
                      fetch=_fetcher({}), pace=0, us_etf={"AIRR"})
    assert "ETF 목록" not in hn._day_meta(rec2)


# ── 누적 비용 = 파일 + 로테이션 몫(리뷰 Low 12) ─────────────────────────────────
def test_rotation_keeps_a_per_subsystem_rollup(tmp_path, monkeypatch):
    import bot.usage_tracker as ut
    logf = tmp_path / "usage.jsonl"
    monkeypatch.setattr(ut, "USAGE_LOG", logf)
    monkeypatch.setattr(ut, "ROLLUP_PATH", tmp_path / "rollup.json")
    old = time.time() - 40 * 86400
    rows = [{"ts": old, "type": "llm_call", "cost_usd": 2.0, "subsystem": "holdings_news"},
            {"ts": old, "type": "llm_call", "cost_usd": 0.5},
            {"ts": time.time(), "type": "llm_call", "cost_usd": 1.0, "subsystem": "holdings_news"}]
    logf.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (tmp_path / "rollup.json").write_text(json.dumps({"cost_usd": 4.0, "by_subsystem": {"holdings_news": 1.0,
                                                                                        "screener": 3.0}}))
    ut.load_records()                                                   # 이전 적산분에 더한다(덮지 않는다)
    assert ut.rollup_cost_usd() == 6.5
    assert ut.rollup_cost_by_subsystem() == {"holdings_news": 3.0, "screener": 3.0}
    ut.load_records()                                                   # 다시 돌아도 이중 적산 없음
    assert ut.rollup_cost_by_subsystem() == {"holdings_news": 3.0, "screener": 3.0}


def test_cost_line_total_includes_rotated_cost(hn, tmp_path):
    now = _t("2026-10-12T09:00")
    (tmp_path / "usage_rollup.json").write_text(json.dumps(
        {"cost_usd": 9.0, "by_subsystem": {"holdings_news": 0.01, "screener": 8.99}}), encoding="utf-8")
    (tmp_path / "usage.jsonl").write_text(json.dumps({"type": "llm_call", "subsystem": "holdings_news",
                                                      "cost_usd": 0.001, "ts": now.timestamp()}) + "\n",
                                          encoding="utf-8")
    from bot.usage_tracker import KRW_PER_USD
    t, m, x = (round(v * KRW_PER_USD) for v in (0.001, 0.001, 0.011))
    assert hn._cost_line(now) == f"₩{t:,} / ₩{m:,} / ₩{x:,}"
    (tmp_path / "usage.jsonl").unlink()
    assert hn._cost_line(now) == f"₩0 / ₩0 / ₩{round(0.01 * KRW_PER_USD):,}"


# ── 조사 꼬리 · 앞 '&' (리뷰 Low 13 · 생존 M13·M14) ───────────────────────────
@pytest.mark.parametrize("text, alias, hit", [
    ("삼성전자株 급등", "삼성전자", True), ("도요타社 리콜", "도요타", True),
    ("SK&LG 합작", "LG", False), ("LG&SK 합작", "LG", False),
])
def test_cjk_suffix_and_left_ampersand(hn, text, alias, hit):
    assert bool(hn.find_mentions(text, alias, "name")) is hit


# ── 버린 줄(리뷰 Low 14) ──────────────────────────────────────────────────────
def test_dropped_lines_are_counted_and_shown(hn):
    s = _stock("KR:A", 2)
    hn.analyze(_rec(s), llm=_llm(json.dumps({"stocks": [{"key": "KR:A", "relevant": [1, 2],
        "pros": [{"t": "근거 없음", "refs": []}, "문자열"], "cons": [{"t": "없는 번호", "refs": [7]}]}]},
        ensure_ascii=False)))
    assert s["dropped_lines"] == 3 and s["pros"] == [] and s["cons"] == []
    card = hn._card({**s, "sym": "A"})
    assert "AI 가 쓴 줄 중 3줄은 근거 번호가 없거나 맞지 않아 버림" in card and "가를 내용이 없음" not in card
    s2 = _stock("KR:B", 1)
    hn.analyze(_rec(s2), llm=_llm(json.dumps({"stocks": [{"key": "KR:B", "pros": [
        {"t": "좋다", "refs": [1]}, {"t": "버림", "refs": []}]}]}, ensure_ascii=False)))
    assert "근거 번호가 없거나 맞지 않아 버린 줄 1개" in hn._card({**s2, "sym": "B"})


# ── last_tick 은 시작할 때(리뷰 Low 15) ───────────────────────────────────────
def test_last_tick_is_saved_before_the_long_collect(hn, portfolio):
    during = {}

    def fetch(st):
        during.setdefault("st", hn.load_state())
        return _news_for_all(st)
    hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=fetch, body=lambda l: "", pace=0)
    assert during["st"]["last_tick"] == "2026-10-12T07:15:00+09:00"
    assert hn.status_verdict([], _t("2026-10-12T07:40"), during["st"])[0] == "wait"   # 수집 중엔 '멈춤' 아님


def test_successful_tick_clears_a_stale_no_holdings_flag(hn, portfolio):
    """보유 종목이 없던 날의 표시가 남으면 다음 날 00:00~07:10 내내 '보유 종목 없음' 이다(생존 M10)."""
    hn._save_state({"no_holdings": True, "reason": "자산 대시보드에 보유 종목이 없음(portfolio.json)"})
    hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=_news_for_all, body=lambda l: "", pace=0)
    st = hn.load_state()
    assert st["no_holdings"] is False and st["reason"] == ""
    assert hn.status_verdict([], _t("2026-10-13T06:00"), st)[0] == "wait"


# ── 국내 구글 결과도 이름으로 거른다(생존 M11) ─────────────────────────────────
def test_kr_google_results_are_name_filtered_too(hn):
    rec = hn.collect(_t(_NOW), model=_model(_h("카카오", "035720", "KR")), records=[], pace=0, us_etf=set(),
                     fetch=_fetcher({"KR:035720": ([_it("카카오 실적", "2026-10-12T05:00:00+09:00", link="https://g/1"),
                                                    _it("다른 회사", "2026-10-12T04:00:00+09:00", link="https://g/2")],
                                                   "google", "네이버 키 없음")}))
    assert [it["title"] for it in rec["stocks"][0]["items"]] == ["카카오 실적"]
    assert rec["sources"]["dropped_name"] == 1


# ── escape(생존 M2~M7·S2) ────────────────────────────────────────────────────
def test_every_outside_string_is_escaped(hn):
    """원천·AI·텔레그램이 준 글은 전부 escape — 칸마다 다른 표식을 둬야 하나만 빠져도 잡힌다(#91c)."""
    rec = _page_rec()
    ok = rec["stocks"][0]
    ok["name"] = "삼성'<q1>"
    ok["pros"] = [{"t": "호재<x2>", "refs": [1]}]
    ok["items"][0]["link"] = "https://p/?a='b'&c=<x8>"
    ok["items"][1]["summary"] = "텔레<x7>그램 글 전체"
    rec["stocks"][2]["reason"] = "실패<x6>"
    rec["stocks"][4]["name"] = "조용<x4>"
    rec["sources"]["fail"] = 1
    rec["sources"]["fail_reasons"] = {"원천<x3>": 1}
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN")
    for raw in ("<q1>", "<x2>", "<x3>", "<x4>", "<x6>", "<x7>", "<x8>", "'b'"):
        assert raw not in html, raw
    for esc in ("&lt;q1&gt;", "호재&lt;x2&gt;", "원천&lt;x3&gt;", "조용&lt;x4&gt;", "실패&lt;x6&gt;",
                "텔레&lt;x7&gt;", "a=&#x27;b&#x27;&amp;c=&lt;x8&gt;", "data-q='삼성&#x27;&lt;q1&gt; 005930'"):
        assert esc in html, esc


# ── portfolio_watch 첫 채움 실패(생존 P1·P2) ─────────────────────────────────
class _SeedFails(_Client):
    def iter_messages(self, chan, **kw):
        if "min_id" in kw:
            return super().iter_messages(chan, **kw)          # 부모가 호출을 센다
        self.calls.append(kw)

        async def bad():
            raise RuntimeError("FloodWait 같은 실패")
            yield                                                   # noqa: unreachable — async 생성기로 만든다
        return bad()


def test_portfolio_watch_seed_failure_neither_aborts_nor_retries(hn, tmp_path, monkeypatch):
    """첫 채움이 실패해도 (P2) 그 주기는 끝까지 간다 — 예외가 새면 `_save_seen` 전에 빠져 자산 zip 을 다음
    폴링에 또 적재하고 '✅ 자산 업데이트' 를 다시 보낸다 · (P1) 다시 시도하지 않는다 — 실패를 매 폴링 두드리면
    FloodWait 를 키운다(#258)."""
    import bot.portfolio_watch as pw
    monkeypatch.setattr(pw, "_SEEN_PATH", tmp_path / "seen.json")
    for k, v in (("TG_USER_API_ID", "123"), ("TG_USER_API_HASH", "h"), ("TG_RAG_CHANNEL", "@rag"),
                 ("TG_USER_SESSION", str(tmp_path / "sess"))):
        monkeypatch.setenv(k, v)
    pw._save_seen({"initialized": True, "ids": [100], "last_msg_id": 100})
    now = datetime.now(timezone.utc)
    c = _SeedFails([_Msg(101, "삼성전자 새 글", now)], [])
    _fake_telethon(monkeypatch, c)
    assert asyncio.run(pw._run_async()) == 0
    st = pw._load_seen()
    assert st["last_msg_id"] == 101 and st["tg_seeded"] is True
    assert [r["id"] for r in hn._tg_read()[0]] == [101]
    c2 = _SeedFails([_Msg(102, "카카오 글", now)], [])
    _fake_telethon(monkeypatch, c2)
    assert asyncio.run(pw._run_async()) == 0
    assert c2.calls == [{"min_id": 101, "limit": pw._NEW_MSG_CAP}]    # 첫 채움을 다시 시도하지 않는다


def test_probe_searches_with_the_dart_korean_name(hn, monkeypatch):
    """프로브는 수집과 같은 검색어를 쓴다(#145) — 영문 이름 국내 종목은 DART 한글명으로."""
    import bot.dart_client as dc
    import bot.portfolio as pf
    monkeypatch.setattr(pf, "load", lambda: _model(_h("NAVER", "035420", "KR"), _h("HYBE", "352820", "KR")))
    monkeypatch.setattr(dc, "get_dart", lambda: types.SimpleNamespace(
        news_search_name=lambda c: "네이버" if c == "035420" else None))
    asked = []
    monkeypatch.setattr(hn, "fetch_stock_news", lambda s: asked.append(hn.search_name(s)) or ([], "naver", ""))
    out = []
    assert hn.probe(None, out=out.append) == 0
    assert asked == ["네이버", "HYBE"]
    assert any("검색어 '네이버'(DART 한글명 '네이버')" in ln for ln in out)
    assert any("검색어 'HYBE' · ⚠️ 영문 이름인데 DART 한글명을 못 얻음" in ln for ln in out)


def test_us_etf_symbols_falls_back_to_a_stale_list(tmp_path, monkeypatch):
    """목록을 다 못 받은 날은 7일 넘은 캐시라도 쓴다 — 빈 목록이면 그날 미국 ETF 가 전부 회사로 섞인다."""
    import os

    import httpx

    import bot.us_symbol_names as us
    cache = tmp_path / "etf.json"
    monkeypatch.setattr(us, "_ETF_CACHE_FILE", cache)
    cache.write_text(json.dumps(["AIRR", "DRAM"]), encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(cache, (old, old))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert us.us_etf_symbols() == {"AIRR", "DRAM"}
    assert cache.stat().st_mtime == old                                # 낡은 캐시를 새것처럼 다시 굽지 않는다
    cache.unlink()
    assert us.us_etf_symbols() == set()


# ── 반영분 독립 리뷰(2026-10-13) ──────────────────────────────────────────────
def _body_fetch(stock):
    if stock["market"] == "KR":
        return ([_it("삼성전자 HBM 소식", "2026-10-12T06:00:00+09:00", link="https://press/a",
                     naver_link="https://n.news.naver.com/mnews/article/001/1")], "naver", "")
    return ([_it("엔비디아 소식", "2026-10-12T06:00:00+09:00", link="https://y/1")], "yfinance", "")


def test_body_survives_a_transient_error_via_the_sidecar(hn, portfolio, monkeypatch, tmp_path):
    """H1 — 본문은 수집 주기의 메모리에만 있었다. 07:10 일시 오류 한 번이면 다음 주기는 기록을 다시 읽어 본문
    없이 정리했는데 카드는 '본문을 AI 가 읽음' 이었다(리뷰 실측). 사이드카가 본문을 나르고, 화면은 실제로
    보낸 것만 '읽음' 으로 적고, 다 정리된 날은 사이드카를 지운다."""
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "arch")
    down = _llm(None, pt=0, ot=0, why="AI 일시 오류(ServerError: 503)", kind="transient")
    hn.tick(_t(_NOW), llm=down, fetch=_body_fetch, body=lambda l: "본문XYZ 끝", pace=0)
    side = hn._bodies_path("2026-10-12")
    assert json.loads(side.read_text(encoding="utf-8")) == {"KR:005930": {"https://press/a": "본문XYZ 끝"}}
    saved = hn.load_records()[0]
    it = saved["stocks"][0]["items"][0]
    assert it["body_chars"] == len("본문XYZ 끝") and "_body" not in it and "body_used" not in it
    assert "본문 앞부분을 AI 가 읽음" not in hn.render_page([saved], {}, _t("2026-10-12T09:00"), gen="GEN")
    ok = _llm(_answer_all)
    hn.tick(_t(_NOW), llm=ok, fetch=pytest.fail, body=pytest.fail, pace=0)       # 다시 모으지 않는다
    assert "/ 본문: 본문XYZ 끝" in ok.calls[0]                         # 다시 읽은 기록에 본문이 붙어 AI 에 간다
    rec = hn.load_records()[0]
    s = {x["key"]: x for x in rec["stocks"]}
    assert s["KR:005930"]["items"][0]["body_used"] is True and "body_used" not in s["US:NVDA"]["items"][0]
    assert "본문 앞부분을 AI 가 읽음" in hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN")
    assert not side.exists()                                            # 대기 종목이 없으면 지운다


def test_missing_or_broken_sidecar_sends_no_body_and_claims_none(hn, caplog):
    def pend():
        s = _stock("KR:A", 1)
        s["items"][0].update(k="https://p/KR:A/1", body_chars=40)
        return _rec(s)
    for broken in (False, True):
        if broken:
            hn._DAYS.mkdir(parents=True, exist_ok=True)
            hn._bodies_path("2026-10-12").write_text("{깨짐", encoding="utf-8")
        rec = pend()
        llm = _llm(_answer_all)
        with caplog.at_level("WARNING", logger="bot.holdings_news"):
            hn.analyze(rec, llm=llm)
        assert "/ 본문:" not in llm.calls[0] and "body_used" not in rec["stocks"][0]["items"][0]
    assert any("본문 사이드카 bodies_2026-10-12.json 읽기 실패" in r.getMessage() for r in caplog.records)
    li = hn._item_li({"n": 1, "src": "naver", "title": "t", "summary": "", "link": "", "source": "x",
                      "ts": "2026-10-12T06:00+09:00", "body_chars": 300}, False)
    assert "AI 가 읽음" not in li                                       # 길이만으로는 '읽음' 이 아니다


def test_sidecars_live_only_for_days_still_being_finished(hn):
    def day(date):
        s = _stock("KR:A", 1)
        s["items"][0].update(k="https://p/1", _body="본문")
        return {"date": date, "stocks": [s]}
    for dt in ("2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"):
        hn.save_record(day(dt))
    assert sorted(p.name for p in hn._DAYS.glob("bodies_*.json")) == \
        ["bodies_2026-10-11.json", "bodies_2026-10-12.json"]
    assert hn.count_records() == 4                                      # 사이드카는 하루 기록으로 안 센다


def test_retry_promise_is_rendered_only_where_it_can_be_kept(hn):
    """M1 — '다음 주기에 다시' 를 저장해 두면 그날이 정리 대상에서 빠진 뒤에도 약속이 남는다. 사유만 저장하고
    약속은 화면이 아직 이어서 정리하는 날에만 붙인다."""
    s = {**_stock("KR:A", 1), "reason": "AI 일시 오류(x)", "retry": True}
    assert "Pros/Cons 대기 — AI 일시 오류(x) — 다음 주기에 다시" in hn._card(s)
    final = hn._card(s, final=True)
    assert "정리를 마치지 못함 — AI 일시 오류(x) · 뉴스만 표시" in final and "다음 주기에 다시" not in final
    rej = hn._card({**s, "reason": "AI 호출 실패(400)", "retry": False})
    assert "Pros/Cons 대기 — AI 호출 실패(400)</div>" in rej
    now = _t("2026-10-12T09:00")
    assert hn.status_verdict([_rec(s)], now, {})[1].endswith("대기 1종목 — AI 일시 오류(x) — 다음 주기에 다시")
    assert hn.status_verdict([_rec({**s, "retry": False})], now, {})[1].endswith("대기 1종목 — AI 일시 오류(x)")
    assert hn.status_verdict([_rec({**s, "reason": ""})], now, {})[1].endswith("— 다음 주기에 이어서")


def test_kr_stock_with_a_dart_name_also_searches_its_own_name(hn, monkeypatch):
    """M2 — DART 한글명이 음역('케이티')인 회사는 기사가 영문('KT')으로 쓴다. 같은 원천으로 두 이름 다 묻고,
    링크로 중복을 빼고, 최근 순으로."""
    a = _it("케이티 a", "2026-10-12T05:00:00+09:00", link="https://n/a")
    b = _it("KT b", "2026-10-12T03:00:00+09:00", link="https://n/b")
    c = _it("KT c", "2026-10-12T06:00:00+09:00", link="https://n/c")
    asked = []
    naver = {"케이티": ([a, b], ""), "KT": ([b, c], "")}
    monkeypatch.setattr(hn, "_fetch_naver", lambda q: asked.append(("naver", q)) or naver[q])
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: asked.append(("google", q, mk)) or ([], ""))
    kt = {"key": "KR:030200", "market": "KR", "ticker": "030200", "name": "KT", "ko": "케이티"}
    items, used, why = hn.fetch_stock_news(kt)
    assert [x["link"] for x in items] == ["https://n/c", "https://n/a", "https://n/b"] and used == "naver"
    assert asked == [("naver", "케이티"), ("naver", "KT")]
    asked.clear()
    naver["케이티"] = (None, "네이버 키 없음")                          # 1순위가 구글이면 두 번째도 구글
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: asked.append(("google", q, mk)) or (
        [a] if q == "케이티" else [c], ""))
    items, used, why = hn.fetch_stock_news(kt)
    assert asked == [("naver", "케이티"), ("google", "케이티", "KR"), ("google", "KT", "KR")]
    assert [x["link"] for x in items] == ["https://n/c", "https://n/a"] and (used, why) == ("google", "네이버 키 없음")
    asked.clear()
    naver.update({"삼성전자": ([a], ""), "케이티": ([a], "")})
    hn.fetch_stock_news({"key": "KR:005930", "market": "KR", "ticker": "005930", "name": "삼성전자"})
    hn.fetch_stock_news({"key": "KR:030200", "market": "KR", "ticker": "030200", "name": "케이티", "ko": "케이티"})
    assert asked == [("naver", "삼성전자"), ("naver", "케이티")]           # 이름이 하나면 한 번만


def test_exc_msg_masks_before_it_cuts(hn, monkeypatch):
    """L1 — 자른 뒤 가리면 경계에 걸린 키 조각(앞 몇 글자)이 그대로 남는다."""
    import bot.env_keys as ek
    monkeypatch.setattr(ek, "_SECRETS", ())
    key = "AIzaS" + "Q" * 34
    ek.remember_secret(key)
    out = hn._exc_msg(RuntimeError("x" * 66 + key + " tail"), 70)
    assert out == "x" * 66 + "*** " and "AIza" not in out


def test_short_ascii_names_get_no_title_case_alias(hn):
    """L2 — 'HMM' → 'Hmm' 은 감탄사라 텔레그램 잡담이 그 회사로 잡힌다. 네 글자 이상만 첫 글자 대문자 꼴."""
    als = hn.aliases({"name": "HMM", "market": "KR", "ticker": "011200"})
    assert ("Hmm", "name") not in als and ("HMM", "name") in als
    assert not hn.mentioned("Hmm, 오늘은 쉬어 갑니다", als)
    assert ("Naver", "name") in hn.aliases({"name": "NAVER", "market": "KR", "ticker": "035420"})


def test_long_collect_keeps_saying_the_tick_is_alive(hn, portfolio, monkeypatch):
    """L3 — 수집이 `STALE_TICK_MIN` 을 넘기면 판정이 '봇 주기 멈춤' 으로 읽었다. 종목마다 살아 있다는 표시를
    남긴다(간격 `BEAT_SEC`)."""
    monkeypatch.setattr(hn, "BEAT_SEC", 0)
    seen = []

    def fetch(st):
        seen.append(hn.load_state().get("last_tick"))
        hn._save_state({"last_tick": "2000-01-01T00:00:00+09:00"})       # 다른 무엇이 덮어써도
        return _news_for_all(st)
    hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=fetch, body=lambda l: "", pace=0)
    assert seen == ["2026-10-12T07:15:00+09:00"] * 2                   # 둘째 종목 앞에서 다시 남겼다


def test_long_collect_beats_at_most_once_per_interval(hn, portfolio):
    seen = []

    def fetch(st):
        seen.append(hn.load_state().get("last_tick"))
        hn._save_state({"last_tick": "2000-01-01T00:00:00+09:00"})
        return _news_for_all(st)
    hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=fetch, body=lambda l: "", pace=0)
    assert seen == ["2026-10-12T07:15:00+09:00", "2000-01-01T00:00:00+09:00"]   # 60초 안엔 다시 안 쓴다


def test_google_news_empty_ok_tells_zero_from_failure(tmp_path, monkeypatch):
    """L4 — 0건과 실패가 둘 다 None 이라 0건이 '원천 실패' 로 세어졌다. 묻는 쪽이 고른다(옛 호출부는 그대로)."""
    import bot.google_news_client as gn
    monkeypatch.setattr(gn, "_CACHE_DIR", tmp_path / "g")
    empty = b"<rss><channel></channel></rss>"
    monkeypatch.setattr(gn.requests, "get", lambda *a, **k: types.SimpleNamespace(
        raise_for_status=lambda: None, content=empty))
    assert gn.fetch_news("없는말하나") is None
    assert gn.fetch_news("없는말둘", empty_ok=True) == []
    monkeypatch.setattr(gn.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert gn.fetch_news("없는말셋", empty_ok=True) is None


def test_fetch_google_counts_zero_results_as_received(hn, monkeypatch):
    import bot.google_news_client as gn
    monkeypatch.setattr(gn, "fetch_news", lambda *a, **k: [] if k.get("empty_ok") else None)
    assert hn._fetch_google("삼성전자", "KR") == ([], "")
    monkeypatch.setattr(gn, "fetch_news", lambda *a, **k: None)
    assert hn._fetch_google("삼성전자", "KR") == (None, "구글 뉴스 실패(로그 참조)")


@pytest.mark.parametrize("stock, want", [
    ({"key": "JP:7203.T", "market": "JP", "ticker": "7203.T", "name": "도요타"}, ("도요타", "KR")),
    ({"key": "JP:7203.T", "market": "JP", "ticker": "7203.T", "name": "Toyota"}, ("Toyota", "JP")),
    ({"key": "US:NVDA", "market": "US", "ticker": "NVDA", "name": "엔비디아"}, ("NVDA stock", "US")),
])
def test_foreign_google_query_asks_in_the_query_language(hn, monkeypatch, stock, want):
    """L6 — 자산 기록의 한글 이름을 일본 로케일로 물으면 늘 0건이다."""
    asked = []
    monkeypatch.setattr(hn, "_fetch_yf", lambda t: ([], ""))
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: asked.append((q, mk)) or ([], ""))
    hn.fetch_stock_news(stock)
    assert asked == [want]


def test_analyze_budget_is_shared_between_today_and_yesterday(hn, portfolio, monkeypatch):
    """한 주기의 AI 호출 상한은 오늘·어제 합이다 — 어제 몫에 남은 상한만 준다."""
    monkeypatch.setattr(hn, "MAX_LLM_CALLS", 2)
    hn.save_record(_pending_day("2026-10-12"))
    yday = _pending_day("2026-10-11")
    yday["stocks"] = [_stock(f"KR:{i:06d}", 1) for i in range(hn.BATCH_MAX + 2)]
    hn.save_record(yday)
    llm = _llm(_answer_all)
    res = hn.tick(_t(_NOW), llm=llm, fetch=pytest.fail, body=pytest.fail, pace=0)
    assert len(llm.calls) == 2 and res["calls"] == 2


def test_market_with_raw_results_outside_the_window_is_not_an_outage(hn):
    rec = _src_rec(*[{**_s("US"), "items": []}] * 4, *[_s("KR")] * 4, naver=4, yfinance=4)
    assert hn.status_verdict([rec], _t("2026-10-12T09:00"), {})[0] == "ok"   # 원천은 줬다 — 창 밖일 뿐


@pytest.mark.parametrize("n, kind", [(12, "fail"), (13, "ok")])
def test_outage_share_boundary(hn, n, kind):
    rec = _src_rec(*[_s("KR", failed=True)] * 3, *[_s("KR")] * (n - 3), fail=3, naver=n - 3)
    assert hn.status_verdict([rec], _t("2026-10-12T09:00"), {})[0] == kind


def test_us_etf_cache_only_reads_an_old_cache_without_network(tmp_path, monkeypatch):
    import httpx

    import bot.us_symbol_names as us
    cache = tmp_path / "etf.json"
    monkeypatch.setattr(us, "_ETF_CACHE_FILE", cache)
    cache.write_text(json.dumps(["AIRR"]), encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(cache, (old, old))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("진단은 묻지 않는다"))
    assert us.us_etf_symbols(cache_only=True) == {"AIRR"}


def test_board_audit_counts_saved_days_by_itself(hn):
    from bot.scripts.board_audit import _holdings_news_line
    for i in range(5):
        hn.save_record({"date": f"2026-10-0{i + 1}", "stocks": []})
    assert " 기록 5일 · " in _holdings_news_line(now=_t("2026-10-12T06:00"))


def test_regenerate_says_how_many_days_are_kept(hn, monkeypatch, tmp_path):
    import bot.dashboard as d
    monkeypatch.setattr(d, "ARCHIVE_ROOT", tmp_path / "arch")
    start = datetime(2026, 8, 1)
    for i in range(hn.RENDER_DAYS + 3):
        hn.save_record({"date": (start + timedelta(days=i)).date().isoformat(), "stocks": []})
    hn.regenerate(_t("2026-10-12T09:00"))
    page = (tmp_path / "arch" / "holdings_news.html").read_text(encoding="utf-8")
    assert f"총 {hn.RENDER_DAYS + 3}일 기록 중 최근 {hn.RENDER_DAYS}일 표시" in page


def test_rejection_after_a_transient_error_drops_the_retry_promise(hn):
    """일시 오류(약속 붙음) 뒤 거부가 오면 사유가 거부로 바뀐다 — 약속 표시가 남으면 거부 사유에 일시 오류의
    '다시' 가 붙는다."""
    rec = _rec(_stock("KR:A", 1))
    hn.analyze(rec, llm=_llm(None, pt=0, ot=0, why="AI 일시 오류(503)", kind="transient"))
    assert rec["stocks"][0]["retry"] is True
    hn.analyze(rec, llm=_llm(None, pt=0, ot=0, why="AI 호출 실패(400)", kind="rejected"))
    s = rec["stocks"][0]
    assert s["retry"] is False and "Pros/Cons 대기 — AI 호출 실패(400)</div>" in hn._card(s)


# ── 반영분 리뷰 2차(2026-10-13) ───────────────────────────────────────────────
def test_beat_uses_the_live_clock_when_the_tick_is_not_pinned(hn, portfolio, monkeypatch):
    """심박은 **지금** 시각을 남겨야 한다 — 주기 시작 시각을 되쓰면 45분 넘는 수집이 그대로 '멈춤' 이다. 다른 테스트는
    전부 `now` 를 고정해 그 갈래를 한 번도 안 탔다(리뷰 뮤테이션 생존)."""
    import itertools
    clock = (_t("2026-10-12T07:15") + timedelta(minutes=i) for i in itertools.count())
    monkeypatch.setattr(hn, "_now", lambda n=None: n if n is not None else next(clock))
    monkeypatch.setattr(hn, "BEAT_SEC", 0)
    seen = []

    def fetch(st):
        seen.append(hn.load_state()["last_tick"])
        return _news_for_all(st)
    hn.tick(None, llm=_llm(_answer_all), fetch=fetch, body=lambda l: "", pace=0)
    assert len(seen) == 2 and "2026-10-12T07:15:00+09:00" < seen[0] < seen[1]


def test_collect_start_clears_a_stale_failure_reason(hn, portfolio):
    """어제 주기 실패 사유가 남아 있으면 판정이 그걸 먼저 봐 정상 수집 내내 '오늘 집계 못 함' 이다."""
    hn._save_state({"reason": "주기 실패(RuntimeError: x)"})
    during = []

    def fetch(st):
        during.append(hn.status_verdict([], _t("2026-10-12T07:20"), hn.load_state())[0])
        return _news_for_all(st)
    hn.tick(_t(_NOW), llm=_llm(_answer_all), fetch=fetch, body=lambda l: "", pace=0)
    assert during == ["wait", "wait"] and hn.load_state()["reason"] == ""


def test_no_key_after_a_transient_error_drops_the_retry_promise(hn):
    rec = _rec(_stock("KR:A", 1))
    hn.analyze(rec, llm=_llm(None, pt=0, ot=0, why="AI 일시 오류(503)", kind="transient"))
    hn.analyze(rec, llm=_llm(None, why="no_key"))
    s = rec["stocks"][0]
    assert s["retry"] is False and "다음 주기에 다시" not in hn._card(s)


def test_rejected_stock_never_claims_its_body_was_read(hn):
    s = _stock("KR:A", 1)
    s["items"][0].update(k="https://p/KR:A/1", _body="본문", body_chars=2)
    rec = _rec(s)
    bad = _llm(None, pt=0, ot=0, why="AI 호출 실패(400)", kind="rejected")
    for _ in range(hn.MAX_ANALYZE_TRIES):
        hn.analyze(rec, llm=bad)
    assert s["status"] == "failed" and "body_used" not in s["items"][0]
    assert "AI 가 읽음" not in hn._card(s)


@pytest.mark.parametrize("name, titled", [("ABCD", "Abcd"), ("ABC", None)])
def test_title_case_alias_starts_at_four_letters(hn, name, titled):
    als = hn.aliases({"name": name, "market": "KR", "ticker": "000001"})
    assert [a for a, k in als if k == "name"] == [name] + ([titled] if titled else [])


def test_second_name_failure_is_logged_and_google_is_paced(hn, monkeypatch, caplog):
    slept = []
    monkeypatch.setattr(hn.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(hn, "_fetch_naver", lambda q: (None, "네이버 키 없음"))
    monkeypatch.setattr(hn, "_fetch_google", lambda q, mk: (
        [_it("케이티 a", "2026-10-12T05:00:00+09:00", link="https://n/a")], "") if q == "케이티" else (None, "구글 뉴스 실패(로그 참조)"))
    kt = {"key": "KR:030200", "market": "KR", "ticker": "030200", "name": "KT", "ko": "케이티"}
    with caplog.at_level("WARNING", logger="bot.holdings_news"):
        items, used, why = hn.fetch_stock_news(kt)
    assert [x["link"] for x in items] == ["https://n/a"] and used == "google"   # 1순위 결과로 간다
    assert slept == [hn.ALT_PACE]
    assert any("KR:030200 영문 이름 'KT' 검색 실패: 구글 뉴스 실패(로그 참조)" in r.getMessage() for r in caplog.records)
    assert hn.alt_query({"market": "US", "name": "KT", "ko": "케이티"}) == ""
    assert hn.alt_query({"market": "KR", "name": "케이티", "ko": "케이티"}) == ""


def test_probe_names_the_second_query(hn, monkeypatch):
    import bot.dart_client as dc
    import bot.portfolio as pf
    monkeypatch.setattr(pf, "load", lambda: _model(_h("KT", "030200", "KR")))
    monkeypatch.setattr(dc, "get_dart", lambda: types.SimpleNamespace(news_search_name=lambda c: "케이티"))
    monkeypatch.setattr(hn, "fetch_stock_news", lambda s: ([], "naver", ""))
    out = []
    hn.probe(None, out=out.append)
    assert any("검색어 '케이티'(DART 한글명 '케이티') + 영문 'KT'" in ln for ln in out)


def test_sidecar_survives_a_save_of_a_reloaded_record(hn, caplog):
    """다시 읽은 기록(본문이 메모리에 없다)을 붙이기 전에 저장해도 사이드카의 본문을 지우지 않는다 · 사이드카를 못
    읽으면 이번엔 덮지 않는다."""
    s = _stock("KR:A", 1)
    s["items"][0].update(k="https://p/1", _body="본문 원래", body_chars=5)
    hn.save_record({"date": "2026-10-12", "stocks": [s]})
    side = hn._bodies_path("2026-10-12")
    reloaded = hn.load_records()[0]
    assert "_body" not in reloaded["stocks"][0]["items"][0]
    hn.save_record(reloaded)
    assert json.loads(side.read_text(encoding="utf-8")) == {"KR:A": {"https://p/1": "본문 원래"}}
    side.write_bytes(b"\xff\xfe broken")
    with caplog.at_level("WARNING", logger="bot.holdings_news"):
        hn.save_record(reloaded)
    assert side.read_bytes() == b"\xff\xfe broken"
    assert any("못 읽어 이번엔 갱신하지 않음" in r.getMessage() for r in caplog.records)
    reloaded["stocks"][0]["status"] = "ok"                              # 다 정리되면 지운다
    hn.save_record(reloaded)
    assert not side.exists()


# ── 카드 머리: 종목분석 링크 · 전날 등락(사용자 2026-10-13) ────────────────────────
@pytest.fixture
def ws_snaps(tmp_path, monkeypatch):
    """`world_sectors` 하루치 저장소를 임시로 — 원천(TradingView) 형식 그대로 `KRX:005930` 기호·ROW 순서."""
    import gzip

    import bot.world_sectors as ws
    import bot.holdings_news as hnm
    monkeypatch.setattr(ws, "_SNAP", tmp_path / "snap")
    monkeypatch.setattr(hnm, "_PREV_CACHE", {})
    ws.cache_clear()

    def put(market, d, rows):
        p = ws.snap_path(market, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        full = [[sym, sym, None, None, chg, 1.0, 1.0, None, 1.0, 0] for sym, chg in rows]
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            json.dump({"rows": full}, fh)
        ws.cache_clear()
    yield put
    ws.cache_clear()


def _closes(hn, rows: dict):
    """봇 주기(`refresh_closes`)가 받아 둔 Yahoo 일봉 종가 캐시를 심는다 — {보유 키: ({날짜: 종가}, 받은 시각 KST ISO)}."""
    p = hn._closes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: {"y": "", "bars": b, "at": _t(at).timestamp(), "tried": _t(at).timestamp()}
                             for k, (b, at) in rows.items()}), encoding="utf-8")


def _hs(market, ticker, sym=None, name="X"):
    return {"key": f"{market}:{ticker}", "market": market, "ticker": ticker, "sym": sym or ticker,
            "name": name, "value": 1.0, "items": [], "status": "pending"}


def test_prev_change_reads_the_previous_session_of_each_market(hn, ws_snaps):
    """'전날' = 기록 날짜 **앞** 그 시장의 마지막 거래일 — 2026-10-12(월) 기록이면 한국은 10-09 한글날
    휴장이라 10-08, 미국은 10-09. 날짜를 하루 빼는 식이면 한국은 휴장일(빈 하루치)을 찾는다."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", 1.234), ("KRX:000660", -2.0)])
    ws_snaps("KR", "2026-10-09", [("KRX:005930", 99.0)])          # 있어선 안 될 날 — 집으면 틀린다
    ws_snaps("US", "2026-10-09", [("NASDAQ:NVDA", -0.5), ("NYSE:BRK.B", 0.0)])
    look = hn.prev_changes("2026-10-12")
    assert look(_hs("KR", "005930", "005930.KS")) == (1.234, "2026-10-08", "")
    assert look(_hs("KR", "000660")) == (-2.0, "2026-10-08", "")
    assert look(_hs("US", "NVDA")) == (-0.5, "2026-10-09", "")
    assert look(_hs("US", "BRK-B")) == (0.0, "2026-10-09", "")     # 기호 BRK.B ↔ 자산 기록 BRK-B
    v, d, why = look(_hs("US", "AAPL"))
    assert v is None and "2026-10-09 하루치에 이 종목이 없음" in why


def test_prev_change_missing_day_and_market_say_why(hn, ws_snaps):
    # 시계를 그날 아침에 묶는다 — 실제 시계면 미국 10-12 창이 닫힌 뒤 '놓침' 갈래로 바뀐다(#425)
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 10, 13, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    look = hn.prev_changes("2026-10-13", now=now)                   # 미국 10-12 하루치는 아직 없음
    v, d, why = look(_hs("US", "NVDA"))
    assert v is None and d == "2026-10-12" and "아직 받지 않음" in why and "3시간" in why
    v, d, why = look(_hs("HK", "0700.HK"))
    assert v is None and "HK 시장은 전날 등락 원천이 없음" in why


def test_prev_change_failure_only_empties_that_cell(hn, ws_snaps, monkeypatch, caplog):
    import bot.world_sectors as ws

    def boom(*a, **k):
        raise RuntimeError("disk")
    monkeypatch.setattr(ws, "prev_sessions", boom)
    with caplog.at_level("WARNING"):
        v, d, why = hn.prev_changes("2026-10-13")(_hs("KR", "005930"))
    assert v is None and why == "전날 등락 조회 실패" and "전날 등락 조회 실패" in caplog.text


def test_card_header_shows_link_and_prev_change(hn, ws_snaps):
    """카드 머리 — 이름 = 종목분석 링크(접미 없는 국내 6자리, #399), 괄호 = 전날 등락(색은 한국 관례),
    값이 없으면 `(—)` + 사유 툴팁. 배선은 `_day_body` 로 태운다(#20 — 헬퍼만 재면 떼어도 통과)."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", 1.234), ("KRX:000660", -2.0), ("KRX:035720", 0.0)])
    s1 = {**_stock("KR:005930"), "name": "삼성전자", "value": 100_000_000.0}
    s2 = {**_stock("KR:000660"), "name": "SK하이닉스", "value": 30_000_000.0}
    s3 = {**_stock("KR:035720"), "name": "카카오", "value": 5_000.0}
    s4 = {**_stock("KR:999999"), "name": "없음"}
    body = hn._day_body(_rec(s1, s2, s3, s4))
    assert ("<a class='hn-nm' href='lookup/005930' title='종목분석 화면으로'>삼성전자</a>"
            "<span class='hn-chg hn-up' title='2026-10-08 종가 기준") in body
    # 2026-10-14 사용자 "퍼센티지 옆에 금액" — 금액 = 보유 평가금액 × 등락률(추정, 툴팁이 밝힌다)
    assert "<span class='hn-chg hn-dn'" in body and "(-2.00% · -60만원)</span>" in body
    assert "(+1.23% · +123만원)</span>" in body and "(0.00% · 0원)</span>" in body and "(+0.00%" not in body
    assert "금액 = 보유 평가금액 × 등락률(추정)" in body
    assert "<span class='hn-chg ' title='2026-10-08" in body                       # 보합은 색 없음
    assert "title='2026-10-08 하루치에 이 종목이 없음'>(—)</span>" in body


def test_prev_change_css_uses_defined_tokens(hn):
    css = hn._HN_CSS
    for tok in ("--hn-up", "--hn-dn"):
        assert css.count(f"{tok}:") == 2                    # 라이트·다크 둘 다 정의
    assert ".hn-chg.hn-up{color:var(--hn-up)}" in css and ".hn-chg.hn-dn{color:var(--hn-dn)}" in css


def test_guide_run_window_derives_from_run_time(hn, monkeypatch):
    monkeypatch.setattr(hn, "RUN_HOUR", 8)
    monkeypatch.setattr(hn, "RUN_MIN", 0)
    assert hn._hm_after(hn.TICK_MIN) == "08:00~08:15"
    assert hn._hm_after(30) == "08:00~08:30"                  # 인자를 따른다(주기를 바꾸면 구간도)
    monkeypatch.setattr(hn, "RUN_HOUR", 23)
    monkeypatch.setattr(hn, "RUN_MIN", 50)
    assert hn._hm_after(15) == "23:50~00:05"
    page = hn.render_page([], None, _t("2026-10-13T09:00"))
    assert "매일 23:50 KST 이후" in page and "23:50~00:05 사이" in page


def test_as_of_falls_back_to_the_upload_time(hn):
    """자산 기록은 `as_of` 를 안 싣는다(파서가 안 채운다 — 옛 픽스처가 지어낸 키, #155). 자산 대시보드의
    '마지막 업데이트' 와 같은 `_saved_ts` 로 말한다."""
    ts = datetime(2026, 10, 12, 21, 3, tzinfo=timezone(timedelta(hours=9))).timestamp()
    _, sk = hn.universe({"holdings": [], "_saved_ts": ts}, us_etf=set())
    assert sk["as_of"] == "업로드 2026-10-12 21:03 KST"
    _, sk = hn.universe({"holdings": []}, us_etf=set())
    assert sk["as_of"] is None


def test_prev_change_table_is_cached_by_mtime(hn, ws_snaps, monkeypatch):
    """재생성(15분)마다 30일 × 시장의 하루치를 다시 풀지 않는다 — 같은 파일이면 한 번만 읽고, 파일이 바뀌면
    (mtime) 다시 읽는다. 국가별 보드의 원본 캐시는 건드리지 않는다(30일을 훑으면 그 보드 캐시를 밀어낸다)."""
    import os

    import bot.world_sectors as ws
    ws_snaps("KR", "2026-10-12", [("KRX:005930", 1.0)])
    reads = []
    real = ws._gz_load
    monkeypatch.setattr(ws, "_gz_load", lambda p: reads.append(p) or real(p))
    monkeypatch.setattr(ws, "load_snapshot", lambda *a, **k: pytest.fail("국가별 보드 캐시를 쓰면 안 된다"))
    st = _hs("KR", "005930")
    assert hn.prev_changes("2026-10-13")(st)[0] == 1.0
    assert hn.prev_changes("2026-10-13")(st)[0] == 1.0
    assert len(reads) == 1
    ws_snaps("KR", "2026-10-12", [("KRX:005930", 2.5)])
    p = ws.snap_path("KR", "2026-10-12")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10**9))
    assert hn.prev_changes("2026-10-13")(st)[0] == 2.5 and len(reads) == 2


def test_prev_change_nan_is_not_a_value(hn, ws_snaps):
    """원천이 NaN 을 주면(거래정지 등) '(nan%)' 가 아니라 '없음' — 사유와 함께 `(—)`."""
    ws_snaps("KR", "2026-10-12", [("KRX:005930", float("nan"))])
    v, d, why = hn.prev_changes("2026-10-13")(_hs("KR", "005930"))
    assert v is None and "하루치에 이 종목이 없음" in why


def test_prev_change_missing_reason_follows_the_board_status(hn, ws_snaps, monkeypatch):
    """하루치가 없을 때 '아직' 은 받을 창이 남았을 때만 — 창을 놓친 날·휴장·보관 기간 밖이면 그렇게 말한다
    (영원히 안 채워질 칸에 '다음 갱신에 채워짐' 이라 적으면 #380). 판정은 국가별 보드의 것을 그대로(#35)."""
    import bot.world_sectors as ws
    seen = []

    def status(mk, d, now, state=None):
        seen.append((mk, d, now.isoformat()))
        return box["st"]
    monkeypatch.setattr(ws, "market_day_status", status)
    box = {}
    want = {"pending": "아직 받지 않음", "missing": "받을 창을 놓쳐", "holiday": "휴장(추정)",
            "closed": "휴장(추정)", "ok": "보관 기간(45일) 밖", "": "2026-10-12 하루치가 없음"}
    for st, txt in want.items():
        box["st"] = st
        v, d, why = hn.prev_changes("2026-10-13", _t("2026-10-13T08:05"))(_hs("KR", "005930"))
        assert v is None and txt in why, (st, why)
        assert ("다음 갱신에 채워짐" in why) == (st == "pending"), (st, why)
    assert seen[0] == ("KR", "2026-10-12", "2026-10-13T08:05:00+09:00")         # 호출부의 now 를 넘긴다


def test_prev_cache_cap_covers_every_rendered_day_and_market(hn):
    """재생성은 화면의 날 × 시장을 매번 같은 순서로 훑는다 — 상한이 그보다 작으면 FIFO 가 매 패스 전부 밀어내
    15분마다 모두 다시 푼다(리뷰 2026-10-13: JP 보유가 생기면 30×3=90 > 80)."""
    assert hn._PREV_CACHE_MAX >= hn.RENDER_DAYS * len(set(hn._PREV_MK.values()))


def test_prev_cache_evicts_when_full(hn, ws_snaps, monkeypatch):
    monkeypatch.setattr(hn, "_PREV_CACHE_MAX", 2)
    for d, c in (("2026-10-07", 1.0), ("2026-10-08", 2.0), ("2026-10-12", 3.0)):
        ws_snaps("KR", d, [("KRX:005930", c)])
    for day in ("2026-10-08", "2026-10-12", "2026-10-13"):
        hn.prev_changes(day)(_hs("KR", "005930"))
    assert len(hn._PREV_CACHE) == 2 and ("KR", "2026-10-07") not in hn._PREV_CACHE


def test_prev_change_us_dot_class_shares(hn, ws_snaps):
    """자산 기록이 BRK.B 로 줘도 표 키(lookup_ticker 꼴 BRK-B)와 맞는다."""
    ws_snaps("US", "2026-10-12", [("NYSE:BRK.B", 0.7)])
    assert hn.prev_changes("2026-10-13")(_hs("US", "BRK.B"))[0] == 0.7


def test_as_of_broken_saved_ts_does_not_raise(hn):
    for bad in (float("inf"), 1e300, "x", None):
        assert hn.universe({"holdings": [], "_saved_ts": bad}, us_etf=set())[1]["as_of"] is None


# ── 투자와 무관한 소식은 Pros/Cons 에 모으지 않는다(사용자 2026-10-13 두산우 · 야구단 경기 기사) ─────────
def test_prompt_keeps_only_investment_relevant_items(hn):
    """'두산' 이름을 단 야구단의 와일드카드·홈런 기사가 Cons 에 실렸다 — 옛 규칙 5 는 '이름이 같은 **다른 회사**'
    만 빼라고 해서, 그 회사 이름이 나오는 비투자 소식은 통과했다. 기준은 '그 회사 얘기인가' 가 아니라 '투자
    판단에 닿나' 다. 구단 매각처럼 재무에 닿는 소식은 예외로 남아야 한다(뺄 것을 지우면 그것도 사라진다)."""
    p = hn.build_prompt([])
    assert "투자 판단에 닿는 내용만" in p and "투자 판단에 닿는 항목만" in p
    assert "둘 다 규칙 5 의 relevant 항목만 근거로" in p                  # 본업 예외가 Pros/Cons 에도 닿는다
    assert "스포츠 구단의 경기 결과" in p and "구단 매각" in p
    # 본업이 스포츠·연예·게임인 회사(HYBE·MANU·TKO)는 그 소식이 곧 사업이다 — 예외가 빠지면 다 지워진다(리뷰 M2)
    assert "본업이거나" in p and "후원·마케팅 계약" in p
    # 규칙 2(Pros·Cons)와 규칙 5(relevant)가 같은 주제 목록 — 따로 들면 5 에서 빠진 근거를 단 줄이 버려진다
    assert p.count(hn.INVEST_TOPICS) == 2


def test_prompt_sig_follows_the_template_and_is_stamped(hn, monkeypatch):
    """지문은 틀에서 파생한다 — 상수를 돌려주는 변형이면 규칙을 고쳐도 아무것도 다시 안 정리된다(#91b·#119)."""
    a = hn.prompt_sig()
    assert a == hn.prompt_sig() and len(a) == 10
    monkeypatch.setattr(hn, "MAX_PER_SIDE", hn.MAX_PER_SIDE + 1)
    assert hn.prompt_sig() != a
    monkeypatch.undo()
    real = hn.build_prompt                      # 규칙 **문구**만 바뀌어도 지문이 바뀐다(상수만 해싱하면 못 본다)
    monkeypatch.setattr(hn, "build_prompt", lambda st: real(st) + "7) 새 규칙\n")
    assert hn.prompt_sig() != a
    monkeypatch.undo()
    s = _stock("KR:A", 2)
    hn.analyze(_rec(s), llm=_llm(json.dumps({"stocks": [{"key": "KR:A", "pros": [{"t": "x", "refs": [1]}]}]})))
    assert s["status"] == "ok" and s["pv"] == hn.prompt_sig()


def _ok(key, *, pv="old", body_link=True):
    s = _stock(key, 3, status="ok")
    for it in s["items"]:
        it["k"] = f"{key}#{it['n']}"                    # 수집이 늘 붙이는 항목 키 — 사이드카가 이걸로 본문을 찾는다
    s.update(pv=pv, relevant=[1, 2], pros=[{"t": "옛 호재", "refs": [1]}], cons=[{"t": "야구 경기 패배", "refs": [2]}],
             dropped_lines=1, tries=2, reason="옛 사유")
    it = s["items"][0]
    it.update(body_chars=500, body_used=True)
    if body_link:
        it["naver_link"] = f"https://n.news.naver.com/{key}/1"
    s["items"][1].update(src="telegram", body_chars=300, naver_link="https://n.news.naver.com/tg")   # 텔레그램은 안 읽는다
    s["items"][2]["link"] = "https://n.news.naver.com/nobody"                                     # body_chars 없음 = 원래 안 읽은 것
    return s


def test_requeue_stale_resets_old_analyses_and_rereads_bodies(hn):
    cur = hn.prompt_sig()
    stale, fresh = _ok("KR:A"), _ok("KR:B", pv=cur)
    failed, pend, empty = _stock("KR:C", status="failed"), _stock("KR:D"), _stock("KR:E", 0, status="empty")
    reads, beats = [], []
    rec = _rec(stale, fresh, failed, pend, empty)
    n = hn.requeue_stale(rec, body=lambda l: reads.append(l) or f"다시 읽은 {l}", pace=0,
                         beat=lambda: beats.append(1))
    assert n == 1 and reads == ["https://n.news.naver.com/KR:A/1"] and beats == [1]   # 되돌린 종목마다 생존 표시
    assert rec["requeued_for"] == [cur]
    assert stale["status"] == "pending" and stale["reason"] == hn.REQUEUE_REASON
    assert stale["pros"] == [] and stale["cons"] == [] and stale["relevant"] is None
    assert stale["tries"] == 0 and stale["retry"] is False and stale["dropped_lines"] == 0
    assert stale["items"][0]["_body"] == "다시 읽은 https://n.news.naver.com/KR:A/1"
    assert all("body_used" not in it for it in stale["items"])           # 이번에 보낸 것만 '읽음' 이 된다
    assert fresh["status"] == "ok" and fresh["pros"] and fresh["items"][0]["body_used"] is True
    assert failed["status"] == "failed" and pend["status"] == "pending" and empty["status"] == "empty"
    assert hn.requeue_stale(rec, body=lambda l: 1 / 0, pace=0) == 0      # 두 번째엔 되돌릴 것이 없다


def test_requeue_stale_reads_a_naver_link_without_a_naver_copy(hn):
    """수집은 `naver_link` 가 없으면 원문 링크가 네이버 호스트일 때 그걸 읽는다 — 되돌릴 때도 같아야 한다."""
    s = _ok("KR:A", body_link=False)
    s["items"][0]["link"] = "https://n.news.naver.com/orig"
    reads = []
    hn.requeue_stale(_rec(s), body=lambda l: reads.append(l) or "본문", pace=0)
    assert reads == ["https://n.news.naver.com/orig"] and s["items"][0]["_body"] == "본문"


def test_requeue_stale_runs_once_per_rule_per_record(hn, monkeypatch):
    """판이 다른 두 프로세스가 겹치면(재시작 실패 + 수동 --run) 서로의 정리를 끝없이 되돌린다 — 한 기록은 같은
    지문으로 한 번만 되돌린다(리뷰 L1)."""
    rec = _rec(_ok("KR:A", pv="B판"))
    monkeypatch.setattr(hn, "prompt_sig", lambda: "A판")
    assert hn.requeue_stale(rec, body=lambda l: "", pace=0) == 1
    rec["stocks"][0].update(status="ok", pv="B판")                     # B판 프로세스가 다시 정리해 덮었다
    assert hn.requeue_stale(rec, body=lambda l: 1 / 0, pace=0) == 0
    assert rec["stocks"][0]["status"] == "ok" and rec["requeued_for"] == ["A판"]
    monkeypatch.setattr(hn, "prompt_sig", lambda: "C판")                 # 새 기준이면 다시 한 번
    assert hn.requeue_stale(rec, body=lambda l: "", pace=0) == 1 and rec["requeued_for"] == ["A판", "C판"]


def test_requeue_reason_promises_only_on_live_days(hn):
    """저장 사유는 중립 — '다시 정리' 는 이어서 정리하는 날의 화면만 붙인다(지킬 수 없는 약속을 굽지 않는다 #447)."""
    s = _ok("KR:A")
    hn.requeue_stale(_rec(s), body=lambda l: "", pace=0)
    assert "다시" not in s["reason"]
    live = hn._card(s)
    assert "Pros/Cons 대기 — AI 정리 기준이 바뀜 — 새 기준으로 다시 정리" in live and "야구 경기 패배" not in live
    # 그날 안에 차례가 안 왔으면 — 옛 정리를 라벨을 달고 보여 준다(지우기만 하면 흔적 없이 사라진다, 델타 리뷰)
    fin = hn._card(s, final=True)
    assert "옛 기준 정리를 보여 줍니다 — 이어서 정리하는 기간 안에 차례가 오지 않음" in fin
    assert "Cons · 악재·리스크 관점(옛 기준)" in fin and "야구 경기 패배" in fin and "Pros/Cons 대기" not in fin
    assert "data-pro='1' data-con='1'" in fin                           # 필터도 보여 준 정리를 따른다
    assert "종목·투자와 무관하다고 본 1건은 흐리게" in fin                  # 옛 relevant 로 흐리게
    s["prev_pc"].update(pros=[], cons=[])                                # 옛 정리에 줄이 없었으면 그렇다고 말한다
    assert "옛 기준 정리에도 호재·악재로 가른 줄이 없었음" in hn._card(s, final=True)
    s.pop("prev_pc")                                                     # 보관본이 없는 옛 기록
    assert "Pros/Cons 정리를 마치지 못함 — AI 정리 기준이 바뀜 · 뉴스만 표시" in hn._card(s, final=True)


def test_failed_reanalysis_shows_the_old_analysis_labelled(hn):
    """다시 정리가 거부로 끝나도(3회) 멀쩡하던 정리가 사라지지 않는다 — 라벨을 달고 보여 주고, 판정 줄도 '뉴스만
    표시' 라고 거짓말하지 않는다. 다시 정리가 성공하면 보관본은 버린다(델타 리뷰 2026-10-13)."""
    s = _ok("KR:A")
    rec = _rec(s)
    hn.requeue_stale(rec, body=lambda l: "", pace=0)
    for _ in range(hn.MAX_ANALYZE_TRIES):
        hn.analyze(rec, llm=_llm("이건 JSON 이 아님"))
    assert s["status"] == "failed" and s["cons"] == []
    card = hn._card(s)
    assert "옛 기준 정리를 보여 줍니다 — AI 응답을 못 읽음" in card and "야구 경기 패배" in card
    assert "Pros/Cons 못 만듦" not in card
    kind, line = hn.status_verdict([rec], _t("2026-10-12T09:00"), {})
    assert kind == "fail" and "Pros/Cons 실패 1종목(그중 1종목은 옛 기준 정리 표시)" in line
    s2 = _ok("KR:B")
    rec2 = _rec(s2)
    hn.requeue_stale(rec2, body=lambda l: "", pace=0)
    hn.analyze(rec2, llm=_llm(_answer_all))
    assert s2["status"] == "ok" and "prev_pc" not in s2 and "옛 기준" not in hn._card(s2)


def test_why_says_how_many_carry_the_old_rule(hn, monkeypatch):
    sig = hn.prompt_sig()
    rec = _rec(_ok("KR:A"), _ok("KR:B", pv=sig), _stock("KR:C"))           # 2026-10-12 기록
    head = f"정리 기준 지문 {sig} · 옛 기준으로 정리된 종목 1/2 — "
    promise = head + "AI 키가 있으면 다음 주기에 새 기준으로 다시 정리"
    assert hn.requeue_note(rec, _t("2026-10-12T09:00")) == promise          # 오늘 기록
    assert hn.requeue_note(rec, _t("2026-10-13T06:00")) == promise          # 집계 시각 전 — 아직 가장 최근
    # 집계 시각이 지났는데 오늘 기록이 없다 — 다음 주기가 오늘을 모으므로 이 기록은 다시 정리되지 않는다
    assert hn.requeue_note(rec, _t("2026-10-13T09:00")) == head + "다음 주기가 오늘 기록을 새 기준으로 정리하고, 이 기록은 옛 정리 그대로"
    rec["requeued_for"] = [sig]
    assert "이미 한 번 되돌렸음" in hn.requeue_note(rec, _t("2026-10-12T09:00"))
    assert hn.requeue_note(_rec(_ok("KR:B", pv=sig)), _t("2026-10-12T09:00")) == \
        f"정리 기준 지문 {sig} · 정리된 종목 1 전부 이 기준"
    # 대조할 정리가 0건이면 '전부 이 기준' 이라 하지 않는다(#54)
    assert hn.requeue_note(_rec(_stock("KR:C")), _t("2026-10-12T09:00")) == f"정리 기준 지문 {sig} · 정리된 종목 없음"
    hn.save_record({**_rec(_ok("KR:A")), "date": "2026-10-13"})
    # 인자 없이 부르면 실제 시계(먼 미래로 고정)를 본다 — why 가 자기 now 를 넘기는지 시계와 무관하게 잰다(#425)
    real_now = hn._now
    monkeypatch.setattr(hn, "_now", lambda now=None: real_now(now) if now else _t("2030-01-02T09:00"))
    lines = []
    hn.why(_t("2026-10-13T09:00"), out=lines.append)
    assert any(ln.startswith("   정리 기준 지문 ") and "옛 기준으로 정리된 종목 1/1 — AI 키가 있으면" in ln
               for ln in lines)


def test_requeue_stale_marks_the_record_only_when_it_requeued(hn):
    """되돌린 게 없으면 '이 지문으로 되돌렸음' 을 남기지 않는다 — 남기면 나중에 옛 기준 종목이 생겨도 영영 안
    돈다(델타 리뷰 L2)."""
    rec = _rec(_stock("KR:A"), _ok("KR:B", pv=hn.prompt_sig()))
    assert hn.requeue_stale(rec, body=lambda l: "", pace=0) == 0 and "requeued_for" not in rec
    rec["stocks"].append(_ok("KR:C"))
    assert hn.requeue_stale(rec, body=lambda l: "", pace=0) == 1 and rec["requeued_for"] == [hn.prompt_sig()]


def test_requeue_stale_unread_body_and_budget(hn, monkeypatch):
    """본문을 못 읽으면 본문 없이 간다 · 다시 읽기는 호출당 상한까지(원천을 몰아 치지 않게)."""
    monkeypatch.setattr(hn, "MAX_BODY_PER_RUN", 1)
    a, b = _ok("KR:A"), _ok("KR:B")
    reads = []
    assert hn.requeue_stale(_rec(a, b), body=lambda l: reads.append(l) or "", pace=0) == 2
    assert len(reads) == 1 and "_body" not in a["items"][0] and "body_used" not in a["items"][0]
    assert b["status"] == "pending" and "_body" not in b["items"][0]


def test_requeue_pending_card_does_not_show_old_pros(hn):
    s = _ok("KR:A")
    s.update(market="KR", ticker="005930", name="두산우", key="KR:000155")
    hn.requeue_stale(_rec(s), body=lambda l: "", pace=0)
    html = hn.render_page([{**_rec(s), "date": "2026-10-13"}], {}, _t("2026-10-13T09:00"), gen="GEN")
    card = html.split("data-q='두산우")[1].split("data-hncard")[0]
    assert "Pros/Cons 대기 — " + hn.REQUEUE_REASON + " — 새 기준으로 다시 정리" in card and "야구 경기 패배" not in card
    assert html.split("data-q='두산우")[0].rsplit("<div class='hn-card'", 1)[1].count("data-pro='0' data-con='0'") == 1


def test_tick_reanalyzes_only_the_newest_record_with_the_new_rule(hn, monkeypatch):
    """규칙이 바뀌면 **가장 최근 기록**의 'ok' 종목을 다시 정리한다 — 본문을 다시 읽어 프롬프트에 싣는다. 직전
    기록은 건드리지 않는다: 밤늦게 되돌리면 자정에 그날이 이어서 정리하는 날 밖으로 밀려 옛 정리만 잃는다(리뷰 M1).
    한 번 다시 정리하면 다음 주기엔 AI 를 안 부른다."""
    monkeypatch.setattr(hn, "_llm_ready", lambda: True)
    for d in ("2026-10-10", "2026-10-12", "2026-10-13"):
        hn.save_record({**_rec(_ok("KR:A"), _ok("KR:B", pv=hn.prompt_sig()), _ok("KR:D")), "date": d})
    llm = _llm(_answer_all)
    res = hn.tick(_t("2026-10-13T09:00"), llm=llm, fetch=_news_for_all, body=lambda l: f"본문 {l}", pace=0)
    assert res["status"] == "ok" and res["requeued"] == 2 and res["analyzed"] == 2
    assert len(llm.calls) == 1 and "본문: 본문 https://n.news.naver.com/KR:A/1" in llm.calls[0]
    assert "본문: 본문 https://n.news.naver.com/KR:D/1" in llm.calls[0]
    assert "KR:B" not in llm.calls[0]                                   # 지금 규칙으로 정리된 종목은 그대로
    recs = {r["date"]: r for r in hn.load_records()}
    a = recs["2026-10-13"]["stocks"][0]
    assert a["status"] == "ok" and a["pv"] == hn.prompt_sig() and a["pros"][0]["t"] == "좋은 소식"
    assert a["cons"] == [] and a["items"][0]["body_used"] is True
    for d in ("2026-10-10", "2026-10-12"):                              # 그 앞의 기록은 옛 정리 그대로
        old = recs[d]["stocks"][0]
        assert old["status"] == "ok" and old["pv"] == "old" and old["cons"][0]["t"] == "야구 경기 패배"
    again = _llm(_answer_all)
    assert hn.tick(_t("2026-10-13T09:15"), llm=again, fetch=_news_for_all, pace=0)["requeued"] == 0
    assert again.calls == []


def test_tick_before_collect_requeues_the_previous_record(hn, monkeypatch):
    """오늘 기록이 아직 없으면(집계 시각 전) 가장 최근 기록은 직전 기록이다 — 그날은 오늘 자정까지 이어서 정리된다."""
    monkeypatch.setattr(hn, "_llm_ready", lambda: True)
    hn.save_record({**_rec(_ok("KR:A")), "date": "2026-10-12"})
    res = hn.tick(_t("2026-10-13T06:00"), llm=_llm(_answer_all), fetch=_news_for_all, body=lambda l: "", pace=0)
    assert res["status"] == "wait" and res["requeued"] == 1 and res["analyzed"] == 1


def test_tick_without_ai_key_keeps_the_old_analysis(hn, monkeypatch):
    """키가 없으면 다시 정리할 수 없다 — 지우면 멀쩡한 카드가 '키 미설정 — 뉴스만' 이 된다(리뷰 M1)."""
    monkeypatch.setattr(hn, "_llm_ready", lambda: False)
    hn.save_record({**_rec(_ok("KR:A")), "date": "2026-10-13"})
    llm = _llm(_answer_all)
    res = hn.tick(_t("2026-10-13T09:00"), llm=llm, fetch=_news_for_all, body=lambda l: 1 / 0, pace=0)
    assert res["requeued"] == 0 and llm.calls == []
    a = hn.load_records()[0]["stocks"][0]
    assert a["status"] == "ok" and a["cons"][0]["t"] == "야구 경기 패배"


def test_llm_ready_reads_the_key_and_fails_closed(hn, monkeypatch):
    import bot.genai_factory as gf
    monkeypatch.setattr(gf, "effective_key", lambda: "k")
    assert hn._llm_ready() is True
    monkeypatch.setattr(gf, "effective_key", lambda: "")
    assert hn._llm_ready() is False
    monkeypatch.setattr(gf, "effective_key", lambda: 1 / 0)
    assert hn._llm_ready() is False


def test_tick_saves_the_requeue_before_asking_the_ai(hn, monkeypatch):
    """AI 를 부르기 전에 되돌린 상태와 다시 읽은 본문을 저장한다 — 정리가 예외로 끝나도 화면이 옛 정리를 계속
    보여 주지 않고, 다음 주기는 본문을 또 읽지 않는다(사이드카)."""
    monkeypatch.setattr(hn, "_llm_ready", lambda: True)
    hn.save_record({**_rec(_ok("KR:A")), "date": "2026-10-13"})

    def boom(prompt):
        raise RuntimeError("AI 경로 예외")
    with pytest.raises(RuntimeError):
        hn.tick(_t("2026-10-13T09:00"), llm=boom, fetch=_news_for_all, body=lambda l: f"본문 {l}", pace=0)
    prev = hn.load_records()[0]["stocks"][0]
    assert prev["status"] == "pending" and prev["reason"] == hn.REQUEUE_REASON and prev["cons"] == []
    reads = []
    llm = _llm(_answer_all)
    hn.tick(_t("2026-10-13T09:15"), llm=llm, fetch=_news_for_all, body=lambda l: reads.append(l) or "", pace=0)
    assert reads == [] and "본문: 본문 https://n.news.naver.com/KR:A/1" in llm.calls[0]


# ── 무관 소식 확인(사용자 2026-10-14 'A') ────────────────────────────────────
# 10-14 09:12 정리분: 새 규칙(#1333)으로 정리됐는데도 두산우 Cons 에 '야구팀 선수 부상' 이 남았다 — 프롬프트만으로는
# 못 막아 스포츠·연예 문맥 줄만 AI 에 한 번 더 묻는다. 가짜 AI 는 프롬프트로 정리/확인을 가른다.
def _sport_stock():
    s = _stock("KR:000155", 2)
    s["name"] = "두산우"
    s["items"][0]["title"] = "두산 베어스 주전 선수 부상으로 이탈"
    s["items"][1]["title"] = "두산, CCL 판가 인상 수혜 전망"
    return s


def _two_step(keep, *, check_text=None, check_kind=""):
    """정리 응답 = 야구 Cons 1줄 + 판가 Pros 1줄 · 확인 응답 = `keep`(None 이면 그 id 를 빼고 답한다)."""
    calls = []

    def f(prompt):
        calls.append(prompt)
        if "검수하는 보조" in prompt:
            if check_kind:
                return None, 0, 0, "AI 일시 오류(ServerError: 503)", check_kind
            if check_text is not None:
                return check_text, 300, 20, "", ""
            ids = [ln.split("id=")[1].split(" ")[0] for ln in prompt.splitlines() if ln.startswith("### id=")]
            return json.dumps({"checks": [{"id": i, "keep": keep} for i in ids if keep is not None]}), 300, 20, "", ""
        keys = [ln.split("key=")[1].split(" ")[0] for ln in prompt.splitlines() if ln.startswith("### key=")]
        out = {"stocks": [{"key": k, "relevant": [1, 2],
                           "pros": [{"t": "CCL 판가 인상 수혜", "refs": [2]}],
                           "cons": [{"t": "야구팀 주요 선수 부상" if k != "KR:B" else "실적 둔화",
                                     "refs": [1]}]} for k in keys]}
        return json.dumps(out, ensure_ascii=False), 1000, 200, "", ""
    f.calls = calls
    return f


def test_offtopic_candidates_look_at_line_and_cited_titles_not_bodies(hn):
    s = _sport_stock()
    s["pros"] = [{"t": "CCL 판가 인상 수혜", "refs": [2]}]
    s["cons"] = [{"t": "주가 변동 가능성", "refs": [1]}]          # 줄엔 낱말이 없지만 근거 제목에 '선수'
    assert hn.offtopic_candidates(s) == [("cons", 0)]
    s["items"][1]["_body"] = "본문 중 야구 이야기"                    # 본문의 지나가는 낱말은 후보로 안 센다
    assert hn.offtopic_candidates(s) == [("cons", 0)]
    s["pros"] = [{"t": "축구 후원 계약", "refs": [2]}]                  # 줄 글 자체에 낱말
    assert hn.offtopic_candidates(s) == [("pros", 0), ("cons", 0)]


def test_check_drops_lines_the_ai_calls_offtopic_and_dims_their_items(hn):
    s = _sport_stock()
    rec = _rec(s)
    f = _two_step(False)
    done = hn.analyze(rec, llm=f)
    assert len(f.calls) == 2 and "id=c1" in f.calls[1] and "선수 부상" in f.calls[1]
    assert s["status"] == "ok" and s["cons"] == [] and s["pros"] == [{"t": "CCL 판가 인상 수혜", "refs": [2]}]
    assert s["offtopic_lines"] == 1 and s["relevant"] == [2]        # 뺀 줄만 쓰던 [1] 은 흐리게
    assert done["analyzed"] == 1 and rec["llm"]["calls"] == 2 and rec["llm"]["checks"] == 1
    card = hn._card(s)
    assert "다시 확인해 뺀 줄 1개" in card and "선수 부상" not in card.split("hn-src")[0]
    assert "AI 2회(그중 무관 소식 확인 1회)" in hn._day_meta(rec)


def test_check_keeps_lines_the_ai_calls_relevant(hn):
    s = _sport_stock()
    hn.analyze(_rec(s), llm=_two_step(True))
    assert s["cons"] == [{"t": "야구팀 주요 선수 부상", "refs": [1]}] and s["offtopic_lines"] == 0
    assert s["relevant"] == [1, 2] and s["check_why"] == ""
    assert "다시 확인" not in hn._card(s)


def test_no_candidate_means_no_check_call(hn):
    s = _stock("KR:A", 2)
    f = _llm(json.dumps({"stocks": [{"key": "KR:A", "cons": [{"t": "실적 둔화", "refs": [1]}]}]}))
    hn.analyze(_rec(s), llm=f)
    assert len(f.calls) == 1 and s["cons"] and "checks" not in _rec()["llm"]


@pytest.mark.parametrize("check_text, why", [
    ("JSON 아님", "무관 소식 확인 응답을 못 읽음"),
    (json.dumps({"checks": []}), "무관 소식 확인 응답에 이 줄이 없음"),
    (json.dumps({"checks": [{"id": "c1", "keep": "false"}]}), "무관 소식 확인 응답에 이 줄이 없음"),
])
def test_check_unreadable_keeps_the_line_and_says_so(hn, check_text, why):
    """모를 때는 남긴다 — 빼는 쪽이 틀리면 진짜 호재·악재가 사라진다. 대신 카드가 확인 못 했다고 말한다."""
    s = _sport_stock()
    hn.analyze(_rec(s), llm=_two_step(None, check_text=check_text))
    assert s["status"] == "ok" and s["cons"] and s["check_why"] == why
    assert f"다시 확인하지 못해 그대로 둠 — {why}" in hn._card(s)


def test_check_rejected_keeps_line_with_reason(hn):
    s = _sport_stock()
    hn.analyze(_rec(s), llm=_two_step(None, check_kind="rejected"))
    assert s["status"] == "ok" and s["cons"] and s["check_why"].startswith("무관 소식 확인 실패(AI 일시 오류")


def test_check_transient_reverts_to_pending_for_next_cycle(hn):
    """확인이 일시 오류면 확인 안 된 줄을 '확인했다' 로 남기지 않는다 — 대기로 되돌려 다음 주기에 다시."""
    s = _sport_stock()
    s["items"][0]["_body"] = "본문"
    plain = _stock("KR:B", 1)                                          # 후보 없는 종목은 정리 그대로
    rec = _rec(s, plain)
    f = _two_step(None, check_kind="transient")
    done = hn.analyze(rec, llm=f)
    assert s["status"] == "pending" and s["retry"] is True and s["pros"] == [] and "pv" not in s
    assert s["reason"].startswith("무관 소식 확인 중 AI 일시 오류") and s["tries"] == 0
    assert not any(it.get("body_used") for it in s["items"])
    assert plain["status"] == "ok" and done["transient"] is True and done["analyzed"] == 1
    assert "다음 주기에 다시" in hn._card(s)


def test_check_transient_stops_the_cycle(hn):
    many = [_sport_stock() for _ in range(hn.BATCH_MAX + 1)]
    for i, s in enumerate(many):
        s["key"] = f"KR:{i:06d}"
    f = _two_step(None, check_kind="transient")
    hn.analyze(_rec(*many), llm=f)
    assert sum("검수하는 보조" in p for p in f.calls) == 1             # 다음 묶음으로 가지 않는다


def test_check_prompt_is_part_of_the_rule_signature(hn, monkeypatch):
    """확인 규칙을 고치면 가장 최근 기록을 새 기준으로 다시 정리한다(`requeue_stale` 이 `pv` 를 대조)."""
    a = hn.prompt_sig()
    orig = hn.build_check_prompt
    monkeypatch.setattr(hn, "build_check_prompt", lambda c: orig(c) + "x")
    assert hn.prompt_sig() != a


def test_check_line_ids_map_back_to_the_right_stock_and_side(hn):
    """두 종목·두 쪽에 걸친 후보 — 확인 응답의 id 가 **그 종목의 그 줄** 을 뺀다(위치를 섞지 않는다)."""
    a, b = _sport_stock(), _sport_stock()
    b["key"] = "KR:000150"
    b["name"] = "두산"
    resp = {"stocks": [{"key": k, "relevant": [1, 2],
                        "pros": [{"t": "야구단 후원 계약 체결", "refs": [2]}],
                        "cons": [{"t": "선수 부상", "refs": [1]}]} for k in ("KR:000155", "KR:000150")]}

    def f(prompt):
        if "검수하는 보조" in prompt:
            # c1=a.pros c2=a.cons c3=b.pros c4=b.cons — b 의 Cons 만 뺀다
            return json.dumps({"checks": [{"id": i, "keep": i != "c4"} for i in ("c1", "c2", "c3", "c4")]}), 1, 1, "", ""
        return json.dumps(resp, ensure_ascii=False), 1, 1, "", ""
    hn.analyze(_rec(a, b), llm=f)
    assert a["cons"] and a["pros"] and b["pros"] and b["cons"] == [] and b["offtopic_lines"] == 1


def test_all_lines_dropped_says_why(hn):
    s = _sport_stock()
    s["items"][1]["title"] = "두산 베어스 우승"
    hn.analyze(_rec(s), llm=_two_step(False))
    assert s["pros"] == [] and s["cons"] == []
    assert "AI 가 쓴 줄 중 2줄은 투자와 무관한 소식(스포츠·연예 등)이라고 다시 확인해 뺌" in hn._card(s)


def test_requeue_clears_check_traces(hn):
    s = _sport_stock()
    rec = _rec(s)
    hn.analyze(rec, llm=_two_step(False))
    s["pv"] = "old"
    hn.requeue_stale(rec, body=lambda l: "", pace=0)
    assert s["offtopic_lines"] == 0 and s["check_why"] == "" and s["prev_pc"]["pv"] == "old"


def test_check_transient_after_requeue_keeps_the_old_summary(hn):
    """독립 리뷰 HIGH 재현 — 새 기준으로 다시 정리하던 종목이 확인 일시 오류로 '대기' 로 돌아가도 옛 정리
    (`prev_pc`)는 남아야 한다. 그날 안에 못 끝내면 카드가 그걸 '옛 기준' 으로 보여 준다."""
    s = _sport_stock()
    rec = _rec(s)
    hn.analyze(rec, llm=_two_step(True))
    s["pv"] = "old"
    hn.requeue_stale(rec, body=lambda l: "", pace=0)
    hn.analyze(rec, llm=_two_step(None, check_kind="transient"))
    assert s["status"] == "pending" and s["prev_pc"]["cons"]
    card = hn._card(s, final=True)
    assert "옛 기준 정리를 보여 줍니다" in card and "야구팀 주요 선수 부상" in card
    hn.analyze(rec, llm=_two_step(True))                              # 성공하면 그때 버린다
    assert s["status"] == "ok" and "prev_pc" not in s


def test_check_transient_gives_up_after_the_revert_cap(hn):
    """확인이 계속 일시 오류면 매 주기 정리+확인을 두 번 내지 않게 — 상한 번째엔 줄을 남기고 사유를 적는다."""
    s = _sport_stock()
    rec = _rec(s)
    for i in range(1, hn.MAX_CHECK_REVERTS):
        hn.analyze(rec, llm=_two_step(None, check_kind="transient"))
        assert s["status"] == "pending" and s["check_reverts"] == i
    hn.analyze(rec, llm=_two_step(None, check_kind="transient"))
    assert s["status"] == "ok" and s["cons"] and s["check_reverts"] == hn.MAX_CHECK_REVERTS
    assert f"무관 소식 확인이 {hn.MAX_CHECK_REVERTS}번 일시 오류" in hn._card(s)


def test_check_no_key_is_named_and_not_counted(hn):
    s = _sport_stock()
    rec = _rec(s)
    hn.analyze(rec, llm=_two_step(None, check_kind="no_key"))
    assert s["status"] == "ok" and s["cons"] and s["check_why"] == "AI 키 없음"
    assert rec["llm"]["calls"] == 1 and "checks" not in rec["llm"]


def test_check_cost_is_logged_and_added_to_the_record(hn):
    import bot.usage_tracker as ut
    s = _sport_stock()
    rec = _rec(s)
    hn.analyze(rec, llm=_two_step(False))
    rows = [json.loads(x) for x in ut.USAGE_LOG.read_text().splitlines()]
    assert [(r["prompt_tokens"], r["completion_tokens"]) for r in rows] == [(1000, 200), (300, 20)]
    assert rec["llm"]["pt"] == 1300 and rec["llm"]["ot"] == 220
    assert rec["llm"]["cost_usd"] == round(sum(r["cost_usd"] for r in rows), 6) and rec["llm"]["cost_usd"] > 0


def test_check_splits_into_chunks_and_applies_every_chunk(hn, monkeypatch):
    """후보가 `CHECK_MAX` 를 넘으면 여러 번 묻는다 — 둘째 묶음 판정도 적용돼야 한다(첫 묶음만 보면 놓친다)."""
    monkeypatch.setattr(hn, "CHECK_MAX", 1)
    s = _sport_stock()
    s["items"][1]["title"] = "두산 야구단 후원"
    seen = []

    def f(prompt):
        if "검수하는 보조" in prompt:
            ids = [ln.split("id=")[1].split(" ")[0] for ln in prompt.splitlines() if ln.startswith("### id=")]
            seen.append(ids)
            return json.dumps({"checks": [{"id": i, "keep": i == "c1"} for i in ids]}), 1, 1, "", ""
        return _two_step(True)(prompt)
    hn.analyze(_rec(s), llm=f)
    assert seen == [["c1"], ["c2"]] and s["pros"] and s["cons"] == [] and s["offtopic_lines"] == 1


def test_why_splits_check_calls(hn, capsys):
    rec = {"date": "2026-10-14", "stocks": [], "llm": {"calls": 3, "checks": 1, "pt": 1, "ot": 1,
                                                      "cost_usd": 0.001}}
    hn.save_record(rec)
    hn.why(_t("2026-10-14T09:30"), out=print)
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("   AI "))
    assert line.startswith("   AI 3회(그중 무관 소식 확인 1회) · 입력")     # ④ 의 일자 메타가 대신 만족 못 하게(#75)


# ── 전일대비 금액(사용자 2026-10-14) ──────────────────────────────────────────
# 자산 기록엔 수량이 없어 금액 = 보유 평가금액 × 등락률(추정)이다. 요약은 값을 매길 수 있는 보유 **전부**(ETF
# 포함 — '전체' 손익에서 ETF 를 빼면 합이 작아진다).
def _pf(at=None):
    """`at` = 자산 업로드 시각(KST ISO) — 전일대비의 기준 날을 정한다(사용자 2026-10-14 날짜 기준)."""
    m = _pf0()
    if at:
        m["_saved_ts"] = _t(at).timestamp()
    return m


def _pf0():
    return _model(_h("삼성전자", "005930.KS", "KR", 100_000_000.0),
                  {**_h("삼성전자", "005930", "KR", 50_000_000.0), "금융사": "다른 증권사"},
                  _h("KODEX 200", "069500", "KR", 20_000_000.0, source="etf_meta"),
                  _h("애플", "AAPL", "US", 40_000_000.0, source="alias"),
                  _h("TIGER 어딘가", None, "KR", 7_000_000.0, matched=False, source="etf_brand"),
                  _h("모르는 상품", None, None, 3_000_000.0, matched=False, source=None))


def test_pnl_positions_merge_include_etf_and_count_unpriced(hn):
    pos, skip = hn.pnl_positions(_pf())
    assert [(p["key"], p["value"]) for p in pos] == [
        ("KR:005930", 150_000_000.0), ("US:AAPL", 40_000_000.0), ("KR:069500", 20_000_000.0)]
    assert skip == {"n": 2, "value": 10_000_000.0}


def test_pnl_summary_groups_and_leaves_unknown_out_of_both_sides(hn):
    pos, _ = hn.pnl_positions(_pf())
    pct = {"KR:005930": -2.0, "US:AAPL": 1.0, "KR:069500": None}
    sm = hn.pnl_summary(pos, lambda p: (pct[p["key"]], None, ""))
    # 환율을 안 주면 해외는 주가 등락만으로 세고 '환율 미확인' 으로 센다(2026-10-14 원화 손익 이후 계약, #222)
    assert sm["all"] == {"amt": -3_000_000.0 + 400_000.0, "base": 190_000_000.0, "n": 3, "n_priced": 2, "n_nofx": 1}
    assert sm["KR"] == {"amt": -3_000_000.0, "base": 150_000_000.0, "n": 2, "n_priced": 1, "n_nofx": 0}
    assert sm["XX"] == {"amt": 400_000.0, "base": 40_000_000.0, "n": 1, "n_priced": 1, "n_nofx": 1}


def test_pnl_block_rendered_on_the_page(hn, ws_snaps):
    """서버 값 = 장종료가 누적 등락 · 세 칸(전체·국내·해외) · 추정이라고 밝힌다 · JS 재료(JSON)를 싣는다 · 시장마다
    어느 날 종가까지인지 보이는 줄로 적는다(사용자 2026-10-14 "기준을 장종료가격으로")."""
    # TradingView 하루치는 보통주·DR 만 싣는다 — ETF(069500)는 원천처럼 하루치에 넣지 않고 Yahoo 일봉 종가로(#155)
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    _closes(hn, {"KR:069500": ({"2026-10-07": 100.0, "2026-10-08": 101.0}, "2026-10-08T16:00")})
    rec = _rec({**_stock("KR:005930"), "name": "삼성전자", "value": 150_000_000.0})
    # 기준 = 10-08 08:00 업로드(KST 날짜 10-08 < 오늘 10-12) — 그 뒤 마감한 KR 세션은 10-08 하나(10-09 한글날)
    html = hn.render_page([rec], {}, _t("2026-10-12T08:00"), gen="GEN", portfolio=_pf("2026-10-08T08:00"))
    kr = html.split("id='hn-pnl-KR'>")[1].split("</div>")[0]
    assert kr == "<span class='hn-dn'>-280만원 (-1.65%)</span>"          # (-300만 + 20만) ÷ 1.7억
    assert html.split("id='hn-pnl-KR-sub'>")[1].split("</div>")[0] == "종가 기준 2/2종목"
    assert html.split("id='hn-pnl-XX'>")[1].split("</div>")[0] == "—"     # 미국 하루치 없음 → 모름
    assert html.split("id='hn-pnl-XX-sub'>")[1].split("</div>")[0] == "종가 기준 0/1종목"
    assert "티커를 못 찾은 상품 2개(1,000만원)는 뺌" in html and "10-08 08:00 업로드" in html
    # 미국은 한 종목도 못 셌다 — 날짜를 적으면 그날 종가를 반영한 척하고, 빼면 시장이 조용히 사라진다 → '—'
    assert html.split("id='hn-pnl-asof'>")[1].split("</div>")[0] == "반영한 마지막 종가: 한국 10-08 · 미국 —"
    el = html.split("id='hn-pnl-data'")[1]
    assert el.startswith(" data-basis='종가 기준'>")                       # JS 가 서버 칸과 같은 이름을 쓴다(#38)
    data = {d["k"]: d for d in json.loads(el.split(">", 1)[1].split("</script>")[0])}
    assert data["KR:005930"] == {"k": "KR:005930", "v": 150000000, "g": "KR", "p": pytest.approx(-2.0)}
    assert data["US:AAPL"]["p"] is None and data["US:AAPL"]["c"] == "USD"
    # 종목 시세는 묻지 않는다 — 카드 실시간 칸도, 실시간 스크립트도 없다
    from bot.live_pct import LIVE_JS
    assert LIVE_JS not in html and "js-live-pct" not in html and "data-t=" not in html
    assert "api/live_pct?t=" not in html and "fetch('api/live_pct?fx=1'" in html


def test_pnl_block_absent_without_portfolio_and_failure_does_not_break_page(hn, monkeypatch):
    rec = _rec(_stock("KR:A"))
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio={})
    assert "id='hn-pnl-data'" not in html and "hn-grid" in html
    monkeypatch.setattr(hn, "pnl_summary", lambda *a: 1 / 0)
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio=_pf())
    assert "id='hn-pnl-data'" not in html and "hn-grid" in html            # 곁들이가 본체를 지우지 않는다


def test_pnl_json_cannot_close_the_script(hn):
    pos = [{"key": "</script><b>", "market": "KR", "ticker": "1", "sym": "1", "name": "x", "value": 1.0}]
    out = hn._pnl_html(pos, {"n": 0}, lambda p: (1.0, None, ""), fx={"</script>": 1.0})
    assert out.count("</script>") == 2                                    # 재료 둘(종목 · 환율) 각자 하나씩


_PNL_HARNESS = r"""
var els={}, fetched=[];
function mk(id,txt){var e={id:id,textContent:txt||'',className:'',children:[],attrs:{},
  appendChild:function(c){this.children.push(c); this.textContent=c.textContent;},
  getAttribute:function(k){return this.attrs[k]===undefined?null:this.attrs[k];},
  setAttribute:function(k,v){this.attrs[k]=v;}};
  els[id]=e; return e;}
var data=mk('hn-pnl-data',DATA); data.attrs['data-basis']=BASIS;
if(typeof FX!=='undefined'){mk('hn-fx-data',FX);}
['all','KR','XX'].forEach(function(g){mk('hn-pnl-'+g,'SERVER'); mk('hn-pnl-'+g+'-sub','SERVER');});
var cell=null; if(typeof CELL!=='undefined'){cell={textContent:'SERVER',attrs:CELL,
  className:(typeof CLS!=='undefined')?CLS:'hn-chg js-hn-amt',
  getAttribute:function(k){return this.attrs[k]===undefined?null:this.attrs[k];},
  setAttribute:function(k,v){this.attrs[k]=v;}};}
global.window=global; global.document={readyState:'complete',getElementById:function(i){return els[i]||null;},
  createElement:function(){return {textContent:'',className:''};},
  // 렌더된 카드의 클래스가 JS 선택자와 맞을 때만 고른다 — 이름이 갈리면 화면이 그 카드를 영영 안 칠한다(#20)
  querySelectorAll:function(sel){return (sel==='.js-hn-amt'&&cell&&(' '+cell.className+' ').indexOf(' js-hn-amt ')>=0)?[cell]:[];},
  addEventListener:function(){}};
// RUN = 타이머를 실제로 몇 번까지 돌릴지(없으면 기록만) — 재시도 상한처럼 여러 바퀴를 재야 할 때
var delays=[]; global.setTimeout=function(f,d){delays.push(d); if(typeof RUN!=='undefined'&&delays.length<RUN) f();};
global.clearTimeout=function(){};
// RESP 가 배열이면 차례로(끝은 마지막 것 반복) · 'ERR' 는 연결 실패(대시보드 재시작 중) — 거부된 약속
global.fetch=function(u){fetched.push(u);
  var r=Array.isArray(RESP)?RESP[Math.min(fetched.length-1,RESP.length-1)]:RESP;
  if(r==='ERR') return Promise.reject(new Error('down'));
  return Promise.resolve({ok:true,json:function(){return Promise.resolve(r);}});};
"""


def _run_pnl_js(hn, data, resp, fx=None, cell=None, cls=None, basis=None, run=None):
    """`_PNL_JS` 를 node 로 — `resp` = `api/live_pct?fx=1` 응답. 값 칸의 처음 글자는 'SERVER'(서버가 그린 값) —
    JS 가 안 건드리면 그대로 남는다."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    src = (f"var DATA={json.dumps(json.dumps(data))}; var RESP={json.dumps(resp)};"
           f"var BASIS={json.dumps(hn.PNL_BASIS if basis is None else basis)};"
           + (f"var RUN={int(run)};" if run else "")
           + (f"var FX={json.dumps(json.dumps(fx))};" if fx is not None else "")
           + (f"var CELL={json.dumps(cell)};" if cell is not None else "")
           + (f"var CLS={json.dumps(cls)};" if cls is not None else "")
           + _PNL_HARNESS
           + hn._PNL_JS
           # 이벤트 루프가 다 비면(약속 사슬이 전부 끝나면) 찍는다 — 정해진 틱 수를 기다리면 여러 바퀴를 못 잰다
           + "\nprocess.on('exit',function(){console.log(JSON.stringify("
             "{all:els['hn-pnl-all'].textContent,allsub:els['hn-pnl-all-sub'].textContent,"
             "kr:els['hn-pnl-KR'].textContent,xx:els['hn-pnl-XX'].textContent,xxsub:els['hn-pnl-XX-sub'].textContent,"
             "cell:cell?cell.textContent:'',title:cell?(cell.attrs.title||''):'',urls:fetched,delays:delays}));});")
    r = subprocess.run([node, "-e", src], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_pnl_js_redraws_server_values_with_fx_and_counts_unknown(hn):
    """옛 계약(실시간이 온 종목은 실시간, 못 받으면 전날)은 사용자 2026-10-14 "기준을 장종료가격으로" 로 바뀌었다
    (#222) — JS 는 서버가 구운 장종료가 등락(`p`)만 쓰고 환율을 받아 다시 센다. 등락이 없으면 세지 않는다(서버
    `pnl_summary` 와 같은 규칙)."""
    data = [{"k": "KR:005930", "v": 150000000, "g": "KR", "p": -2.0},
            {"k": "KR:069500", "v": 20000000, "g": "KR", "p": 1.0},
            {"k": "US:AAPL", "v": 40000000, "g": "XX", "p": None, "c": "USD"},
            {"k": "JP:7203", "v": 10000000, "g": "XX", "p": 0.0, "c": "JPY"}]
    o = _run_pnl_js(hn, data, {"q": {}, "pending": 0, "fx": {"USD": {"pct": 1.0, "age": 1}, "JPY": {"pct": 0.0}}})
    assert o["kr"] == "-280만원 (-1.65%)"                                   # -300만 + 20만
    assert o["all"] == "-280만원 (-1.56%)" and o["allsub"] == "종가 기준 3/4종목"   # ÷ 1.8억(애플 빼고)
    assert o["xx"] == "0원 (0.00%)" and o["xxsub"] == "종가 기준 1/2종목"


def test_pnl_js_amount_format_matches_python(hn):
    for v, pct in ((12_345_678_900, 1.0), (-3_000_000_000, 1.0), (55_000, -10.0), (500, 2.0)):
        o = _run_pnl_js(hn, [{"k": "US:A", "v": abs(v), "g": "XX", "p": pct * (1 if v > 0 else -1), "c": "USD"}],
                        {"q": {}, "pending": 0, "fx": {"USD": {"pct": 0.0, "age": 1}}})
        a = abs(v) * pct * (1 if v > 0 else -1) / 100
        assert o["all"].split(" (")[0] == hn._signed_krw(a), (v, o)


def test_pnl_js_asks_only_fx_once_a_minute_and_never_tickers(hn):
    """옛 계약(종목 티커를 400개씩 나눠 1분마다 묻는다)은 사용자 2026-10-14 로 바뀌었다(#222) — 종목 시세는 묻지
    않는다(열어 둔 화면 하나가 분당 수백 건이었다). 해외 보유가 있으면 환율 하나만 1분마다 묻는다."""
    data = [{"k": f"KR:{i:06d}", "v": 1000, "g": "KR", "p": 1.0} for i in range(650)]
    data.append({"k": "US:AAPL", "v": 1000, "g": "XX", "p": 1.0, "c": "USD"})
    o = _run_pnl_js(hn, data, {"q": {}, "pending": 3, "fx": {"USD": {"pct": 0.0}}})
    assert o["urls"] == ["api/live_pct?fx=1"] and o["delays"] == [60000]   # 종목 pending 이어도 5초로 당기지 않는다


def test_pnl_js_domestic_only_page_asks_nothing(hn):
    """국내만 들고 있으면 환율도 필요 없다 — 한 건도 묻지 않고 서버가 그린 값이 그대로다."""
    data = [{"k": f"KR:{t_}", "v": 1000000, "g": "KR", "p": 1.0} for t_ in ("A", "B", "C")]
    o = _run_pnl_js(hn, data, {"q": {}, "pending": 0, "fx": {"USD": {"pct": 1.0}}})
    assert o["urls"] == [] and o["delays"] == [] and o["kr"] == "SERVER" and o["allsub"] == "SERVER"


def test_pnl_positions_skip_unmatched_even_with_a_ticker(hn):
    pos, skip = hn.pnl_positions(_model(_h("오매칭", "123456", "KR", 5.0, matched=False),
                                        _h("정상", "005930", "KR", 7.0)))
    assert [p["key"] for p in pos] == ["KR:005930"] and skip == {"n": 1, "value": 5.0}


def test_pnl_positions_tolerate_nan_value(hn):
    pos, _ = hn.pnl_positions(_model(_h("이상값", "005930", "KR", float("nan"))))
    assert pos[0]["value"] == 0.0


def test_pnl_zero_base_renders_dash_like_the_js(hn):
    pos = [{"key": "KR:1", "market": "KR", "ticker": "000001", "sym": "000001", "name": "x", "value": 0.0}]
    out = hn._pnl_html(pos, {"n": 0}, lambda p: (1.0, None, ""))
    assert "id='hn-pnl-KR'>—</div>" in out


def test_signed_krw_matches_js_rounding_and_drops_sign_at_zero(hn):
    cases = (0.4, -0.4, 15_000, 25_000, -35_000, 149_999_999, 150_000_000, 12_345_678_900, float("nan"))
    import shutil as _sh, subprocess as _sp
    if not _sh.which("node"):
        pytest.skip("node 없음")
    src = hn._PNL_JS + "console.log(JSON.stringify(%s.map(function(a){return window.hnAmt(a);})));" % json.dumps(
        [c if c == c else "NaN" for c in cases]).replace('"NaN"', "NaN")
    r = _sp.run([_sh.which("node"), "-e", "global.window=global;global.document={readyState:'complete',"
                 "getElementById:function(){return null;},querySelectorAll:function(){return [];},"
                 "addEventListener:function(){}};" + src],
                capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [hn._signed_krw(c) for c in cases]
    assert hn._signed_krw(0.4) == "0원" and hn._signed_krw(15_000) == "+2만원" and hn._signed_krw(25_000) == "+3만원"
    assert hn._signed_krw(150_000_000) == "+1.5억원" and hn._signed_krw(float("nan")) == "0원"


def test_latest_card_shows_close_based_amount_from_current_portfolio(hn, ws_snaps):
    """최신일 카드 = 요약과 같은 장종료가 누적 · 금액은 요약과 같은 지금의 자산 기록 값(#33) · 툴팁이 무엇의 값인지
    (기준 시각 · 반영한 마지막 종가)를 말한다 · 실시간 칸이 아니다(사용자 2026-10-14)."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    rec = _rec({**_stock("KR:005930"), "name": "삼성전자", "value": 1.0})
    html = hn.render_page([rec], {}, _t("2026-10-12T08:00"), gen="GEN", portfolio=_pf("2026-10-08T08:00"))
    card = html.split("data-hncard='1'")[1]
    head = card.split("class='hn-chg")[1].split("</span>")[0]
    assert "(-2.00% · -300만원)" in head and "보유 1.5억원" in card
    assert "장종료가 누적 등락" in head and "2026-10-08 종가까지" in head and "10-08 08:00(KST)" in head
    assert "js-live-pct" not in card and "js-hn-amt" not in head            # 국내는 환율이 없어 다시 그릴 것이 없다


def test_overseas_cell_says_it_is_krw_with_fx(hn, ws_snaps):
    """옛 계약(해외 = 현지통화 · 환율 제외)은 사용자 2026-10-14 "환율 반영 원화손익으로" 로 뒤집혔다(#222) — 칸 이름이
    원화·환율 반영을 말하고, 옛 '빠져 있습니다' 문구는 남지 않는다."""
    html = hn.render_page([_rec(_stock("KR:A"))], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio=_pf(), fx={})
    assert "전일대비 · 해외(원화 · 환율 반영)" in html and "환율 제외" not in html
    assert "그날 환율 변동은 빠져 있습니다" not in html


def test_krw_pct_compounds_and_falls_back_without_fx(hn):
    assert hn.krw_pct(-6.69, None) == -6.69
    assert hn.krw_pct(0.0, 0.5) == pytest.approx(0.5)
    assert hn.krw_pct(10.0, 10.0) == pytest.approx(21.0)                  # 곱이지 합이 아니다
    assert hn.krw_pct(-1.55, 0.5) == pytest.approx(((1 - 0.0155) * 1.005 - 1) * 100)


def test_currency_of_market(hn):
    assert hn.currency_of("KR") is None and hn.currency_of("US") == "USD" and hn.currency_of("JP") == "JPY"
    assert hn.currency_of("TW") == "TWD" and hn.currency_of("HK") == "HKD" and hn.currency_of("CN_A") == "CNY"
    assert hn.currency_of("ZZ") is None


def test_pnl_summary_applies_fx_only_to_overseas(hn):
    pos, _ = hn.pnl_positions(_pf())
    pct = {"KR:005930": -2.0, "US:AAPL": 1.0, "KR:069500": None}
    sm = hn.pnl_summary(pos, lambda p: (pct[p["key"]], None, ""), {"USD": 2.0, "KRW": 50.0})
    xx = 40_000_000.0 * ((1.01 * 1.02) - 1)
    assert sm["XX"]["amt"] == pytest.approx(xx) and sm["XX"]["n_nofx"] == 0
    assert sm["KR"]["amt"] == pytest.approx(-3_000_000.0)                 # 국내엔 환율이 안 붙는다
    assert sm["all"]["amt"] == pytest.approx(xx - 3_000_000.0)


def test_page_summary_and_card_use_fx(hn, ws_snaps):
    """서버가 그린 해외 칸·해외 카드 금액이 원화 기준 · JS 재료에 통화(`c`)와 환율(`hn-fx-data`)이 실린다(#20) ·
    해외 카드는 화면이 환율을 받아 다시 그릴 재료(`js-hn-amt`)를 싣는다."""
    ws_snaps("US", "2026-10-09", [("NASDAQ:AAPL", 1.0)])
    rec = _rec({**_stock("US:AAPL"), "market": "US", "ticker": "AAPL", "name": "애플", "value": 1.0})
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio=_pf("2026-10-09T08:00"),
                          fx={"USD": 2.0})
    xx = html.split("id='hn-pnl-XX'>")[1].split("</div>")[0]
    assert xx == "<span class='hn-up'>+121만원 (+3.02%)</span>", xx        # 4천만 × 3.02%
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    aapl = [d for d in data if d["k"] == "US:AAPL"][0]
    assert aapl["v"] == 40000000 and aapl["p"] == pytest.approx(1.0) and aapl["c"] == "USD"
    assert all("c" not in d for d in data if d["g"] == "KR")
    assert json.loads(html.split("id='hn-fx-data'>")[1].split("</script>")[0]) == {"USD": 2.0}
    card = html.split("data-hncard='1'")[1]
    head = card.split("class='hn-chg")[1].split("</span>")[0]
    assert head.startswith(" hn-up js-hn-amt' ")                             # 토큰 그대로 — 'js-hn-amtX' 면 JS 가 못 고른다(#75)
    assert "data-c='USD'" in head and "data-p='1.0'" in head and "data-v='40000000'" in head
    assert "(+1.00% · 원화 +121만원)" in head and "원화 기준(주가 +1.00% · 원/USD +2.00%)" in head


def test_page_counts_missing_fx_instead_of_inventing_it(hn, ws_snaps):
    ws_snaps("US", "2026-10-09", [("NASDAQ:AAPL", 1.0)])
    rec = _rec({**_stock("US:AAPL"), "market": "US", "ticker": "AAPL", "name": "애플", "value": 1.0})
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio=_pf("2026-10-09T08:00"),
                          fx={"JPY": 9.0})
    assert html.split("id='hn-pnl-XX'>")[1].split("</div>")[0] == "<span class='hn-up'>+40만원 (+1.00%)</span>"
    assert "환율 미확인 1" in html.split("id='hn-pnl-XX-sub'>")[1].split("</div>")[0]
    assert "USD 환율 미확인 — 현지통화 등락만" in html.split("data-hncard='1'")[1]


def test_page_render_never_fetches_fx_and_says_it_waits(hn, ws_snaps, monkeypatch):
    """서버는 환율을 굽지 않는다 — 장 중 계속 바뀌는 값을 구우면 15분 재생성마다 내용이 달라져 '내용이 같으면 안
    쓴다'(#442)가 무너진다(독립 리뷰 2026-10-14 High). 화면이 열자마자 받으므로 서버 칸은 '환율 대기'(미확인 아님)."""
    import bot.live_pct as lp
    called = []
    monkeypatch.setattr(lp, "fx_changes", lambda: called.append(1) or {"USD": -1.0})
    ws_snaps("US", "2026-10-09", [("NASDAQ:AAPL", 1.0)])
    rec = _rec({**_stock("US:AAPL"), "market": "US", "ticker": "AAPL", "name": "애플", "value": 1.0})
    a = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio=_pf("2026-10-09T08:00"))
    b = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio=_pf("2026-10-09T08:00"))
    assert called == [] and a == b
    assert "id='hn-fx-data'" not in a
    assert "환율 대기 1" in a.split("id='hn-pnl-XX-sub'>")[1].split("</div>")[0] and "환율 미확인" not in a.split(
        "id='hn-pnl-XX-sub'>")[1].split("</div>")[0]
    card = a.split("data-hncard='1'")[1]
    assert "USD 환율은 화면이 곧 받습니다" in card and "(+1.00% · +40만원)" in card   # 원화 표식 없음(환율 미반영)


def test_unknown_currency_market_is_not_counted_as_missing_fx_like_the_js(hn):
    pos = [{"key": "ZZ:X", "market": "ZZ", "ticker": "X", "sym": "X", "name": "x", "value": 100.0}]
    sm = hn.pnl_summary(pos, lambda p: (1.0, None, ""), {})
    assert sm["XX"]["n_nofx"] == 0 and sm["XX"]["amt"] == pytest.approx(1.0)


def test_past_day_card_never_applies_todays_fx(hn):
    """지난 날 카드에 오늘 환율을 곱하면 다른 날의 등락이 된다 — 현지통화 등락만, 툴팁이 그렇게 말하고, 화면이
    환율로 다시 그리는 재료도 싣지 않는다."""
    out = hn._prev_html((1.0, "2026-10-01", ""), 40_000_000.0, ccy="USD", fx={"USD": 2.0})
    assert "+40만원" in out and "그날 기준 하루 등락이라 현지통화 등락만(환율 변동 미반영)" in out
    assert "data-c" not in out and "js-hn-amt" not in out and "원화" not in out


def test_check_revert_cap_is_three(hn):
    assert hn.MAX_CHECK_REVERTS == 3            # 리터럴로 — 상수로 재면 99 도 1 도 통과한다(#66)


def test_check_reverts_reset_after_a_clean_check_and_on_requeue(hn):
    s = _sport_stock()
    rec = _rec(s)
    hn.analyze(rec, llm=_two_step(None, check_kind="transient"))
    assert s["check_reverts"] == 1
    hn.analyze(rec, llm=_two_step(True))
    assert "check_reverts" not in s                                    # 확인을 마쳤다 — 처음부터 센다
    s["check_reverts"] = 2
    s["pv"] = "old"
    hn.requeue_stale(rec, body=lambda l: "", pace=0)
    assert "check_reverts" not in s


def test_transient_in_a_later_chunk_keeps_earlier_drops_for_capped_stocks(hn, monkeypatch):
    monkeypatch.setattr(hn, "CHECK_MAX", 1)
    s = _sport_stock()
    s["items"][1]["title"] = "두산 야구단 후원"
    s["check_reverts"] = hn.MAX_CHECK_REVERTS - 1                      # 이번이 상한 — 되돌리지 않는다
    n = []

    def f(prompt):
        if "검수하는 보조" in prompt:
            n.append(1)
            if len(n) == 2:
                return None, 0, 0, "AI 일시 오류(503)", "transient"
            return json.dumps({"checks": [{"id": "c1", "keep": False}]}), 1, 1, "", ""
        return _two_step(True)(prompt)
    hn.analyze(_rec(s), llm=f)
    assert s["status"] == "ok" and s["pros"] == [] and s["offtopic_lines"] == 1   # c1(Pros) 판정은 적용
    assert s["cons"] and "일시 오류" in s["check_why"]


def test_pnl_js_krw_with_fetched_fx_redraws_summary_and_card(hn):
    """JS 도 파이썬 `krw_pct` 와 같은 식 — 받은 환율로 요약과 해외 카드(`js-hn-amt`)를 같이 다시 그린다. 등락은
    서버가 구운 장종료가(`p`·`data-p`) 그대로다."""
    data = [{"k": "US:AAPL", "v": 40000000, "g": "XX", "p": 1.0, "c": "USD"},
            {"k": "KR:005930", "v": 150000000, "g": "KR", "p": -2.0}]
    cell = {"data-p": "1.0", "data-v": "40000000", "data-c": "USD", "data-tip": "TIP"}
    o = _run_pnl_js(hn, data, {"q": {}, "pending": 0, "fx": {"USD": {"pct": 2.0, "age": 3}}}, cell=cell)
    assert o["xx"] == "+121만원 (+3.02%)" and o["xxsub"] == "종가 기준 1/1종목"
    assert o["kr"] == "-300만원 (-2.00%)"
    assert o["cell"] == "(+1.00% · 원화 +121만원)"
    assert o["title"] == "TIP · 원화 기준(주가 +1.00% · 원/USD 오늘 하루 +2.00%)"
    assert o["urls"] == ["api/live_pct?fx=1"]


def test_pnl_js_counts_missing_fx(hn):
    data = [{"k": "US:AAPL", "v": 40000000, "g": "XX", "p": 1.0, "c": "USD"}]
    cell = {"data-p": "1.0", "data-v": "40000000", "data-c": "USD", "data-tip": "TIP"}
    o = _run_pnl_js(hn, data, {"q": {}, "pending": 0, "fx": {"JPY": {"pct": 1.0}}}, cell=cell)   # 받았는데 USD 가 없다
    assert o["xx"] == "+40만원 (+1.00%)" and o["xxsub"] == "종가 기준 1/1종목 · 환율 미확인 1"
    assert o["title"] == "TIP · USD 환율 미확인 — 현지통화 등락만" and o["cell"] == "(+1.00% · +40만원)"


def test_pnl_js_response_replaces_fx_wholesale(hn):
    """서버가 낡아서 버린 통화(`fx: {}`)는 화면에서도 사라진다 — 받은 통화만 덮어쓰면 마지막 환율이 영원히 곱해진다
    (독립 리뷰 2026-10-14 Medium). 환율을 안 실은 응답(오류 등)은 서버가 그린 값을 그대로 둔다."""
    data = [{"k": "US:AAPL", "v": 40000000, "g": "XX", "p": 1.0, "c": "USD"}]
    cell = {"data-p": "1.0", "data-v": "40000000", "data-c": "USD"}
    o = _run_pnl_js(hn, data, {"q": {}, "pending": 0, "fx": {}}, fx={"USD": 2.0}, cell=cell)
    assert o["xx"] == "+40만원 (+1.00%)" and o["xxsub"].endswith("환율 미확인 1")
    assert o["cell"] == "(+1.00% · +40만원)"                              # 원화 표식도 빠진다
    o = _run_pnl_js(hn, data, {"ok": False}, fx={"USD": 2.0}, cell=cell)
    assert o["xx"] == "SERVER" and o["cell"] == "SERVER" and o["delays"] == [60000]   # 다음 분에 다시 묻는다


# ── 업종(사용자 2026-10-14 "종목코드에 따른 업종을 같이 명시하고 그거에 맞게 뉴스를") ─────────────────────
@pytest.fixture
def ws_ind(tmp_path, monkeypatch):
    """업종·한국어 라벨을 실은 하루치 — 원천 형식 그대로(`ROW` 순서 · labels.ind 영문→한국어)."""
    import gzip

    import bot.world_sectors as ws
    import bot.holdings_news as hnm
    monkeypatch.setattr(ws, "_SNAP", tmp_path / "snapi")
    monkeypatch.setattr(hnm, "_PREV_CACHE", {})
    ws.cache_clear()

    def put(market, d, rows, labels=None):
        p = ws.snap_path(market, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        full = [[sym, sym, "Electronic Technology", ind, chg, 1.0, 1.0, None, 1.0, 0] for sym, ind, chg in rows]
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            json.dump({"rows": full, "labels": {"sec": {}, "ind": labels or {}}}, fh)
        ws.cache_clear()
    yield put
    ws.cache_clear()


def test_industry_lookup_uses_source_korean_label_and_falls_back(hn, ws_ind):
    ws_ind("KR", "2026-10-08", [("KRX:079370", "Electronic Production Equipment", 1.0),
                                ("KRX:005930", "Telecommunications Equipment", -1.0)],
           {"Electronic Production Equipment": "전자 생산 장비"})
    ind = hn.industry_lookup("2026-10-12")          # 10-09(한글날)·주말 건너 10-08 하루치
    assert ind({"market": "KR", "ticker": "079370"}) == "전자 생산 장비"
    assert ind({"market": "KR", "ticker": "005930"}) == "Telecommunications Equipment"   # 라벨 없으면 원천 영문
    assert ind({"market": "KR", "ticker": "000000"}) == ""
    assert ind({"market": "HK", "ticker": "0700"}) == ""                                 # 하루치가 없는 시장
    # 전날 하루치가 없어도 몇 거래일 앞까지 본다(업종은 거의 안 바뀐다)
    assert hn.industry_lookup("2026-10-14")({"market": "KR", "ticker": "079370"}) == "전자 생산 장비"


def test_prompt_carries_industry_and_the_rule(hn):
    s = {**_stock("KR:079370"), "name": "제우스", "ind": "전자 생산 장비"}
    p = hn.build_prompt([s])
    assert "### key=KR:079370 · 제우스(079370, 한국) · 업종: 전자 생산 장비" in p
    assert "**이름만 같은** 다른 회사·남의 게임" in p
    assert "업종:" not in hn.build_prompt([_stock("KR:A")]).split("종목:")[1]          # 모르면 지어내지 않는다


def test_analyze_attaches_industry_before_asking(hn, ws_ind):
    ws_ind("KR", "2026-10-08", [("KRX:079370", "Electronic Production Equipment", 1.0)],
           {"Electronic Production Equipment": "전자 생산 장비"})
    s = {**_stock("KR:079370"), "name": "제우스"}
    rec = _rec(s)
    f = _llm(lambda p: json.dumps({"stocks": [{"key": "KR:079370", "relevant": [1], "pros": [], "cons": []}]}))
    hn.analyze(rec, llm=f)
    assert s["ind"] == "전자 생산 장비" and "업종: 전자 생산 장비" in f.calls[0]


def test_attach_industry_failure_leaves_it_blank(hn, monkeypatch):
    monkeypatch.setattr(hn, "industry_lookup", lambda d: (lambda s: 1 / 0))
    s = _stock("KR:A")
    hn._attach_industry(_rec(s), [s])
    assert "ind" not in s


def test_game_line_is_a_check_candidate_and_check_prompt_has_industry(hn):
    s = {**_stock("KR:079370", 1), "name": "제우스", "ind": "전자 생산 장비",
         "cons": [{"t": "구글 플레이 매출 순위 2위를 기록했다.", "refs": [1]}], "pros": []}
    assert hn.offtopic_candidates(s) == [("cons", 0)]
    p = hn.build_check_prompt([("c1", s, "cons", 0)])
    assert "제우스(079370) · 업종: 전자 생산 장비 · Cons" in p and "**이름만 같은**" in p
    # 업종은 동명 판별의 단서일 뿐 사업 범위가 아니다 — 같은 회사의 다른 사업부는 남긴다(리뷰 2026-10-14 High)
    assert "keep=true: 같은 회사의 다른 사업부·자회사 소식(업종과 달라도)" in p
    assert "같은 회사의\n   다른 사업부·자회사 소식은 업종과 달라도 관련 있다" in hn.build_prompt([])


def test_card_header_shows_industry_even_for_old_records(hn, ws_ind):
    """업종을 적어 두지 않은 기록도 렌더 때 채운다(#270) · 적어 둔 값이 있으면 그것."""
    ws_ind("KR", "2026-10-08", [("KRX:079370", "Electronic Production Equipment", 1.0)],
           {"Electronic Production Equipment": "전자 생산 장비"})
    rec = _rec({**_stock("KR:079370"), "name": "제우스"})
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio={})
    card = html.split("data-hncard='1'")[1]
    assert "079370 · <span class='hn-ind'" in card and ">전자 생산 장비</span> · 보유" in card
    # 렌더 때 읽은 값이 이긴다(그날 영문으로 적힌 값도 한국어 라벨을 따라온다) · 하루치가 없으면 적어 둔 값
    rec = _rec({**_stock("KR:079370"), "name": "제우스", "ind": "Electronic Production Equipment"})
    card = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio={}).split("data-hncard='1'")[1]
    assert ">전자 생산 장비</span>" in card
    rec = _rec({**_stock("KR:999999"), "name": "x", "ind": "적어 둔 업종"})
    card = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio={}).split("data-hncard='1'")[1]
    assert ">적어 둔 업종</span>" in card


@pytest.mark.parametrize("txt,hit", [("HBM 게임체인저로 부상", False), ("치킨게임 심화", False), ("제로섬 게임", False),
                                     ("SCOTT 대표 선임", False), ("신작 게임 출시", True), ("넷플릭스 OTT", True),
                                     ("ott 시장", True), ("신작 MMORPG", True), ("구글플레이 매출 2위", True)])
def test_offtopic_hint_game_words_skip_common_economic_idioms(hn, txt, hit):
    assert bool(hn.OFFTOPIC_HINT.search(txt)) is hit


def test_broken_industry_field_keeps_the_change_table(hn, tmp_path, monkeypatch):
    """업종 칸이 깨져도(리스트·라벨이 dict 아님) 전날 등락은 산다 — 곁들이가 본체를 지우지 않는다(#315)."""
    import gzip

    import bot.world_sectors as ws
    monkeypatch.setattr(ws, "_SNAP", tmp_path / "snapb")
    monkeypatch.setattr(hn, "_PREV_CACHE", {})
    ws.cache_clear()
    p = ws.snap_path("KR", "2026-10-08")
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [["KRX:000001", "a", None, ["weird"], 1.5, 1, 1, None, 1, 0],
            ["KRX:000002", "b", None, "Semis", -2.0, 1, 1, None, 1, 0]]
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        json.dump({"rows": rows, "labels": {"ind": ["not", "a", "dict"]}}, fh)
    ws.cache_clear()
    look = hn.prev_changes("2026-10-12")
    assert look({"market": "KR", "ticker": "000001"})[0] == 1.5
    ind = hn.industry_lookup("2026-10-12")
    assert ind({"market": "KR", "ticker": "000001"}) == "" and ind({"market": "KR", "ticker": "000002"}) == "Semis"


def test_industry_lookback_merges_per_stock_nearest_first(hn, ws_ind):
    """가까운 날 하루치에 없는 종목(거래정지 등)은 더 앞 날에서 찾고, 둘 다 있으면 가까운 날이 이긴다."""
    ws_ind("KR", "2026-10-08", [("KRX:000001", "New", 1.0)])
    ws_ind("KR", "2026-10-07", [("KRX:000001", "Old", 1.0), ("KRX:000002", "Halted", 1.0)])
    ind = hn.industry_lookup("2026-10-12")
    assert ind({"market": "KR", "ticker": "000001"}) == "New" and ind({"market": "KR", "ticker": "000002"}) == "Halted"


def test_industry_us_key_and_escape(hn, ws_ind):
    ws_ind("US", "2026-10-09", [("NYSE:BRK.B", "<b>Insurance</b>", 1.0)])
    assert hn.industry_lookup("2026-10-12")({"market": "US", "ticker": "BRK-B", "sym": "BRK-B"}) == "<b>Insurance</b>"
    assert "&lt;b&gt;Insurance&lt;/b&gt;" in hn._ind_html("<b>Insurance</b>") and "<b>" not in hn._ind_html("<b>x</b>")


def test_industry_reaches_the_offtopic_check_call(hn, ws_ind):
    """E2E — `analyze` 가 붙인 업종이 실제 확인 호출에 실린다(헬퍼만 재면 배선을 못 본다, #20)."""
    ws_ind("KR", "2026-10-08", [("KRX:000155", "Electronic Production Equipment", 1.0)],
           {"Electronic Production Equipment": "전자 생산 장비"})
    s = _sport_stock()
    s.pop("ind", None)
    rec = _rec(s)
    f = _two_step(False)
    hn.analyze(rec, llm=f)
    chk = [c for c in f.calls if "검수하는 보조" in c]
    assert chk and "업종: 전자 생산 장비" in chk[0]


def test_non_string_label_falls_back_to_source_english(hn, ws_ind):
    ws_ind("KR", "2026-10-08", [("KRX:000001", "Semis", 1.0)], {"Semis": 5})
    assert hn.industry_lookup("2026-10-12")({"market": "KR", "ticker": "000001"}) == "Semis"


# ── 낮에 새로 생긴 보유(사용자 2026-10-14 "자산 기록이 바뀌었을 때 새로 생긴 종목만 바로 수집해 오늘 카드에") ──
def test_tick_adds_only_new_holdings_to_todays_record_and_analyzes_them(hn, portfolio):
    """아침에 모은 기록에 없는 보유만 그 주기에 모아 붙이고 같은 주기에 정리한다 · 다시 불러도 또 모으지 않는다 ·
    판 종목은 오늘 기록에서 지우지 않는다."""
    # 하이닉스 소식은 아침 창(10-11 07:15~) 안이지만 '지금−24h'(10-11 13:30~) 밖 — 아침 창을 써야 실린다
    f = _fetcher({"KR:000660": ([_it("SK하이닉스 소식", "2026-10-11T10:00:00+09:00", link="https://p/h")],
                                "naver", "")},
                 ([_it("삼성전자 소식", "2026-10-12T06:00:00+09:00", link="https://p/1")], "naver", ""))
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert len(f.calls) == 2
    portfolio["m"] = _model(_h("삼성전자", "005930", "KR"), _h("SK하이닉스", "000660", "KR"))   # NVDA 팜 · 하이닉스 삼
    res = hn.tick(_t("2026-10-12T13:30"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert f.calls[2:] == ["KR:000660"]                         # 새 보유만 모은다
    rec = hn.load_records()[0]
    st = {s["key"]: s for s in rec["stocks"]}
    assert set(st) == {"KR:005930", "US:NVDA", "KR:000660"}                         # 판 종목은 남는다
    assert st["KR:000660"]["status"] == "ok" and st["KR:000660"]["added_at"] == "2026-10-12T13:30+09:00"
    assert "added_at" not in st["KR:005930"] and res["analyzed"] == 1
    assert rec["universe"]["n"] == 3 and rec["added"] == [{"at": "2026-10-12T13:30+09:00", "n": 1}]
    hn.tick(_t("2026-10-12T13:45"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert len(f.calls) == 3                                                        # 다시 모으지 않는다
    card = hn.render_page(hn.load_records(), {}, _t("2026-10-12T14:00"), gen="GEN", portfolio={})
    one = [c for c in card.split("data-hncard='1'")[1:] if "000660" in c][0]
    assert "자산 기록 변경 뒤 13:30 추가" in one


def test_new_holdings_before_run_time_wait_for_the_morning_collect(hn, portfolio):
    """오늘 기록이 아직 없으면(집계 전) 붙일 곳이 없다 — 아침 수집이 그 종목을 같이 모은다."""
    f = _fetcher({}, ([], "naver", ""))
    hn.tick(_t("2026-10-12T06:00"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert f.calls == [] and hn.load_records() == []


def test_add_new_holdings_caps_per_tick(hn, monkeypatch):
    monkeypatch.setattr(hn, "MAX_ADD_PER_TICK", 2)
    model = _model(*[_h(f"종목{i}", f"00000{i}", "KR") for i in range(1, 6)])
    rec = {"date": "2026-10-12", "stocks": [], "universe": {"n": 0}}
    f = _fetcher({}, ([], "naver", ""))
    n = hn.add_new_holdings(rec, _t("2026-10-12T13:00"), records=[], model=model, fetch=f, body=lambda l: "",
                            pace=0, us_etf=set(), ko=lambda c: "")
    assert n == 2 and len(rec["stocks"]) == 2 and rec["universe"]["n"] == 2
    n = hn.add_new_holdings(rec, _t("2026-10-12T13:15"), records=[], model=model, fetch=f, body=lambda l: "",
                            pace=0, us_etf=set(), ko=lambda c: "")
    assert n == 2 and len(rec["stocks"]) == 4                                       # 남은 종목은 다음 주기에


def test_add_new_holdings_failure_does_not_stop_the_tick(hn, portfolio, monkeypatch):
    f = _fetcher({}, ([_it("소식", "2026-10-12T06:00:00+09:00", link="https://p/1")], "naver", ""))
    hn.tick(_t("2026-10-12T07:15"), llm=_llm(lambda p: json.dumps({"stocks": [{"key": "KR:005930"}]})),
            fetch=f, body=lambda l: "", pace=0)
    monkeypatch.setattr(hn, "add_new_holdings", lambda *a, **k: 1 / 0)
    res = hn.tick(_t("2026-10-12T07:30"), llm=_llm(_answer_all), fetch=f, body=lambda l: "", pace=0)
    assert res["status"] == "ok" and res["analyzed"] == 1                           # 남은 정리는 그대로 돈다


# ── 전일대비 기준 = KST 날짜 기준 앞선 날의 마지막 자산 업로드(사용자 2026-10-14) ──────────────────────────
def _pm(at, **vals):
    m = _model(*[_h(k, k, "KR", v) for k, v in vals.items()])
    m["_saved_ts"] = _t(at).timestamp()
    return m


def test_pnl_base_is_the_last_upload_of_the_latest_earlier_kst_date(hn):
    """오늘이 10/9 이고 앞선 기록이 10/5 뿐이면 10/5 의 마지막 업로드 — 오늘 여러 번 올려도 기준은 안 바뀐다."""
    assert hn.pnl_base(_t("2026-10-05T09:00"), _pm("2026-10-05T09:00", **{"000001": 100.0}), bak=None)["vals"] is None
    hn.pnl_base(_t("2026-10-05T20:00"), _pm("2026-10-05T19:00", **{"000001": 110.0}), bak=None)   # 같은 날 늦은 업로드
    pb = hn.pnl_base(_t("2026-10-09T10:00"), _pm("2026-10-09T09:30", **{"000001": 150.0}), bak=None)
    assert pb["vals"] == {"KR:000001": 110.0} and pb["at"] == "10-05 19:00"
    assert pb["cur_ts"] == _t("2026-10-09T09:30").timestamp()
    base2 = hn.pnl_base(_t("2026-10-09T15:00"), _pm("2026-10-09T14:50", **{"000001": 160.0}), bak=None)["vals"]
    assert base2 == pb["vals"]                                                     # 오늘 업로드는 기준이 아니다
    assert hn.pnl_base(_t("2026-10-10T09:00"), _pm("2026-10-09T14:50", **{"000001": 160.0}), bak=None)["vals"] == \
        {"KR:000001": 160.0}                                                       # 다음 날엔 어제 마지막 업로드


def test_pnl_base_seeds_from_the_backup_and_prunes(hn, monkeypatch):
    bak = _pm("2026-10-07T18:00", **{"000001": 90.0})
    pb = hn.pnl_base(_t("2026-10-08T10:00"), _pm("2026-10-08T09:00", **{"000001": 95.0}), bak=bak)
    assert pb["vals"] == {"KR:000001": 90.0} and pb["at"] == "10-07 18:00"
    led = json.loads(hn._base_path().read_text(encoding="utf-8"))
    assert set(led) == {"2026-10-07", "2026-10-08"}
    hn.pnl_base(_t("2026-10-30T10:00"), _pm("2026-10-29T09:00", **{"000001": 1.0}), bak=None)
    assert set(json.loads(hn._base_path().read_text(encoding="utf-8"))) == {"2026-10-29"}     # 10일 넘은 날은 지움
    # 가장 최근 날은 오래돼도 남긴다 — 12일 전 업로드가 마지막이면 그게 기준이다(지우면 매 렌더 넣고 지운다)
    pb = hn.pnl_base(_t("2026-11-15T10:00"), _pm("2026-10-29T09:00", **{"000001": 1.0}), bak=None)
    assert pb["vals"] == {"KR:000001": 1.0} and pb["at"] == "10-29 09:00"


def test_pnl_base_records_fx_level_only_for_a_fresh_upload(hn, monkeypatch):
    import bot.live_pct as lp
    seen = []
    monkeypatch.setattr(lp, "fx_levels_cached", lambda **kw: seen.append(kw.get("max_age")) or {"USD": 1350.0})
    hn.pnl_base(_t("2026-10-08T18:30"), _pm("2026-10-08T18:00", **{"000001": 1.0}), bak=_pm("2026-10-07T09:00", **{"000001": 1.0}))
    led = json.loads(hn._base_path().read_text(encoding="utf-8"))
    assert led["2026-10-08"]["fx"] == {"USD": 1350.0} and "fx" not in led["2026-10-07"]   # 옛 기록에 지금 환율 금지
    assert seen == [1800 + hn.FX_BASE_SLACK_SEC]                                 # 값 풀은 그 업로드 무렵 것만
    assert hn.pnl_base(_t("2026-10-09T10:00"), _pm("2026-10-09T09:00", **{"000001": 1.0}), bak=None)["fx"] == {"USD": 1350.0}


def test_cum_lookup_compounds_sessions_after_the_base(hn, ws_snaps):
    """기준 10-05 18:00 뒤 마감한 KR 세션 = 10-06·10-07·10-08(10-09 한글날) — 그날 종가 등락을 곱한다(장종료가 기준 ·
    사용자 2026-10-14). 장중이든 장 전이든 같은 값이다(실시간을 곱하지 않는다)."""
    ws_snaps("KR", "2026-10-06", [("KRX:000001", 1.0)])
    ws_snaps("KR", "2026-10-07", [("KRX:000001", 2.0)])
    ws_snaps("KR", "2026-10-08", [("KRX:000001", -1.0)])
    st = {"market": "KR", "ticker": "000001"}
    base = _t("2026-10-05T18:00").timestamp()
    want = {"ka": pytest.approx(1.01 * 1.02 * 0.99), "n": 3, "asof": "2026-10-08", "wait": [], "hol": [],
            "src": "tv", "need": None, "lag": 180, "nosnap": False, "why": ""}
    assert hn.cum_lookup(base, _t("2026-10-12T10:00"))(st) == want              # 장중(10-12 세션은 아직 안 닫힘)
    assert hn.cum_lookup(base, _t("2026-10-12T08:00"))(st) == want              # 장 전
    c = hn.cum_lookup(_t("2026-10-08T20:00").timestamp(), _t("2026-10-12T08:00"))(st)
    assert c == {"ka": 1, "n": 0, "asof": "2026-10-08", "wait": [], "hol": [], "src": "tv", "need": None,
                 "lag": 180, "nosnap": False, "why": ""}                                                      # 기준 뒤 마감 없음 → 0
    # 기준 무렵 종가 세션은 며칠 앞일 수 있다 — 일요일 업로드면 목요일(10-09 한글날 · 주말) 종가
    c = hn.cum_lookup(_t("2026-10-11T12:00").timestamp(), _t("2026-10-12T08:00"))(st)
    assert c["n"] == 0 and c["asof"] == "2026-10-08"
    # 그날 하루치에 이 종목만 없다(ETF·우선주·거래정지) — 0% 로 지어내지 않고 Yahoo 종가를 기다린다
    c = hn.cum_lookup(base, _t("2026-10-12T10:00"), closes={})({"market": "KR", "ticker": "000009"})
    assert c["ka"] is None and "TradingView 하루치에 없음" in c["why"] and c["src"] == "yahoo"
    assert c["need"] == {"key": "KR:000009", "y": "000009.KS", "from": "2026-10-02", "to": "2026-10-08",
                         "ready": _t("2026-10-08T16:00").timestamp()}   # 기준 앞 세션(10-05 대체휴일) · 마감 15:30 + 30분
    # 기준 10-01 18:00 → 10-02 세션 하루치가 없다(10-05 는 개천절 대체휴일이라 세션이 아니다)
    c = hn.cum_lookup(_t("2026-10-01T18:00").timestamp(), _t("2026-10-12T10:00"))(st)
    assert c["ka"] is None and "2026-10-02" in c["why"]                       # 하루치가 없는 세션은 지어내지 않는다
    assert "이 종목이 없음" not in c["why"]                                       # 하루치 통째 없음 ≠ 종목만 없음
    # 하루치를 받지 않는 시장(홍콩)은 거래소 달력 + Yahoo 일봉 종가 — 아직 못 받았으면 지어내지 않는다
    c = hn.cum_lookup(base, _t("2026-10-12T10:00"), closes={})({"market": "HK", "ticker": "0700.HK"})
    assert c["src"] == "yahoo" and c["ka"] is None and c["need"]["y"] == "0700.HK" and "홍콩 시장은" in c["why"]
    # 거래소 달력을 모르는 시장은 None — 부른 쪽이 `no_close_why` 를 단 '(—)' 로
    assert hn.cum_lookup(base, _t("2026-10-12T10:00"))({"market": "ZZ", "ticker": "X"}) is None
    assert hn.no_close_why("ZZ") == "ZZ 시장은 거래소 달력을 몰라 장종료가 기준 등락을 못 셈"


def test_summary_counts_holdings_new_since_the_base(hn, ws_snaps):
    hn.pnl_base(_t("2026-10-08T09:00"), _pm("2026-10-08T08:00", **{"005930": 100.0}), bak=None)
    m = _pm("2026-10-12T07:00", **{"005930": 999.0, "000660": 50.0})
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0), ("KRX:000660", 1.0)])
    html = hn.render_page([_rec(_stock("KR:005930"))], {}, _t("2026-10-12T08:00"), gen="GEN", portfolio=m)
    data = {d["k"]: d for d in json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])}
    assert data["KR:005930"]["v"] == 100 and data["KR:000660"]["v"] == 50    # 기준 금액 · 새 종목은 지금 금액
    assert "기준 날 이후 새로 생긴 1종목은 지금 기록 금액" in html and "10-08 08:00 업로드" in html


def test_without_an_earlier_date_the_base_is_todays_record_and_says_so(hn):
    html = hn.render_page([_rec(_stock("KR:005930"))], {}, _t("2026-10-12T08:00"), gen="GEN",
                          portfolio=_pm("2026-10-12T07:00", **{"005930": 1.0}))
    assert "기준 = 오늘 자산 기록(10-12 07:00 업로드 — 앞선 날짜의 기록이 아직 없음)" in html


def test_cum_lookup_stops_at_the_last_collected_close_and_names_the_pending_one(hn, ws_snaps):
    """10-08 15:30 에 닫힌 세션의 하루치는 마감 3시간 뒤(18:30) 받는다 — 그 전엔 받아 둔 마지막 종가(10-07)까지만 세고,
    아직 안 받은 세션을 이름으로 댄다(None 이면 하루 몇 시간씩 시장 전체가 '미확인' 이 된다 · 지어내지도 않는다)."""
    ws_snaps("KR", "2026-10-06", [("KRX:000001", 1.0)])
    ws_snaps("KR", "2026-10-07", [("KRX:000001", 2.0)])
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-08T17:00"))({"market": "KR", "ticker": "000001"})
    assert c == {"ka": pytest.approx(1.01 * 1.02), "n": 2, "asof": "2026-10-07", "wait": ["2026-10-08"], "hol": [],
                 "src": "tv", "need": None, "lag": 180, "nosnap": False, "why": ""}
    assert hn.asof_note(hn.close_asof([{"market": "KR", "key": "k"}], {"k": c})) == (
        "반영한 마지막 종가: 한국 10-07(10-08 종가는 마감 3시간 뒤 수집 — 받으면 다음 갱신에 반영)")


def test_session_after_a_pending_one_is_never_compounded_past_the_gap(hn, ws_snaps, monkeypatch):
    """아직 안 받은 세션 뒤의 세션은 하루치가 있어도 곱하지 않는다 — 곱하면 가운데 하루의 등락을 빼먹은 값이 된다
    (지어낸 값, #165). 평소엔 하루치를 날짜 순으로 받아 이 상태가 안 나오지만, 판정이 갈리는 날 조용히 틀리지 않게."""
    import bot.world_sectors as ws
    ws_snaps("KR", "2026-10-06", [("KRX:000001", 1.0)])
    ws_snaps("KR", "2026-10-08", [("KRX:000001", 5.0)])                         # 10-07 은 없고 10-08 은 있다
    monkeypatch.setattr(ws, "market_day_status", lambda mk, d, now, state=None: "pending")
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-12T08:00"))({"market": "KR", "ticker": "000001"})
    assert c["ka"] == pytest.approx(1.01) and c["asof"] == "2026-10-06" and c["wait"] == ["2026-10-07", "2026-10-08"]


def test_latest_card_and_summary_name_the_close_still_being_collected(hn, ws_snaps):
    """막 끝난 세션의 종가를 아직 안 받았으면 카드 툴팁과 요약 아래 줄이 그 날짜를 같이 말한다 — 값이 하루 전 종가까지인
    이유를 화면이 댄다(#43)."""
    hn.pnl_base(_t("2026-10-05T20:00"), _pm("2026-10-05T19:00", **{"005930": 1_000_000.0}), bak=None)
    ws_snaps("KR", "2026-10-06", [("KRX:005930", 1.0)])
    ws_snaps("KR", "2026-10-07", [("KRX:005930", 2.0)])
    m = _pm("2026-10-08T09:00", **{"005930": 1_000_000.0})
    html = hn.render_page([_rec(_stock("KR:005930"))], {}, _t("2026-10-08T17:00"), gen="GEN", portfolio=m)
    head = html.split("data-hncard='1'")[1].split("class='hn-chg")[1].split("</span>")[0]
    assert "2026-10-07 종가까지" in head and "2026-10-08 종가는 아직 수집 전(장 마감 3시간 뒤)" in head
    assert "한국 10-07(10-08 종가는 마감 3시간 뒤 수집" in html.split("id='hn-pnl-asof'>")[1].split("</div>")[0]


def test_asof_line_uses_the_earliest_close_when_a_new_holding_has_a_later_base(hn, ws_snaps):
    """기준(10-07 20:00) 종목은 10-07 종가까지인데, 10-08 장 마감 뒤(16:00) 새로 생긴 종목의 asof 는 그 업로드 무렵 종가
    (10-08 — 아직 안 받은 세션)다. 늦은 쪽을 적으면 '10-08 반영 · 10-08 수집 대기' 로 한 줄이 제 말을 뒤집는다 — 이른
    쪽을 적는다(독립 리뷰 2026-10-14 M-1)."""
    hn.pnl_base(_t("2026-10-07T21:00"), _pm("2026-10-07T20:00", **{"005930": 1_000_000.0}), bak=None)
    ws_snaps("KR", "2026-10-07", [("KRX:005930", 1.0), ("KRX:000660", 1.0)])
    m = _pm("2026-10-08T16:00", **{"005930": 1_000_000.0, "000660": 500_000.0})
    html = hn.render_page([_rec(_stock("KR:005930"))], {}, _t("2026-10-08T17:00"), gen="GEN", portfolio=m)
    line = html.split("id='hn-pnl-asof'>")[1].split("</div>")[0]
    assert line == "반영한 마지막 종가: 한국 10-07(10-08 종가는 마감 3시간 뒤 수집 — 받으면 다음 갱신에 반영)", line


def test_card_tip_with_a_closed_but_uncollected_session_does_not_say_none_closed(hn):
    """기준 뒤 마감은 했지만 그 하루치를 아직 안 받았다 — '마감한 세션이 없다' 가 아니다(평일마다 한국 15:30~18:30 ·
    미국 05:00~08:00 KST 에 모든 최신일 카드가 이 상태다, 독립 리뷰 2026-10-14 M-2)."""
    tip = hn._close_tip({"at": "10-07 20:00", "n": 0, "asof": "2026-10-07", "wait": ["2026-10-08"]})
    assert tip.startswith("10-07 20:00(KST) 자산 기록 뒤 마감한 세션의 종가를 아직 안 받아 0 — 2026-10-07 종가 그대로")
    assert "마감한 세션이 아직 없어" not in tip and "2026-10-08 종가는 아직 수집 전" in tip
    tip = hn._close_tip({"at": "10-07 20:00", "n": 0, "asof": "2026-10-07", "wait": []})
    assert tip == "10-07 20:00(KST) 자산 기록 뒤 마감한 세션이 아직 없어 0 — 2026-10-07 종가 그대로"


def test_asof_note_orders_markets_like_the_rest_of_the_page(hn):
    note = hn.asof_note({"HK": {"asof": None, "wait": []}, "US": {"asof": "2026-10-07", "wait": []},
                         "KR": {"asof": "2026-10-08", "wait": []}})
    assert note == "반영한 마지막 종가: 한국 10-08 · 미국 10-07 · 홍콩 —"
    assert hn.asof_note({}) == ""


def test_pnl_js_takes_the_basis_label_from_the_page(hn):
    """JS 가 이름을 리터럴로 적으면 서버 칸과 갈린다 — 하네스가 서버와 같은 글자를 쓰면 그걸 못 잡으므로 다른 글자로 잰다."""
    data = [{"k": "US:A", "v": 1000, "g": "XX", "p": 1.0, "c": "USD"}]
    o = _run_pnl_js(hn, data, {"fx": {"USD": {"pct": 0.0}}}, basis="BASIS-X")
    assert o["xxsub"] == "BASIS-X 1/1종목"


def test_pnl_js_retries_soon_while_the_server_says_fx_is_pending(hn):
    """대시보드가 막 재시작하면 환율 값 풀이 비어 있다 — 서버가 '받는 중'(`fx_pending`)이라 말하면 갖고 있던 값을 둔 채
    5초 뒤 다시 묻는다(1분 = 12번까지). 그 뒤엔 1분 주기로 돌아가되 **여전히 받는 중이면 갖고 있던 값을 지우지 않는다**
    (3차 독립 리뷰 2026-10-14 — 상한에 닿으면 빈 표로 '미확인' 을 그렸다). 받기를 마쳤는데 비면(`fx_pending` 없음) 그때
    '미확인' 이 사실이다(처음 판은 '첫 응답' 만 봐서 열어 둔 탭이 재시작마다 '미확인' 이 됐다)."""
    data = [{"k": "US:A", "v": 40000000, "g": "XX", "p": 1.0, "c": "USD"}]
    o = _run_pnl_js(hn, data, {"fx": {}, "fx_pending": True})
    assert o["delays"] == [5000] and o["xx"] == "SERVER" and o["xxsub"] == "SERVER"
    o = _run_pnl_js(hn, data, {"fx": {}, "fx_pending": True}, run=15)
    assert o["delays"] == [5000] * 12 + [60000] * 3 and o["xx"] == "SERVER" and o["xxsub"] == "SERVER"
    o = _run_pnl_js(hn, data, {"fx": {}})
    assert o["delays"] == [60000] and o["xxsub"] == "종가 기준 1/1종목 · 환율 미확인 1"
    o = _run_pnl_js(hn, data, {"fx": {"USD": {"pct": 1.0}}})
    assert o["delays"] == [60000] and o["xx"] == "+80만원 (+2.01%)"           # 4천만 × (1.01 × 1.01 − 1)


def test_pnl_js_keeps_polling_after_an_error_and_keeps_values_while_pending(hn):
    """열어 둔 탭 — 좋은 응답 → 연결 실패(대시보드 재시작 중) → 받는 중(빈 표) → 좋은 응답. 실패해도 1분 뒤 계속 묻고
    (5초로 당기지 않는다 — 서버가 내려간 동안 두드리지 않게), 받는 중엔 갖고 있던 환율을 지우지 않는다."""
    data = [{"k": "US:A", "v": 40000000, "g": "XX", "p": 1.0, "c": "USD"}]
    good = {"fx": {"USD": {"pct": 2.0}}}
    o = _run_pnl_js(hn, data, [good, "ERR"], run=2)
    assert o["delays"] == [60000, 60000] and o["xx"] == "+121만원 (+3.02%)"
    o = _run_pnl_js(hn, data, [good, {"fx": {}, "fx_pending": True}], run=2)
    assert o["delays"] == [60000, 5000] and o["xx"] == "+121만원 (+3.02%)" and "미확인" not in o["xxsub"]
    o = _run_pnl_js(hn, data, [good, "ERR", {"fx": {}, "fx_pending": True}, {"fx": {"USD": {"pct": 1.0}}}], run=4)
    assert o["delays"] == [60000, 60000, 5000, 60000] and o["xx"] == "+80만원 (+2.01%)"



# ── 하루치에 없는 종목의 Yahoo 일봉 종가(독립 리뷰 2026-10-14 High — 하루치는 보통주·DR 만) ──────────────────
def _etf_model(at="2026-10-08T08:00"):
    m = _model(_h("삼성전자", "005930", "KR", 100_000_000.0), _h("KODEX 200", "069500", "KR", 20_000_000.0,
                                                               source="etf_meta"))
    m["_saved_ts"] = _t(at).timestamp()
    return m


def test_refresh_closes_asks_only_for_holdings_missing_from_the_snapshot_and_render_uses_them(hn, ws_snaps):
    """ETF(069500)는 하루치에 없다 — 봇 주기가 그 종목만 Yahoo 에 묻고(보통주는 안 묻는다) 디스크에 두면, 렌더(네트워크
    0)가 같은 asof(10-08) 종가 비로 센다. 이미 있으면 다시 묻지 않는다."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    asked = []

    def dl(syms, start, end):
        asked.append((list(syms), start, end))
        return {"069500.KS": {"2026-10-07": 100.0, "2026-10-08": 101.0, "2026-10-12": 999.0}}, ""
    now = _t("2026-10-12T08:00")
    r = hn.refresh_closes(now, model=_etf_model(), download=dl)
    assert r == {"asked": 1, "got": 1, "why": ""} and asked == [(["069500.KS"], "2026-09-27", "2026-10-13")]
    html = hn.render_page([_rec({**_stock("KR:005930"), "name": "삼성전자"})], {}, now, gen="GEN",
                          portfolio=_etf_model())
    data = {d["k"]: d for d in json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])}
    assert data["KR:069500"]["p"] == pytest.approx(1.0) and data["KR:005930"]["p"] == pytest.approx(-2.0)
    assert html.split("id='hn-pnl-KR-sub'>")[1].split("</div>")[0] == "종가 기준 2/2종목"   # 10-12 장중 봉은 안 쓴다
    assert hn.refresh_closes(now, model=_etf_model(), download=dl) == {"asked": 0, "got": 0, "why": ""}
    assert len(asked) == 1


def test_refresh_closes_backs_off_and_tries_kosdaq_suffix(hn, ws_snaps):
    """.KS 로 못 받으면 코스닥(.KQ)으로 한 번 더 · 둘 다 없으면 30분 → 60분 … 쉬었다 묻는다(원천을 15분마다 두드리지
    않게, #303) · 사유는 카드까지 간다."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    asked = []

    def dl(syms, start, end):
        asked.append(list(syms))
        return {}, ""
    m = _etf_model()
    hn.refresh_closes(_t("2026-10-12T08:00"), model=m, download=dl)
    assert asked == [["069500.KS"], ["069500.KQ"]]
    hn.refresh_closes(_t("2026-10-12T08:20"), model=m, download=dl)               # 첫 실패 → 30분 안 묻는다
    assert len(asked) == 2                                                        # (.KQ 재시도는 같은 바퀴 — 한 번만 센다)
    hn.refresh_closes(_t("2026-10-12T08:35"), model=m, download=dl)
    assert len(asked) == 4
    hn.refresh_closes(_t("2026-10-12T09:20"), model=m, download=dl)               # 두 번째 실패 → 60분
    assert len(asked) == 4
    hn.refresh_closes(_t("2026-10-12T09:40"), model=m, download=dl)
    assert len(asked) == 6
    c = hn.cum_lookup(_t("2026-10-08T08:00").timestamp(), _t("2026-10-12T09:40"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] is None and "Yahoo 일봉에 069500.KQ 가 없음" in c["why"]


def test_yahoo_close_fetched_before_the_session_ended_is_not_a_close(hn, ws_snaps):
    """장중에 받아 둔 봉은 종가가 아니다 — 그 세션이 끝난(+30분) 뒤에 받은 봉만 쓴다(지어낸 값 금지)."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    _closes(hn, {"KR:069500": ({"2026-10-07": 100.0, "2026-10-08": 101.0}, "2026-10-08T15:00")})   # 장중에 받음
    c = hn.cum_lookup(_t("2026-10-08T08:00").timestamp(), _t("2026-10-12T08:00"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] is None and "2026-10-08 종가가 아직 없음" in c["why"]
    _closes(hn, {"KR:069500": ({"2026-10-07": 100.0, "2026-10-08": 101.0}, "2026-10-08T16:00")})
    c = hn.cum_lookup(_t("2026-10-08T08:00").timestamp(), _t("2026-10-12T08:00"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] == pytest.approx(1.01) and c["src"] == "yahoo"
    tip = hn._close_tip({"at": "10-08 08:00", **c})
    assert "2026-10-07 종가 → 2026-10-08 종가(Yahoo 일봉 · TradingView 하루치에 없는 종목이라)" in tip


def test_hong_kong_holding_gets_a_yahoo_close_value(hn):
    """하루치를 받지 않는 시장 — 거래소 달력의 세션 + Yahoo 일봉 종가(마감 30분 뒤)로 같은 식을 센다."""
    m = _model(_h("텐센트", "0700.HK", "HK", 1_000_000.0))
    m["_saved_ts"] = _t("2026-10-08T09:00").timestamp()
    _closes(hn, {"HK:0700.HK": ({"2026-10-07": 500.0, "2026-10-08": 505.0, "2026-10-09": 510.0},
                                "2026-10-09T18:00")})
    rec = _rec({**_stock("HK:0700.HK"), "market": "HK", "ticker": "0700.HK", "sym": "0700.HK", "name": "텐센트"})
    html = hn.render_page([rec], {}, _t("2026-10-09T18:00"), gen="GEN", portfolio=m)
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    assert data[0]["p"] == pytest.approx(2.0)                                      # 10-07 종가 500 → 10-09 종가 510
    assert "홍콩 10-09" in html.split("id='hn-pnl-asof'>")[1].split("</div>")[0]


def test_yahoo_symbol_by_market(hn):
    assert hn.yahoo_symbol({"market": "KR", "ticker": "069500", "sym": "069500"}) == "069500.KS"
    assert hn.yahoo_symbol({"market": "KR", "ticker": "069500", "sym": "069500"}, alt=True) == "069500.KQ"
    assert hn.yahoo_symbol({"market": "KR", "ticker": "035900", "sym": "035900.KQ"}) == "035900.KQ"
    assert hn.yahoo_symbol({"market": "US", "ticker": "BRK.B", "sym": "BRK.B"}) == "BRK-B"
    assert hn.yahoo_symbol({"market": "JP", "ticker": "7203.T", "sym": "7203.T"}) == "7203.T"


def test_yf_daily_reads_the_real_multiindex_shape(hn, monkeypatch):
    """`group_by='ticker'` 응답(종목 × 칸 MultiIndex) 그대로 — 손으로 만든 dict 로는 파서를 못 잰다(#155)."""
    import pandas as pd
    import yfinance as yf
    idx = pd.to_datetime(["2026-10-07", "2026-10-08"])
    cols = pd.MultiIndex.from_product([["069500.KS", "QQQ"], ["Open", "Close"]])
    df = pd.DataFrame([[1, 100.0, 1, 500.0], [1, 101.0, 1, float("nan")]], index=idx, columns=cols)
    seen = {}
    monkeypatch.setattr(yf, "download", lambda syms, **kw: seen.update(kw, syms=syms) or df)
    out, why = hn._yf_daily(["069500.KS", "QQQ", "NONE"], "2026-10-01", "2026-10-09")
    assert out == {"069500.KS": {"2026-10-07": 100.0, "2026-10-08": 101.0}, "QQQ": {"2026-10-07": 500.0}}
    assert why == "" and seen["auto_adjust"] is False and seen["interval"] == "1d"
    monkeypatch.setattr(yf, "download", lambda *a, **k: 1 / 0)
    assert hn._yf_daily(["A"], "x", "y")[1].startswith("Yahoo 일봉 실패(ZeroDivisionError")


def test_tick_and_render_fetches_closes_before_rendering(hn, monkeypatch):
    order = []
    monkeypatch.setattr(hn, "tick", lambda now=None: order.append("tick") or {"status": "ok"})
    monkeypatch.setattr(hn, "refresh_closes", lambda now=None: order.append("closes") or {"asked": 0, "got": 0, "why": ""})
    monkeypatch.setattr(hn, "regenerate", lambda now=None: order.append("render") or True)
    assert hn.tick_and_render(_t(_NOW)) == {"status": "ok"} and order == ["tick", "closes", "render"]
    monkeypatch.setattr(hn, "refresh_closes", lambda now=None: 1 / 0)          # 실패는 그 칸만 — 렌더는 돈다(#315)
    order.clear()
    hn.tick_and_render(_t(_NOW))
    assert order == ["tick", "render"]


def test_refresh_closes_respects_the_yfinance_pause(hn, ws_snaps, monkeypatch):
    import bot.finviz_client as fc
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    monkeypatch.setattr(fc, "yf_paused", lambda: True)
    r = hn.refresh_closes(_t("2026-10-12T08:00"), model=_etf_model(), download=lambda *a: 1 / 0)
    assert r["asked"] == 0 and "일시정지" in r["why"]



def test_yahoo_ratio_uses_the_bar_closest_before_the_base(hn):
    """기준 무렵 종가 = 기준 세션 **이하에서 가장 늦은** 봉 — 더 옛 봉을 쓰면 그 사이 등락이 섞인다."""
    ent = {"bars": {"2026-10-01": 90.0, "2026-10-02": 100.0, "2026-10-08": 101.0}, "at": 2e9}
    r, why = hn.yahoo_ratio(ent, {"from": "2026-10-02", "to": "2026-10-08", "ready": 1e9})
    assert r == pytest.approx(1.01) and why == ""


def test_hong_kong_session_waits_until_its_close_is_settled(hn):
    """하루치 없는 시장 — 마감(홍콩 16:00 = 17:00 KST) 뒤 30분이 지나야 그 세션 종가를 센다. 그 전엔 앞 세션까지와
    '수집 대기' 로(장중·막 끝난 봉을 종가로 굽지 않게)."""
    m = _model(_h("텐센트", "0700.HK", "HK", 1_000_000.0))
    m["_saved_ts"] = _t("2026-10-08T09:00").timestamp()
    _closes(hn, {"HK:0700.HK": ({"2026-10-07": 500.0, "2026-10-08": 505.0}, "2026-10-09T17:15")})
    cum = hn.cum_lookup(m["_saved_ts"], _t("2026-10-09T17:15"))
    c = cum({"key": "HK:0700.HK", "market": "HK", "ticker": "0700.HK", "sym": "0700.HK"})
    assert c["wait"] == ["2026-10-09"] and c["asof"] == "2026-10-08" and c["ka"] == pytest.approx(1.01)


def test_refresh_closes_does_not_reask_what_the_cache_already_answers(hn, ws_snaps):
    """이미 그 날짜 종가가 있으면 시간이 지나도 다시 묻지 않는다(쉬는 시간이 아니라 캐시가 답한다)."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    asked = []

    def dl(syms, start, end):
        asked.append(list(syms))
        return {"069500.KS": {"2026-10-07": 100.0, "2026-10-08": 101.0}}, ""
    hn.refresh_closes(_t("2026-10-12T08:00"), model=_etf_model(), download=dl)
    hn.refresh_closes(_t("2026-10-12T14:00"), model=_etf_model(), download=dl)    # 6시간 뒤에도
    assert asked == [["069500.KS"]]


def test_yahoo_value_does_not_name_holiday_sessions(hn, ws_snaps, monkeypatch):
    """휴장 추정 0 은 하루치 곱의 일이다 — 종가 비로 센 값의 툴팁이 '0 으로 셈' 을 말하면 거짓이다."""
    import bot.world_sectors as ws
    ws_snaps("KR", "2026-10-06", [("KRX:005930", 1.0)])
    monkeypatch.setattr(ws, "market_day_status", lambda mk, d, now, state=None: "holiday")
    _closes(hn, {"KR:069500": ({"2026-10-02": 100.0, "2026-10-07": 102.0}, "2026-10-12T08:00")})
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-12T08:00"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    # 10-06 하루치에 없음 → Yahoo · 10-07·10-08 은 휴장 추정(하루치 곱에서만 0)
    assert c["src"] == "yahoo" and c["hol"] == [] and "휴장 추정" not in hn._close_tip({"at": "x", **c})


def _closes_raw(hn) -> dict:
    return json.loads(hn._closes_path().read_text(encoding="utf-8"))


def test_refresh_closes_replaces_the_fetched_window_so_an_intraday_bar_cannot_become_a_close(hn, ws_snaps):
    """3차 독립 리뷰 2026-10-14 High — 장중에 받아 둔 10-08 봉(150)이, 장 마감 뒤 다시 물었을 때 Yahoo 가 그날 봉을 아직
    안 실어(응답에 10-08 없음) 옛 값 그대로 남았다. 받은 시각만 '마감 뒤' 로 바뀌어 장중 값이 종가로 읽혔다. 받은 창 안은
    통째로 갈아 끼운다 — 그날 봉이 없으면 '아직 없음' 이 사실이다."""
    ws_snaps("KR", "2026-10-07", [("KRX:005930", 1.0)])
    replies = [{"069500.KS": {"2026-10-06": 100.0, "2026-10-07": 101.0, "2026-10-08": 150.0}},   # 10-08 장중 봉 포함
               {"069500.KS": {"2026-10-06": 100.0, "2026-10-07": 101.0}}]                       # 그날 봉 아직 없음
    m = _etf_model("2026-10-07T08:00")
    assert hn.refresh_closes(_t("2026-10-08T10:00"), model=m, download=lambda *a: (replies.pop(0), ""))["got"] == 1
    assert _closes_raw(hn)["KR:069500"]["bars"]["2026-10-08"] == 150.0
    ws_snaps("KR", "2026-10-08", [("KRX:005930", 1.0)])                          # 10-08 하루치가 들어와 목표가 10-08 로
    r = hn.refresh_closes(_t("2026-10-08T19:00"), model=m, download=lambda *a: (replies.pop(0), ""))
    assert r["asked"] == 1 and r["got"] == 0 and "2026-10-08" not in _closes_raw(hn)["KR:069500"]["bars"]
    c = hn.cum_lookup(m["_saved_ts"], _t("2026-10-08T19:00"))({"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] is None and "Yahoo 일봉에 2026-10-08 종가가 아직 없음" in c["why"]


def test_refresh_closes_backoff_counts_only_the_current_target_session(hn, ws_snaps):
    """쉬는 간격은 **그 목표 세션** 의 실패만 센다 — 어제 종가를 여러 번 못 받은 기록(실패 8번 = 8시간 쉼)이 오늘 종가를
    붙잡으면 하루 종일 옛 값이다(3차 독립 리뷰 2026-10-14). 같은 목표면 상한(8시간)까지 쉰다."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    now = _t("2026-10-12T08:00")
    asked = []

    def dl(syms, start, end):
        asked.append(list(syms))
        return {}, ""
    m = _etf_model()
    hn._closes_path().parent.mkdir(parents=True, exist_ok=True)

    def plant(target, tried):
        hn._closes_path().write_text(json.dumps({"KR:069500": {
            "y": "069500.KS", "bars": {"2026-10-02": 100.0}, "at": 1.0, "tried": _t(tried).timestamp(), "fails": 8,
            "for": target}}), encoding="utf-8")
    plant("2026-10-07", "2026-10-12T07:59")                                       # 다른 목표의 실패 — 바로 묻는다
    hn.refresh_closes(now, model=m, download=dl)
    ent = _closes_raw(hn)["KR:069500"]
    assert asked == [["069500.KS"]] and ent["for"] == "2026-10-08" and ent["fails"] == 1
    asked.clear()
    plant("2026-10-08", "2026-10-12T00:01")                                       # 같은 목표 · 실패 8번 · 7시간 59분 전
    hn.refresh_closes(now, model=m, download=dl)
    assert asked == []
    plant("2026-10-08", "2026-10-12T00:00")                                       # 8시간(상한) 지남
    hn.refresh_closes(now, model=m, download=dl)
    assert asked == [["069500.KS"]] and _closes_raw(hn)["KR:069500"]["fails"] == 9


def test_refresh_closes_asks_in_chunks_of_fifty(hn, ws_snaps):
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    m = _model(*[_h(f"E{i}", f"{i + 100000:06d}", "KR", 1_000_000.0, source="etf_meta") for i in range(51)])
    m["_saved_ts"] = _t("2026-10-08T08:00").timestamp()
    sizes = []
    hn.refresh_closes(_t("2026-10-12T08:00"), model=m, download=lambda syms, s, e: (sizes.append(len(syms)) or {}, ""))
    assert sizes == [50, 1, 50, 1]                                                # .KS 두 번 + .KQ 두 번


def test_refresh_closes_does_not_try_kosdaq_when_the_symbol_is_known(hn, ws_snaps):
    """자산 기록이 .KS 를 줬거나 한 번 받아 심볼을 배운 종목은 .KQ 로 다시 갈아 보지 않는다 — 그때 못 받은 건 날짜
    문제라 다른 심볼을 물으면 헛요청이다(3차 독립 리뷰 2026-10-14)."""
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    asked = []

    def dl(syms, start, end):
        asked.append(list(syms))
        return {}, ""
    m = _model(_h("KODEX 200", "069500.KS", "KR", 1_000_000.0, source="etf_meta"))
    m["_saved_ts"] = _t("2026-10-08T08:00").timestamp()
    hn.refresh_closes(_t("2026-10-12T08:00"), model=m, download=dl)
    assert asked == [["069500.KS"]]
    asked.clear()
    hn._closes_path().write_text(json.dumps({"KR:069500": {"y": "069500.KS", "bars": {"2026-10-02": 100.0},
                                                           "at": 1.0}}), encoding="utf-8")
    hn.refresh_closes(_t("2026-10-12T08:00"), model=_etf_model(), download=dl)
    assert asked == [["069500.KS"]]


def test_refresh_closes_drops_sold_holdings_after_sixty_days(hn, ws_snaps):
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    now = _t("2026-10-12T08:00")
    hn._closes_path().parent.mkdir(parents=True, exist_ok=True)
    hn._closes_path().write_text(json.dumps({
        "KR:111111": {"y": "111111.KS", "bars": {}, "tried": (now - timedelta(days=61)).timestamp()},
        "KR:222222": {"y": "222222.KS", "bars": {}, "tried": (now - timedelta(days=59)).timestamp()}}), encoding="utf-8")
    hn.refresh_closes(now, model=_etf_model(), download=lambda *a: ({}, ""))
    assert set(_closes_raw(hn)) == {"KR:222222", "KR:069500"}


def test_refresh_closes_pause_reason_reaches_the_card(hn, ws_snaps, monkeypatch):
    """`/yfpause` 로 묻지 않았으면 카드가 그 사유를 말한다 — '아직 안 받음' 이면 왜 안 받았는지 숨긴다(#82)."""
    import bot.finviz_client as fc
    ws_snaps("KR", "2026-10-08", [("KRX:005930", -2.0)])
    monkeypatch.setattr(fc, "yf_paused", lambda: True)
    hn.refresh_closes(_t("2026-10-12T08:00"), model=_etf_model(), download=lambda *a: 1 / 0)
    c = hn.cum_lookup(_t("2026-10-08T08:00").timestamp(), _t("2026-10-12T08:00"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] is None and "yfinance 일시정지(YF_PAUSE)" in c["why"]
    # 옛 봉을 들고 있어도(그날 봉만 없다) 마지막 물음의 사유가 '아직 없음' 보다 앞선다
    _closes(hn, {"KR:069500": ({"2026-10-02": 100.0}, "2026-10-11T08:00")})
    hn.refresh_closes(_t("2026-10-12T08:00"), model=_etf_model(), download=lambda *a: 1 / 0)
    c = hn.cum_lookup(_t("2026-10-08T08:00").timestamp(), _t("2026-10-12T08:00"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] is None and "yfinance 일시정지(YF_PAUSE)" in c["why"] and "아직 없음" not in c["why"]


def test_yahoo_ratio_base_is_the_last_bar_on_or_before_the_base_session(hn):
    """기준 세션 날 봉이 없으면(원천 결측) 그 앞 가장 늦은 봉 — 앞에 아무 봉도 없으면 지어내지 않는다."""
    ent = {"bars": {"2026-10-01": 90.0, "2026-10-02": 100.0, "2026-10-08": 101.0}, "at": 2e9}
    assert hn.yahoo_ratio(ent, {"from": "2026-10-05", "to": "2026-10-08", "ready": 1e9})[0] == pytest.approx(1.01)
    r, why = hn.yahoo_ratio(ent, {"from": "2026-09-30", "to": "2026-10-08", "ready": 1e9})
    assert r is None and why == "Yahoo 일봉에 2026-09-30 무렵 종가가 없음"


def test_yahoo_value_stops_at_the_last_real_session_before_a_holiday(hn, ws_snaps, monkeypatch):
    """휴장 추정 세션엔 Yahoo 봉이 없다 — 종가 비의 끝은 그 앞 실제 세션(그 날을 asof 로 적는다, 3차 독립 리뷰)."""
    import bot.world_sectors as ws
    ws_snaps("KR", "2026-10-06", [("KRX:005930", 1.0)])
    monkeypatch.setattr(ws, "market_day_status", lambda mk, d, now, state=None: "holiday")
    _closes(hn, {"KR:069500": ({"2026-10-02": 100.0, "2026-10-06": 103.0, "2026-10-07": 999.0}, "2026-10-12T08:00")})
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-12T08:00"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] == pytest.approx(1.03) and c["asof"] == "2026-10-06" and c["need"]["to"] == "2026-10-06"


def test_a_missed_snapshot_session_falls_back_to_yahoo_and_says_so(hn, ws_snaps, monkeypatch):
    """하루치를 놓친 세션이 있으면 보통주도 Yahoo 종가 비로 센다 — 그 세션의 등락을 빼먹지 않게(3차 독립 리뷰). 끝은
    놓친 세션까지(휴장 추정 뒤는 아니다), 사유·툴팁이 '놓친 세션' 갈래를 이름으로 댄다(#82)."""
    import bot.world_sectors as ws
    ws_snaps("KR", "2026-10-06", [("KRX:005930", 1.0)])
    st_by = {"2026-10-07": "missing", "2026-10-08": "holiday"}
    monkeypatch.setattr(ws, "market_day_status", lambda mk, d, now, state=None: st_by.get(d, "ok"))
    st = {"key": "KR:005930", "market": "KR", "ticker": "005930"}
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-12T08:00"))(st)
    assert c["ka"] is None and c["src"] == "yahoo" and c["need"]["to"] == "2026-10-07"
    assert c["why"].startswith("TradingView 하루치를 놓친 세션이 있음(2026-10-07 하루치를 받을 창을 놓쳐")
    _closes(hn, {"KR:005930": ({"2026-10-02": 100.0, "2026-10-07": 105.0, "2026-10-08": 999.0}, "2026-10-12T08:00")})
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-12T08:00"))(st)
    assert c["ka"] == pytest.approx(1.05) and c["asof"] == "2026-10-07" and c["broken"] == ["2026-10-07"]
    assert "Yahoo 일봉 · TradingView 하루치를 놓친 세션(2026-10-07)이 있어" in hn._close_tip({"at": "x", **c})


def test_cum_lookup_without_a_base_session_in_reach_says_so(hn, ws_snaps, monkeypatch):
    """기준 무렵 세션이 14일 안에 없으면(달력 이상) 종가 비의 출발점을 모른다 — 지어내지 않고 사유를 단다."""
    import bot.world_sectors as ws
    real = ws.session_times
    monkeypatch.setattr(ws, "session_times", lambda cal, d: real(cal, d) if d >= "2026-10-06" else None)
    ws_snaps("KR", "2026-10-06", [("KRX:005930", 1.0)])
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-07T08:00"))(
        {"key": "KR:069500", "market": "KR", "ticker": "069500"})
    assert c["ka"] is None and c["why"] == "기준 무렵 세션을 달력에서 못 찾음(14일 안)"


def test_hong_kong_wait_and_reason_use_its_own_timing(hn):
    """홍콩(하루치 없음)은 마감 30분 뒤에 종가를 받는다 — 요약 줄·툴팁이 '3시간 뒤'(하루치 시각)라 적으면 거짓이다.
    Yahoo 로 센 까닭도 '하루치에 없는 종목' 이 아니라 '하루치를 받지 않는 시장' 이다(3차 독립 리뷰 2026-10-14)."""
    m = _model(_h("텐센트", "0700.HK", "HK", 1_000_000.0))
    m["_saved_ts"] = _t("2026-10-08T09:00").timestamp()
    _closes(hn, {"HK:0700.HK": ({"2026-10-07": 500.0, "2026-10-08": 505.0}, "2026-10-09T17:15")})
    st = {"key": "HK:0700.HK", "market": "HK", "ticker": "0700.HK", "sym": "0700.HK"}
    c = hn.cum_lookup(m["_saved_ts"], _t("2026-10-09T17:15"))(st)
    assert hn.asof_note(hn.close_asof([st], {"HK:0700.HK": c})) == (
        "반영한 마지막 종가: 홍콩 10-08(10-09 종가는 마감 30분 뒤 수집 — 받으면 다음 갱신에 반영)")
    tip = hn._close_tip({"at": "x", **c})
    assert "(Yahoo 일봉 · TradingView 하루치를 받지 않는 시장이라)" in tip and "장 마감 30분 뒤" in tip


def test_pnl_js_does_not_poll_when_no_overseas_holding_has_a_value(hn):
    """해외 보유가 전부 '(—)' 면(홍콩 종가를 아직 못 받음 등) 환율로 다시 그릴 것이 없다 — 묻지 않는다."""
    o = _run_pnl_js(hn, [{"k": "HK:0700.HK", "v": 1000, "g": "XX", "p": None, "c": "HKD"},
                         {"k": "KR:A", "v": 1000, "g": "KR", "p": 1.0}], {"fx": {"HKD": {"pct": 1.0}}})
    assert o["urls"] == [] and o["xx"] == "SERVER"


def test_pnl_js_fx_uses_base_level_when_known(hn):
    data = [{"k": "US:AAPL", "v": 40000000, "g": "XX", "p": 0, "c": "USD", "fb": 1300.0}]
    resp = {"q": {}, "pending": 0, "fx": {"USD": {"pct": 0.5, "age": 1, "close": 1326.0}}}
    cell = {"data-p": "0", "data-v": "40000000", "data-c": "USD", "data-fb": "1300"}
    o = _run_pnl_js(hn, data, resp, cell=cell)
    assert o["xx"] == "+80만원 (+2.00%)"                                         # 1326 ÷ 1300 = +2% (오늘 0.5% 아님)
    assert "원/USD 기준 대비 +2.00%" in o["title"] and o["cell"] == "(0.00% · 원화 +80만원)"
    data[0].pop("fb")
    cell.pop("data-fb")
    o = _run_pnl_js(hn, data, resp, cell=cell)
    assert o["xx"] == "+20만원 (+0.50%)"                                         # 기준 수준이 없으면 오늘 하루치
    assert "원/USD 오늘 하루 +0.50%" in o["title"]


def test_render_to_js_e2e_card_uses_base_amount_and_cumulative(hn, ws_snaps):
    """렌더된 카드의 재료(js-hn-amt)·요약 JSON 을 그대로 JS 에 태운다 — 손으로 만든 재료로는 배선을 못 본다(#20).
    카드와 요약이 같은 기준 금액 · 같은 장종료가 누적 · 같은 환율로 같은 금액을 그린다(#38·#33)."""
    import html as _html
    import re
    base = _model(_h("애플", "AAPL", "US", 1_000_000.0, source="alias"))
    base["_saved_ts"] = _t("2026-10-07T19:00").timestamp()
    hn.pnl_base(_t("2026-10-07T20:00"), base, bak=None)                        # 업로드 한 시간 뒤 — 환율 수준 없음
    ws_snaps("US", "2026-10-07", [("NASDAQ:AAPL", 1.0)])
    ws_snaps("US", "2026-10-08", [("NASDAQ:AAPL", 1.0)])
    m = _model(_h("애플", "AAPL", "US", 5_000_000.0, source="alias"))          # 오늘 올린 기록(금액이 다르다)
    m["_saved_ts"] = _t("2026-10-09T07:30").timestamp()
    rec = _rec({**_stock("US:AAPL"), "market": "US", "ticker": "AAPL", "name": "애플"})
    html = hn.render_page([rec], {}, _t("2026-10-09T08:00"), gen="GEN", portfolio=m)
    card = html.split("data-hncard='1'")[1]
    span = card.split("class='hn-chg")[1].split(">")[0]
    attrs = {k: _html.unescape(v) for k, v in re.findall(r"(data-[a-z]+)='([^']*)'", span)}
    cls = "hn-chg" + span.split("'")[0]                                          # 렌더된 class 그대로
    assert attrs["data-v"] == "1000000" and float(attrs["data-p"]) == pytest.approx(2.01)   # 기준 날 금액 · 두 세션 곱
    assert "보유 500만원" in card                                                # 보유 칸은 지금 기록
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    o = _run_pnl_js(hn, data, {"q": {}, "pending": 0, "fx": {"USD": {"pct": 1.0, "age": 1}}}, cell=attrs, cls=cls)
    k = (1.01 * 1.01 * 1.01 - 1) * 100
    assert o["cell"] == f"(+2.01% · 원화 {hn._signed_krw(1_000_000 * k / 100)})"
    assert o["xx"] == f"{hn._signed_krw(1_000_000 * k / 100)} ({k:+.2f}%)"     # 요약과 카드가 같은 값
    assert o["title"].startswith(attrs["data-tip"]) and "2026-10-08 종가까지" in o["title"]


def test_hong_kong_holding_says_why_it_has_no_close_value(hn):
    """옛 계약(하루치가 없는 홍콩은 실시간 하루 등락)은 사용자 2026-10-14 "기준을 장종료가격으로" 로 바뀌었다(#222) —
    하루치를 받지 않는 시장은 Yahoo 일봉 종가로 세고, 아직 못 받았으면 지어내지 않고 '(—)' 에 사유를 단다 · 요약은
    분모에서 뺀다(아래 테스트가 받은 뒤의 값을 잰다)."""
    m = _model(_h("텐센트", "0700.HK", "HK", 1_000_000.0))
    m["_saved_ts"] = _t("2026-10-08T09:00").timestamp()
    # 카드 키는 `universe` 와 같은 꼴(`_code` — 홍콩은 접미까지) — 요약 종목과 같은 키여야 같은 값을 쓴다
    rec = _rec({**_stock("HK:0700.HK"), "market": "HK", "ticker": "0700.HK", "sym": "0700.HK", "name": "텐센트"})
    html = hn.render_page([rec], {}, _t("2026-10-12T10:00"), gen="GEN", portfolio=m)
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    assert data[0]["p"] is None and data[0]["c"] == "HKD"
    head = html.split("data-hncard='1'")[1].split("</span>")[0]
    assert "(—)" in head and "홍콩 시장은 TradingView 하루치를 받지 않음 — Yahoo 일봉 종가로 채우는데 아직 안 받음" in head
    assert html.split("id='hn-pnl-XX-sub'>")[1].split("</div>")[0] == "종가 기준 0/1종목"
    assert "js-live-pct" not in html
    assert html.split("id='hn-pnl-asof'>")[1].split("</div>")[0] == "반영한 마지막 종가: 홍콩 —"


def test_new_holding_since_base_counts_from_its_own_upload(hn, ws_snaps):
    """기준(10-05) 뒤에 새로 생긴 종목은 기준 날부터 세면 들고 있지 않던 날의 손익을 지어낸다 — 그 종목이 처음 실린
    지금 기록의 업로드 시각부터 센다(리뷰 2026-10-14 High)."""
    hn.pnl_base(_t("2026-10-05T20:00"), _pm("2026-10-05T19:00", **{"005930": 100.0}), bak=None)
    for d, v in (("2026-10-06", 1.0), ("2026-10-07", 2.0), ("2026-10-08", 3.0)):
        ws_snaps("KR", d, [("KRX:005930", v), ("KRX:000660", v)])
    m = _pm("2026-10-08T16:00", **{"005930": 999.0, "000660": 50.0})            # 10-08 장 마감 뒤 올린 기록
    html = hn.render_page([_rec(_stock("KR:005930"))], {}, _t("2026-10-08T19:00"), gen="GEN", portfolio=m)
    data = {d["k"]: d for d in json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])}
    assert data["KR:005930"]["p"] == pytest.approx((1.01 * 1.02 * 1.03 - 1) * 100)
    assert data["KR:000660"]["p"] == 0 and data["KR:000660"]["v"] == 50          # 새 종목: 그 업로드 뒤 마감 없음


def test_overseas_card_carries_its_fx_base_level(hn, ws_snaps, monkeypatch):
    import bot.live_pct as lp
    monkeypatch.setattr(lp, "fx_levels_cached", lambda **kw: {"USD": 1300.0})
    m = _model(_h("애플", "AAPL", "US", 40_000_000.0, source="alias"))
    m["_saved_ts"] = _t("2026-10-09T08:00").timestamp()
    hn.pnl_base(_t("2026-10-09T08:10"), m, bak=None)                                # 업로드 10분 뒤 처음 봄 → 환율 수준 적음
    ws_snaps("US", "2026-10-09", [("NASDAQ:AAPL", 1.0)])
    rec = _rec({**_stock("US:AAPL"), "market": "US", "ticker": "AAPL", "name": "애플"})
    html = hn.render_page([rec], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio=m)
    data = json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])
    assert data[0]["fb"] == 1300.0 and "data-fb='1300.0'" in html.split("data-hncard='1'")[1]


def test_new_overseas_holding_uses_its_own_upload_fx_level(hn, ws_snaps, monkeypatch):
    """기준 날 뒤 새로 생긴 해외 종목의 환율 기준은 그 종목이 처음 실린 기록 무렵 수준이다(기준 날 수준 아님)."""
    import bot.live_pct as lp
    lv = {"v": {"USD": 1300.0}}
    monkeypatch.setattr(lp, "fx_levels_cached", lambda **kw: lv["v"])
    old = _model(_h("삼성전자", "005930", "KR", 1.0))
    old["_saved_ts"] = _t("2026-10-08T08:00").timestamp()
    hn.pnl_base(_t("2026-10-08T08:10"), old, bak=None)
    lv["v"] = {"USD": 1350.0}
    m = _model(_h("삼성전자", "005930", "KR", 1.0), _h("애플", "AAPL", "US", 40_000_000.0, source="alias"))
    m["_saved_ts"] = _t("2026-10-12T07:00").timestamp()
    html = hn.render_page([_rec(_stock("KR:005930"))], {}, _t("2026-10-12T07:10"), gen="GEN", portfolio=m)
    data = {d["k"]: d for d in json.loads(html.split("id='hn-pnl-data'")[1].split(">", 1)[1].split("</script>")[0])}
    assert data["US:AAPL"]["fb"] == 1350.0


def test_unreadable_ledger_is_not_overwritten(hn):
    p = hn._base_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{깨짐", encoding="utf-8")
    hn.pnl_base(_t("2026-10-09T10:00"), _pm("2026-10-09T09:00", **{"000001": 1.0}), bak=None)
    assert p.read_text(encoding="utf-8") == "{깨짐"                              # 쌓은 날을 빈 장부로 덮지 않는다


def test_holiday_session_counts_as_no_change_and_missing_one_is_not_invented(hn, ws_snaps, monkeypatch):
    """원천 값이 직전 세션과 같았던 세션(휴장 추정 — `market_day_status` 'holiday')은 값이 안 움직인 날이라 등락 0
    으로 센다. 못 받은 세션('missing')은 지어내지 않는다 — 두 갈래를 같은 '(—)' 로 뭉개면 휴장 하루가 그 뒤 며칠을
    통째로 '미확인' 으로 만든다(#82)."""
    import bot.world_sectors as ws
    ws_snaps("KR", "2026-10-06", [("KRX:000001", 1.0)])
    ws_snaps("KR", "2026-10-08", [("KRX:000001", 3.0)])
    kind = {"v": "holiday"}
    monkeypatch.setattr(ws, "market_day_status", lambda mk, d, now, state=None: kind["v"])
    st = {"market": "KR", "ticker": "000001"}
    base = _t("2026-10-05T18:00").timestamp()
    c = hn.cum_lookup(base, _t("2026-10-12T08:00"))(st)                          # 10-07 하루치 없음
    assert c["ka"] == pytest.approx(1.01 * 1.03) and c["n"] == 3 and c["asof"] == "2026-10-08"
    assert c["hol"] == ["2026-10-07"]                                            # 0 으로 센 세션을 툴팁이 이름으로 댄다
    assert "2026-10-07 휴장 추정(원천 값이 직전 세션과 같았음) — 등락 0 으로 셈" in hn._close_tip({"at": "x", **c})
    kind["v"] = "missing"
    c = hn.cum_lookup(base, _t("2026-10-12T08:00"))(st)
    assert c["ka"] is None and "2026-10-07" in c["why"] and c["wait"] == []


def test_cum_plan_failure_is_not_reported_as_an_unsupported_market(hn, monkeypatch):
    """하루치를 받는 시장인데 계산이 깨지면 '받지 않는 시장' 이라 적지 않는다 — 처방이 다르다(#82)."""
    import bot.world_sectors as ws
    monkeypatch.setattr(ws, "load_state", lambda: 1 / 0)
    c = hn.cum_lookup(_t("2026-10-05T18:00").timestamp(), _t("2026-10-12T08:00"))({"market": "KR", "ticker": "000001"})
    assert c == {"ka": None, "n": 0, "asof": None, "wait": [], "hol": [], "src": "tv", "need": None,
                 "why": "장종료가 등락 계산 실패(봇 로그 참고)"}


def test_sold_stock_on_latest_day_keeps_its_previous_day_change(hn, ws_snaps):
    """지금 자산 기록에 없는 종목(판 종목)의 오늘 카드는 요약에 없으니 장종료가 누적이 없다 — 빈칸으로 두지 않고 그날
    기준 전날 하루 등락을 날짜와 함께 싣는다(#43)."""
    ws_snaps("KR", "2026-10-08", [("KRX:000660", 1.5)])
    rec = _rec({**_stock("KR:000660"), "name": "SK하이닉스", "value": 2_000_000.0})
    html = hn.render_page([rec], {}, _t("2026-10-12T08:00"), gen="GEN", portfolio=_pf("2026-10-08T08:00"))
    head = html.split("data-hncard='1'")[1].split("class='hn-chg")[1].split("</span>")[0]
    assert "(+1.50% · +3만원)" in head and "2026-10-08 종가 기준 등락률" in head and "장종료가 누적" not in head


def test_add_new_holdings_uses_cached_etf_list_and_merges_source_counts(hn, monkeypatch):
    seen = []
    monkeypatch.setattr(hn, "_us_etf", lambda **kw: seen.append(kw) or set())
    model = _model(_h("종목1", "000001", "KR"))
    rec = {"date": "2026-10-12", "stocks": [], "universe": {"n": 0},
           "sources": {"naver": 5, "fail": 1, "fail_reasons": {"x": 1}, "telegram": {"matched": 2}}}
    f = _fetcher({}, ([_it("종목1 소식", "2026-10-12T06:00:00+09:00", link="https://p/1")], "naver", ""))
    hn.add_new_holdings(rec, _t("2026-10-12T13:00"), records=[], model=model, fetch=f, body=lambda l: "",
                        pace=0, ko=lambda c: "")
    assert seen and all(k.get("cache_only") for k in seen)                      # 15분마다 목록 원천을 안 두드린다
    assert rec["sources"]["naver"] == 6 and rec["sources"]["fail"] == 1


def test_nav_has_no_daily_byte_link(hn):
    """사용자 2026-10-14 "대시보드 창에서 daily bite 는 빼주고" — 이 페이지 위 nav 에서 뺀다."""
    html = hn.render_page([], {}, _t("2026-10-12T09:00"), gen="GEN", portfolio={})
    nav = html.split('<div class="nav">')[1].split("</div>")[0]
    assert "daily_byte.html" not in nav and "Daily Byte" not in nav and "dart_feed.html" in nav


def test_stat_shows_previous_recorded_day_next_to_latest(hn):
    """사용자 2026-10-14 — '최신기준일 옆에 그 전 기준일(전에 기록된 일자)도 명시'. 연속이 아닌 날도
    그대로 '전에 기록된 날' 이다(10/14 옆 10/09)."""
    import re
    a, b, c = _page_rec(), _page_rec(), _page_rec()
    a["date"], b["date"], c["date"] = "2026-10-14", "2026-10-09", "2026-10-08"
    html = hn.render_page([c, a, b], {}, _t("2026-10-14T09:00"), gen="GEN")
    m = re.search(r"최신 기준일 · 전 기록일</div><div class='v'>(.*?)</div>", html)
    assert m and m.group(1) == "2026-10-14<span class='prev'>· 전 2026-10-09</span>", m
    one = hn.render_page([a], {}, _t("2026-10-14T09:00"), gen="GEN")
    assert "<span class='prev'>· 전 없음</span>" in one
    assert ".stat .v .prev{" in html     # 클래스만 쓰고 CSS 가 없으면 본문 크기로 뜬다(#201)
