"""鉅亨網 (Anue / cnYES) consensus scraper for TW stocks.

TW analogue to bot/kabutan_consensus.py (JP) and bot/fnguide_consensus.py (KR).
Scrapes analyst target price + rating from the cnyes.com stock page.

Design constraints mirror kabutan_consensus.py:
- Multiple regex strategies per field for resilience to layout changes.
- Realistic Chrome User-Agent with zh-TW Accept-Language.
- 6-hour cache (consensus updates infrequently).
- Pure regex, no bs4.
- Graceful None on any failure — mid-caps without coverage degrade to
  '애널리스트 커버리지 없음' silently.

Rating mapping: 買進→매수, 持有→보유, 賣出→매도
(same unified schema as FnGuide / Kabutan downstream).

Rule applies to all TW analyses going forward.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.cnyes_consensus")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "cnyes_consensus"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_TTL_HOURS = 6

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": "https://www.cnyes.com/",
}
_TIMEOUT = 12

# Rating phrase → unified schema
_RATING_MAP = {
    "買進": "매수",
    "強力買進": "매수",
    "買入": "매수",
    "增持": "매수",
    "持有": "보유",
    "中立": "보유",
    "區間操作": "보유",
    "賣出": "매도",
    "減持": "매도",
    "賣進": "매도",
}


def _ticker_to_code(ticker: str) -> Optional[str]:
    """'2330.TW' → '2330'."""
    if not ticker:
        return None
    code = ticker.upper().split(".")[0]
    return code if code.isdigit() and 2 <= len(code) <= 6 else None


def _cache_key(code: str) -> Path:
    return _CACHE_DIR / f"{code}.json"


def _load_cache(code: str) -> Optional[dict]:
    p = _cache_key(code)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(data.get("fetched_at", "2000-01-01"))
        if (datetime.utcnow() - fetched).total_seconds() < _CACHE_TTL_HOURS * 3600:
            return data.get("payload")
    except Exception:
        pass
    return None


def _save_cache(code: str, payload: dict) -> None:
    try:
        _cache_key(code).write_text(
            json.dumps({"fetched_at": datetime.utcnow().isoformat(), "payload": payload},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("cnyes_consensus cache write failed: %s", exc)


def _parse_target(html: str) -> Optional[float]:
    """Extract analyst mean target price from cnyes HTML (TWD, per share)."""
    patterns = [
        r"目標價[^0-9]*([0-9,]+\.?[0-9]*)",
        r"目標股價[^0-9]*([0-9,]+\.?[0-9]*)",
        r'"consensus"[^}]*"targetPrice"[^0-9]*([0-9]+\.?[0-9]*)',
        r'targetPrice["\s:]+([0-9]+\.?[0-9]*)',
        r"平均目標價[^0-9]*([0-9,]+\.?[0-9]*)",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except (ValueError, IndexError):
                pass
    return None


def _parse_rating(html: str) -> Optional[str]:
    """Extract analyst consensus rating from cnyes HTML."""
    patterns = [
        r"分析師評等[^買持賣買增減]{0,30}(買進|強力買進|買入|增持|持有|中立|區間操作|賣出|減持)",
        r"整體評等[^買持賣買增減]{0,30}(買進|強力買進|買入|增持|持有|中立|區間操作|賣出|減持)",
        r'"rating"[^"]*"([^"]*(?:買進|買入|持有|賣出)[^"]*)"',
        r"consensus[^買持賣]{0,50}(買進|持有|賣出)",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            raw = m.group(1).strip()
            return _RATING_MAP.get(raw, raw)
    # Fallback: scan for explicit rating words near 評等/推薦
    for tw_word, kr_word in _RATING_MAP.items():
        if tw_word in html:
            return kr_word
    return None


def _parse_n_analysts(html: str) -> Optional[int]:
    """Extract number of analysts."""
    patterns = [
        r"([0-9]+)\s*(?:位|名|家)\s*分析師",
        r"分析師\s*([0-9]+)\s*(?:位|名)",
        r'"analystCount"[^0-9]*([0-9]+)',
        r'"numberOfAnalysts"[^0-9]*([0-9]+)',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def _parse_date(html: str) -> Optional[str]:
    """Extract most recent report date (YYYY-MM-DD)."""
    patterns = [
        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 2020 <= y <= 2030:
                    return f"{y:04d}-{mo:02d}-{d:02d}"
            except (ValueError, IndexError):
                pass
    return None


def fetch_consensus(stock_code: str) -> Optional[dict]:
    """Scrape cnyes consensus for a TW-listed stock.

    Args:
        stock_code: ticker with or without .TW suffix (e.g. '2330' or '2330.TW').

    Returns:
        dict with keys:
          - target_mean: float, TWD (per share) or None
          - rating: '매수' | '보유' | '매도' | None
          - n_analysts: int or None
          - last_report_date: 'YYYY-MM-DD' or None
        OR None when unreachable / not covered.
    """
    code = _ticker_to_code(stock_code)
    if not code:
        return None

    cached = _load_cache(code)
    if cached is not None:
        return cached

    # cnyes stock detail page embeds consensus in its JS bundle / HTML
    url = f"https://www.cnyes.com/twstock/{code}"
    try:
        time.sleep(0.5)  # polite delay
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        log.warning("cnyes_consensus fetch failed for %s: %s", code, exc)
        return None

    target    = _parse_target(html)
    rating    = _parse_rating(html)
    n_an      = _parse_n_analysts(html)
    last_date = _parse_date(html)

    if not any([target, rating, n_an]):
        # Page loaded but no consensus block — likely small-cap with no coverage
        log.debug("cnyes_consensus: no consensus data found for %s", code)
        return None

    payload = {
        "target_mean":      target,
        "rating":           rating,
        "n_analysts":       n_an,
        "last_report_date": last_date,
    }
    _save_cache(code, payload)
    return payload
