"""Kabutan (株探) Japanese-language news scraper.

Why this exists: Alpha Vantage / yfinance's news coverage of JP names
is thin, English-only, and dated. For a `.T` ticker that needs short-
horizon (5-day) commentary, Japanese-language news from Reuters Japan,
日経, 東洋経済, 株探独自 etc. is the primary signal source — Kabutan
aggregates them on its per-ticker news page at
`kabutan.jp/stock/news?code=NNNN`.

No API key; pure HTML scrape. 25/day Naver-style budget doesn't apply
here, but we still cache per (code, today) for 4h to be polite (and
to avoid hammering Kabutan on retries).

Auth: none.
Cache: ~/.tradingagents/cache/kabutan_news/, 4h TTL.

Returns the same `{date, title, source, link, summary}` shape that
naver_news_client.fetch_news uses so downstream code can treat the
two pipes interchangeably.
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

log = logging.getLogger("bot.kabutan_news")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "kabutan_news"
_CACHE_TTL_HOURS = 4
_URL_TMPL = "https://kabutan.jp/stock/news?code={code}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
}
_TIMEOUT = 10

# Kabutan news rows on the per-ticker page follow this structure
# (verified across multiple ticker pages):
#   <tr>
#     <td>MM/DD HH:MM</td>
#     <td>カテゴリ</td>
#     <td><a href="/news/marketnews/...">タイトル</a></td>
#   </tr>
# The year is implicit (current year unless the date is in the future,
# in which case it's last year). We parse with a permissive regex; the
# layout is stable but defensive matching tolerates whitespace drift.
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*"
    r"<td[^>]*>\s*(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?\s*</td>\s*"
    r"<td[^>]*>\s*([^<]*?)\s*</td>\s*"
    r"<td[^>]*>\s*<a\s+href=\"([^\"]+)\"[^>]*>\s*(.+?)\s*</a>",
    re.DOTALL | re.IGNORECASE,
)

# Kabutan article source labels — most rows are 'kabutan 株探' for their
# own desk, but they syndicate from Reuters / Jiji / Bloomberg / 株探.
_SOURCE_LABELS = {
    "marketnews": "株探",
    "company": "企業",
    "newsflash": "速報",
    "report": "レポート",
}


def _resolve_year(month: int, day: int, today: date) -> int:
    """Pick the year for a (month, day) listing on Kabutan. They omit the
    year; today's-or-earlier dates are this year, future-looking dates
    rolled over from December are last year."""
    candidate_this_year = date(today.year, month, day) if _valid(today.year, month, day) else None
    if candidate_this_year and candidate_this_year <= today:
        return today.year
    return today.year - 1


def _valid(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _source_from_link(href: str) -> str:
    """Kabutan article URLs look like '/news/marketnews/2026/05/17/N12345'.
    Pull the category segment after '/news/' and map to a Japanese label."""
    m = re.search(r"/news/([^/]+)/", href)
    if not m:
        return "株探"
    cat = m.group(1).lower()
    return _SOURCE_LABELS.get(cat, "株探")


def fetch_news(stock_code: str, days_back: int = 28, max_items: int = 10) -> Optional[list[dict]]:
    """Scrape Kabutan's per-ticker news page.

    Args:
        stock_code: 4-digit Tokyo ticker (`.T` suffix stripped).
        days_back: filter window in days.
        max_items: cap on returned items (~80 chars each in the prompt
            block; 10 fits a normal budget).

    Returns:
        list of {date, title, source, link, summary} dicts, newest-first.
        None on parse failure or empty corpus.
    """
    code = (stock_code or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 4):
        return None

    today = date.today()
    cache_file = _CACHE_DIR / f"news_{code}_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("kabutan_news: cache read failed: %s", exc)

    try:
        resp = requests.get(
            _URL_TMPL.format(code=code), headers=_HEADERS, timeout=_TIMEOUT
        )
        resp.raise_for_status()
        html = resp.text
        if "ニュース" not in html and "news" not in html.lower():
            resp.encoding = "utf-8"
            html = resp.text
    except Exception as exc:
        log.warning("kabutan_news: fetch failed for %s: %s", code, exc)
        return None

    cutoff = today - timedelta(days=days_back)
    items: list[dict] = []
    for match in _ROW_RE.finditer(html):
        if len(items) >= max_items:
            break
        try:
            month = int(match.group(1))
            day = int(match.group(2))
            hour = int(match.group(3) or 0)
            minute = int(match.group(4) or 0)
        except (TypeError, ValueError):
            continue
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        year = _resolve_year(month, day, today)
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d < cutoff:
            continue
        href = match.group(6).strip()
        title = match.group(7).strip()
        # Strip stray inline HTML and entity references that may have
        # leaked through the title slot (Kabutan sometimes wraps a span
        # for breaking-news flair).
        title = re.sub(r"<[^>]+>", "", title)
        title = title.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        if not title:
            continue
        full_link = href if href.startswith("http") else f"https://kabutan.jp{href}"
        items.append({
            "date": d.isoformat(),
            "title": title,
            "source": _source_from_link(href),
            "link": full_link,
            "summary": "",  # Kabutan listing page only carries the title.
        })

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(items, ensure_ascii=False))
    except Exception as exc:
        log.warning("kabutan_news: cache write failed: %s", exc)

    return items if items else None


def has_recent_japanese_news(stock_code: str) -> bool:
    """Pre-flight gate parallel to has_recent_korean_news."""
    items = fetch_news(stock_code, days_back=28, max_items=1)
    return bool(items)


def format_news_for_prompt(items: list[dict]) -> str:
    """Render the news list as a tight bullet block for prompt injection."""
    if not items:
        return ""
    lines = [f"일본어 뉴스 (Kabutan, 최근 4주 {len(items)}건, 최신순):"]
    for it in items:
        date_s = it.get("date", "") or "?"
        title = (it.get("title") or "").strip()
        source = it.get("source") or ""
        src_part = f"[{source}] " if source else ""
        if len(title) > 120:
            title = title[:117] + "..."
        lines.append(f"  • {date_s}: {src_part}{title}")
    return "\n".join(lines)
