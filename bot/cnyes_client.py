"""鉅亨網 (Anue / cnYES) news scraper for TW stocks.

TW analogue to bot/naver_news_client.py (KR) and bot/kabutan_news.py
(JP). 鉅亨網 is Taiwan's largest financial news portal, with a
per-ticker news page (https://news.cnyes.com/tag/<code>) that
aggregates 繁體中文 articles from 經濟日報, 工商時報, MoneyDJ, and
the cnyes desk itself.

Why this exists: yfinance .news returns English wire stories for TW
large-caps (TSMC, Hon Hai) but rarely covers TW-only / mid-cap names,
and English news lags 繁體中文 by 1-3 days for TW-domestic events
(法說會 calendars, monthly 營收 publication, etc.). 鉅亨網 fills that
gap.

Auth: none (HTML scrape).
Cache: ~/.tradingagents/cache/cnyes_news/, 4h TTL — same pattern as
Naver / Kabutan. News refreshes hourly upstream; 4h slack is fine for
a 5-day-horizon analyzer.

Returns the same {date, title, source, link, summary} shape that
Naver / Kabutan use, so downstream code treats all three pipes
interchangeably.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.cnyes")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "cnyes_news"
_CACHE_TTL_HOURS = 4
_URL_TMPL = "https://news.cnyes.com/tag/{code}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}
_TIMEOUT = 10


def _ticker_to_code(ticker: str) -> Optional[str]:
    """Convert `.TW` / `.TWO` ticker to the bare 4-6 digit code 鉅亨網
    uses as URL slug. e.g. '2330.TW' → '2330', '8299.TWO' → '8299'."""
    if not ticker:
        return None
    code = ticker.upper().split(".")[0]
    if code.isdigit() and len(code) in (4, 5, 6):
        return code
    return None


# cnyes article cards on the tag page look like:
#   <a href="/news/id/<NEWS_ID>">
#     <h3>...title...</h3>
#     <time>YYYY-MM-DD HH:MM</time>
#     <span>분류 / source</span>
#   </a>
# but the HTML layout is React-rendered so we match a permissive
# pattern. Year-month-day timestamps are explicit (no implicit year
# resolution like Kabutan needed).
_ARTICLE_RE = re.compile(
    r'<a[^>]+href="(/news/id/\d+[^"]*)"[^>]*>'
    r'.*?'
    r'<h3[^>]*>\s*(.+?)\s*</h3>'
    r'.*?'
    r'(\d{4}-\d{1,2}-\d{1,2})'
    r'.*?'
    r'</a>',
    re.DOTALL,
)


def fetch_news(stock_code: str, days_back: int = 28, max_items: int = 10) -> Optional[list[dict]]:
    """Scrape 鉅亨網 per-ticker news for a TW stock.

    Args:
        stock_code: `.TW` / `.TWO` ticker.
        days_back: filter window in days (cnyes returns ~30 most-recent
            per request; we clip to days_back).
        max_items: cap on returned items.

    Returns:
        list of {date, title, source, link, summary} dicts, newest-first.
        None on parse failure or empty corpus.
    """
    code = _ticker_to_code(stock_code)
    if not code:
        return None

    today = date.today()
    cache_file = _CACHE_DIR / f"news_{code}_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("cnyes: cache read failed: %s", exc)

    try:
        resp = requests.get(
            _URL_TMPL.format(code=code), headers=_HEADERS, timeout=_TIMEOUT,
        )
        resp.encoding = "utf-8"
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        log.warning("cnyes: fetch failed for %s: %s", code, exc)
        return None

    cutoff = today - timedelta(days=days_back)
    items: list[dict] = []
    seen_ids: set[str] = set()
    for m in _ARTICLE_RE.finditer(html):
        if len(items) >= max_items:
            break
        href = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        date_s = m.group(3).strip()
        try:
            y, mo, dd = date_s.split("-")
            d = date(int(y), int(mo), int(dd))
        except Exception:
            continue
        if d < cutoff:
            continue
        # cnyes article ID for dedup
        news_id_match = re.search(r"/news/id/(\d+)", href)
        news_id = news_id_match.group(1) if news_id_match else href
        if news_id in seen_ids:
            continue
        seen_ids.add(news_id)
        if not title:
            continue
        full_link = (
            href if href.startswith("http") else f"https://news.cnyes.com{href}"
        )
        items.append({
            "date": d.isoformat(),
            "title": title,
            "source": "鉅亨網",  # cnyes aggregates many sources; we
                                  # report cnyes as the platform since
                                  # the article comes through their feed.
            "link": full_link,
            "summary": "",
        })

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(items, ensure_ascii=False))
    except Exception as exc:
        log.warning("cnyes: cache write failed: %s", exc)

    return items if items else None


def has_recent_taiwanese_news(stock_code: str) -> bool:
    """Pre-flight gate parallel to has_recent_korean_news /
    has_recent_japanese_news. Returns True iff cnyes has at least one
    article in the last 28 days for this ticker."""
    items = fetch_news(stock_code, days_back=28, max_items=1)
    return bool(items)


def format_news_for_prompt(items: list[dict]) -> str:
    """Render the news list as a prompt block. Same shape as Naver /
    Kabutan equivalents — single line per article with date / source /
    title. Truncates title at 120 chars to bound prompt budget."""
    if not items:
        return ""
    lines = [f"繁體中文 뉴스 (鉅亨網, 최근 4주 {len(items)}건, 최신순):"]
    for it in items:
        date_s = it.get("date", "") or "?"
        title = (it.get("title") or "").strip()
        source = it.get("source") or ""
        src_part = f"[{source}] " if source else ""
        if len(title) > 120:
            title = title[:117] + "..."
        lines.append(f"  • {date_s}: {src_part}{title}")
    return "\n".join(lines)
