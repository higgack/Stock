"""Korean-language news fetcher via Naver Open API (검색 / 뉴스).

Why this exists: Alpha Vantage and yfinance's news fields cover US
listings well but barely touch KR. Without a Korean news source the
news + sentiment analysts hit empty-data and either silent-degrade
to "data not found" sentences or fall through to the fail-fast
guard. Naver fills the gap — 25,000 free calls/day is far beyond
what a single-user bot needs.

Auth: two env vars NAVER_CLIENT_ID + NAVER_CLIENT_SECRET set in
~/stock/.env. Missing keys ⇒ the module silently returns None and
the analyzer continues without the KR news block.

Cache: per (query, today) at ~/.tradingagents/cache/naver_news/
with 4h TTL. News refreshes hourly upstream but 4h cache is fine
for our use case (5-day-horizon analysis bot, not real-time feed).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.naver_news")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_news"
_CACHE_TTL_HOURS = 4
_API_URL = "https://openapi.naver.com/v1/search/news.json"
_TIMEOUT = 10

# Naver returns titles / descriptions with <b>...</b> highlight tags
# around query matches plus HTML entities — strip both before the LLM
# sees them. The pattern is intentionally narrow (only the few tag /
# entity forms Naver actually emits) to avoid clobbering legit content.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&apos;": "'",
}

# Map publisher domains to short Korean labels so the prompt reads as
# "한경 / 매경 / 연합" rather than "hankyung.com / mk.co.kr / yna.co.kr".
# Domains we don't have a label for fall through to bare domain.
_SOURCE_LABELS = {
    "hankyung.com": "한경",
    "mk.co.kr": "매경",
    "chosun.com": "조선",
    "donga.com": "동아",
    "joongang.co.kr": "중앙",
    "edaily.co.kr": "이데일리",
    "fnnews.com": "파이낸셜뉴스",
    "etoday.co.kr": "이투데이",
    "newspim.com": "뉴스핌",
    "newsis.com": "뉴시스",
    "yna.co.kr": "연합뉴스",
    "yonhapnews.co.kr": "연합뉴스",
    "asiae.co.kr": "아시아경제",
    "sedaily.com": "서울경제",
    "hankooki.com": "한국일보",
    "khan.co.kr": "경향",
    "munhwa.com": "문화일보",
    "hani.co.kr": "한겨레",
    "businesspost.co.kr": "비즈니스포스트",
    "thebell.co.kr": "더벨",
    "biz.heraldcorp.com": "헤럴드경제",
    "heraldcorp.com": "헤럴드",
    "naver.com": "네이버",
    "zdnet.co.kr": "ZDNet",
    "etnews.com": "전자신문",
    "inews24.com": "아이뉴스24",
}


def _decode_html(text: str) -> str:
    """Strip <b>highlight</b> tags + HTML entities from Naver response
    strings. Conservative — doesn't try to handle every HTML edge case,
    just what Naver actually emits."""
    if not text:
        return ""
    out = _HTML_TAG_RE.sub("", text)
    for entity, replacement in _HTML_ENTITIES.items():
        out = out.replace(entity, replacement)
    return out.strip()


def _source_label(link: str) -> str:
    """Best-effort source name from a publisher URL. Walks the host
    name from full to shorter suffix so 'biz.heraldcorp.com' matches
    its own dict entry first, while 'news.mk.co.kr' falls through to
    the 'mk.co.kr' entry. Strips a leading 'www.' before the walk
    because every publisher uses it equivalently."""
    if not link:
        return ""
    from urllib.parse import urlparse
    try:
        netloc = (urlparse(link).netloc or "").lower()
    except Exception:
        return ""
    if not netloc:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    parts = netloc.split(".")
    # Walk from longest to shortest suffix.
    for n in range(len(parts), 1, -1):
        candidate = ".".join(parts[-n:])
        if candidate in _SOURCE_LABELS:
            return _SOURCE_LABELS[candidate]
    return netloc


def fetch_news(query: str, days_back: int = 28, max_items: int = 10) -> Optional[list[dict]]:
    """Naver News search for `query`, sorted newest-first.

    Args:
        query: Korean company name (e.g. '삼성전자'). Caller is
            responsible for mapping ticker → name via DART.
        days_back: filter window. Naver's `sort=date` returns recent
            articles first; we still apply a date cutoff in case the
            corpus is sparse and we'd otherwise hand the LLM
            month-old articles as "recent."
        max_items: cap on items returned. Naver allows up to 100 per
            call; 10 is the right cap for our prompt budget (each
            item is ~80 chars in the formatted block).

    Returns:
        list of {date, title, source, link, summary} dicts, newest
        first. None when the API key is missing, the call fails, or
        nothing matches. Cached per (query, today) for 4h.
    """
    if not (query and query.strip()):
        return None

    from bot.env_keys import env_key as _env_key
    client_id = _env_key("NAVER_CLIENT_ID")
    client_secret = _env_key("NAVER_CLIENT_SECRET")
    if not (client_id and client_secret):
        log.warning("naver: NAVER_CLIENT_ID/SECRET missing — news unavailable")
        return None

    # Disk cache check
    safe_query = re.sub(r"[^\w가-힣]", "_", query)[:50]
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"news_{safe_query}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("naver: cache read failed: %s", exc)

    try:
        resp = requests.get(
            _API_URL,
            params={
                "query": query,
                "display": min(max_items * 2, 100),  # over-fetch to allow date filter
                "start": 1,
                "sort": "date",
            },
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.warning("naver: fetch failed for %r: %s", query, exc)
        return None

    items = payload.get("items") or []
    cutoff = datetime.now() - timedelta(days=days_back)
    out: list[dict] = []
    for it in items:
        if len(out) >= max_items:
            break
        # pubDate format: "Mon, 15 May 2026 14:23:00 +0900"
        pub_date_str = ""
        try:
            pub_dt = datetime.strptime(it["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
            if pub_dt.replace(tzinfo=None) < cutoff:
                continue
            pub_date_str = pub_dt.strftime("%Y-%m-%d")
        except Exception:
            pass
        link = it.get("originallink") or it.get("link") or ""
        out.append({
            "date": pub_date_str,
            "title": _decode_html(it.get("title", "")),
            "source": _source_label(link),
            "link": link,
            "summary": _decode_html(it.get("description", "")),
        })

    # Cache (even an empty list — saves a round-trip for confirmed-empty queries)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        log.warning("naver: cache write failed: %s", exc)

    return out


def has_recent_korean_news(query: str) -> bool:
    """Quick yes/no for the pre-flight gate: is there at least one
    Naver-indexed Korean news article for this query in the last
    28 days? Conservative on error (returns False so we don't
    promise news we can't deliver)."""
    items = fetch_news(query, days_back=28, max_items=1)
    return bool(items)


def format_news_for_prompt(items: list[dict]) -> str:
    """Render the news list as a tight bullet block for prompt
    injection. Each line ~80 chars; 10 items × 80 ≈ 800-char budget."""
    if not items:
        return ""
    lines = [f"한국어 뉴스 (네이버 검색, 최근 4주 {len(items)}건, 최신순):"]
    for it in items:
        date_s = it.get("date", "") or "?"
        title = (it.get("title") or "").strip()
        source = it.get("source") or ""
        src_part = f"[{source}] " if source else ""
        # Truncate title at ~120 chars so very long headlines don't blow the budget
        if len(title) > 120:
            title = title[:117] + "..."
        lines.append(f"  • {date_s}: {src_part}{title}")
    return "\n".join(lines)
