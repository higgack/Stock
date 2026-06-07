"""Keyless news fallback via Google News RSS.

Why this exists: the Naver Open API (KR news primary) needs valid
`NAVER_CLIENT_ID`/`SECRET` credentials, and Kabutan / cnyes / AKShare
(JP/TW/CN) are HTML scrapes that occasionally move or block. When the
market-specific source returns nothing — an invalid Naver key
(errorCode 024, surfaced 2026-06-08 on NAVER 035420), a moved scrape
page, a rate-limit — Google News RSS still serves recent articles for
the same query with **no API key**. Universal: works for every
language via the hl / gl / ceid parameters.

Schema matches naver_news_client / kabutan_news so callers can swap it
in transparently: list of {date, title, source, link, summary}.

Cache: per (query, lang, today) at ~/.tradingagents/cache/google_news/
with a 4h TTL — same cadence as the Naver client.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

log = logging.getLogger("bot.google_news")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "google_news"
_CACHE_TTL_HOURS = 4
_TIMEOUT = 10
# Browser-ish UA — Google News RSS serves bare requests but a UA avoids
# the occasional bot-challenge on datacenter IPs.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
}

# market suffix → Google News locale (hl=interface lang, gl=country,
# ceid=country:lang). Defaults to Korean since KR is the surfaced case;
# callers pass an explicit locale for other markets.
_LOCALE = {
    "KR": ("ko", "KR", "KR:ko"),
    "JP": ("ja", "JP", "JP:ja"),
    "TW": ("zh-TW", "TW", "TW:zh-Hant"),
    "CN": ("zh-CN", "CN", "CN:zh-Hans"),
    "HK": ("zh-HK", "HK", "HK:zh-Hant"),
    "US": ("en-US", "US", "US:en"),
}


def locale_for_market(market: str) -> tuple[str, str, str]:
    """Return the (hl, gl, ceid) Google News locale for a market key."""
    return _LOCALE.get((market or "").upper(), _LOCALE["US"])


def _strip_source_suffix(title: str) -> tuple[str, str]:
    """Google News titles are 'Article Title - Publisher'. Split on the
    last ' - ' so we can surface the publisher separately. Returns
    (title, publisher); publisher '' when no separator is present."""
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        # Guard against false splits inside the headline — the publisher
        # segment is short and has no sentence punctuation.
        if head and tail and len(tail) <= 40:
            return head.strip(), tail.strip()
    return title.strip(), ""


def fetch_news(
    query: str,
    days_back: int = 28,
    max_items: int = 10,
    lang: str = "ko",
    country: str = "KR",
    ceid: str | None = None,
) -> Optional[list[dict]]:
    """Google News RSS search for ``query``, newest-first.

    Args:
        query: search term (e.g. '네이버'). Caller maps ticker → name.
        days_back: drop articles older than this many days.
        max_items: cap on returned items.
        lang / country / ceid: Google News locale. Defaults to Korean;
            use ``locale_for_market`` for other markets.

    Returns list of {date, title, source, link, summary} dicts, or None
    when the query is empty, the fetch fails, or nothing matches. Cached
    per (query, lang, today) for 4h. No API key.
    """
    if not (query and query.strip()):
        return None

    ceid = ceid or f"{country}:{lang}"
    url = (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + f"&hl={lang}&gl={country}&ceid={quote(ceid)}"
    )

    safe_query = re.sub(r"[^\w가-힣]", "_", query)[:50]
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"gnews_{safe_query}_{lang}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("google_news: cache read failed: %s", exc)

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log.warning("google_news: fetch failed for %r: %s", query, exc)
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    out: list[dict] = []
    for item in root.iter("item"):
        if len(out) >= max_items:
            break
        title_raw = (item.findtext("title") or "").strip()
        if not title_raw:
            continue
        link = (item.findtext("link") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        pub_date_str = ""
        if pub_raw:
            try:
                pub_dt = datetime.strptime(pub_raw, "%a, %d %b %Y %H:%M:%S %Z")
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                pub_date_str = pub_dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        title, publisher = _strip_source_suffix(title_raw)
        # <source> element carries the publisher name when present.
        src_el = item.find("source")
        if src_el is not None and (src_el.text or "").strip():
            publisher = src_el.text.strip()
        out.append({
            "date": pub_date_str,
            "title": title,
            "source": publisher,
            "link": link,
            "summary": "",
        })

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        log.warning("google_news: cache write failed: %s", exc)

    return out or None
