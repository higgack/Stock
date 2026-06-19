"""해외(worldstock) 기업개요 + 뉴스 — 네이버 한국어 native (₩0, 번역 불요).

사용자 2026-06-16: 종목분석 detail 의 개요·뉴스를 yfinance(영문)+Gemini 번역 대신,
네이버가 **한국어로 주는** worldstock 개요/뉴스로 교체 — 빠르고 번역비 ₩0. 실패 시
호출부가 기존(yfinance+translate) 폴백. KR(국내)은 기존 DART/네이버 경로 유지.

reutersCode 기반(US AAPL.O / JP 7203.T / HK 0700.HK / CN 600519.SS) — world_quote
가 발견·캐시한 rc 재사용. 디스크 캐시(개요 30일·뉴스 12h)로 bulk regen(_render_detail
전 아카이브 루프) 부하 차단 — 종목당 1회만 네이버 호출.

엔드포인트(2026-06-16 VM 확인):
  개요: api.stock.naver.com/stock/<rc>/overview → summary(한국어, <br> 포함)
  뉴스: api.stock.naver.com/news/worldStock/<rc>?pageSize=&page=1
        → [{tit(한국어 제목), ohnm(언론사), dt(YYYYMMDDHHMMSS), subcontent}]
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("bot.naver_overview")

_OVERVIEW_URL = "https://api.stock.naver.com/stock/{rc}/overview"
_NEWS_URL = "https://api.stock.naver.com/news/worldStock/{rc}?pageSize=20&page=1"
_OV_TTL = 30 * 86400     # 개요 거의 불변 — 30일
_NEWS_TTL = 12 * 3600    # 뉴스 — 12시간


def _rc_for(ticker: str) -> str | None:
    """ticker → reutersCode. world_quote 가 시세 fetch 때 발견·캐시한 rc 우선,
    없으면 1회 fetch_world_quote 로 발견(그 자체로 캐시). KR/TW 는 호출부가 제외."""
    try:
        from bot import world_quote
    except Exception:
        return None
    t = (ticker or "").strip().upper()
    if not t:
        return None
    rc = world_quote._rc_cache.get(t)
    if rc:
        return rc
    try:
        world_quote.fetch_world_quote(t)   # 발견 시 _rc_cache 적재
        return world_quote._rc_cache.get(t)
    except Exception:
        return None


def _strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def fetch_naver_overview(ticker: str) -> str | None:
    """네이버 한국어 기업개요(summary). 30일 디스크 캐시. 실패 시 None(호출부 폴백)."""
    from bot.finviz_client import _cache_write, _cached
    t = (ticker or "").strip().upper()
    if not t:
        return None
    ck = f"naver_overview_{t}.json"
    hit = _cached(ck, ttl=_OV_TTL)
    if isinstance(hit, dict):
        return hit.get("summary") or None
    rc = _rc_for(t)
    if not rc:
        return None
    try:
        import requests

        from bot.naver_quote import _HDRS
        r = requests.get(_OVERVIEW_URL.format(rc=rc), headers=_HDRS, timeout=8)
        if r.status_code != 200:
            return None
        summary = _strip_html((r.json() or {}).get("summary") or "")
        if summary:
            _cache_write(ck, {"summary": summary})   # 성공만 캐시(transient 실패 미pin)
            return summary
    except Exception as exc:
        log.debug("naver overview %s: %s", t, exc)
    return None


def fetch_naver_world_news(ticker: str, max_items: int = 10) -> list[dict] | None:
    """네이버 한국어 해외뉴스 [{title, publisher, date, subcontent}]. 12h 캐시.
    실패/빈 결과 시 None(호출부가 stored+translate 폴백)."""
    from bot.finviz_client import _cache_write, _cached
    t = (ticker or "").strip().upper()
    if not t:
        return None
    ck = f"naver_worldnews_v3_{t}.json"   # v3: 기사 deep-link 폐기(2026-06-19) — 본문요약 인라인 + 종목 페이지 링크
    hit = _cached(ck, ttl=_NEWS_TTL)
    if isinstance(hit, dict) and hit.get("items"):
        return hit["items"][:max_items]
    rc = _rc_for(t)
    if not rc:
        return None
    try:
        import requests

        from bot.naver_quote import _HDRS
        r = requests.get(_NEWS_URL.format(rc=rc), headers=_HDRS, timeout=8)
        if r.status_code != 200:
            return None
        items: list[dict] = []
        for it in (r.json() or []):
            tit = (it.get("tit") or "").strip()
            if not tit:
                continue
            dt = str(it.get("dt") or "")
            date = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if len(dt) >= 8 else ""
            # 링크 — worldStock 뉴스 item 엔 기사 URL 필드가 **없다** (VM probe
            # 2026-06-19 ICHR: keys=type/subcontent/thumbUrl/oid/ohnm/aid/tit/dt,
            # oid='fnGuide' 고정·aid 만 상이). 옛 worldstock/news/{oid}/{aid} 는
            # deep-link 미작동 — 네이버 SPA 가 인식 못 해 **모든 기사가 일반 시장
            # 페이지로 폴백**(사용자 2026-06-19 '어떤 뉴스든 다 같은데로'). FnGuide 가
            # 통신사(로이터 등) 와이어를 한국어로 요약한 것이라 표준 기사 페이지가
            # 부재. 해결: (a) 전체 요약(subcontent)을 카드에 **인라인** 노출(클릭 없이
            # 읽힘) + (b) 제목은 종목의 네이버 worldstock 페이지로 링크(신뢰 가능,
            # 깨진 deep-link 안 만듦). _link 우선순위 필드가 생기면 그걸 사용.
            link = ""
            for _lk in ("linkUrl", "link", "url", "newsUrl", "bodyUrl", "origin"):
                _v = it.get(_lk)
                if isinstance(_v, str) and _v.startswith("http"):
                    link = _v
                    break
            if not link and rc:
                link = f"https://m.stock.naver.com/worldstock/stock/{rc}/total"
            items.append({
                "title": tit,
                "publisher": (it.get("ohnm") or "").strip(),
                "date": date,
                "subcontent": (it.get("subcontent") or "").strip(),
                "link": link,
            })
        if items:
            _cache_write(ck, {"items": items})
            return items[:max_items]
    except Exception as exc:
        log.debug("naver worldnews %s: %s", t, exc)
    return None
