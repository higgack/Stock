"""Naver Finance 종목별 리서치 리포트 scraper.

한경 컨센서스(consensus.hankyung.com) 가 2026 초 JS 렌더링으로 전환되어
정적 HTML scrape 불가 → Naver Finance 종목 리서치 목록을 대체 소스로 활용.

URL: finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode=NNNNNN

키 불필요 (static HTML). 12h 디스크 캐시. 같은 {date, broker, rating,
target, title} 스키마를 반환해 한경 → Naver 교체가 dashboard 와 호환.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.naver_research")

_BASE_URL = "https://finance.naver.com/research/company_list.naver"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_research"
_CACHE_TTL_HOURS = 12
_HTTP_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "Referer": "https://finance.naver.com/research/company_list.naver",
}

_RATING_TO_DIRECTION = {
    "매수": "buy", "Buy": "buy", "BUY": "buy", "강력매수": "buy",
    "보유": "hold", "Hold": "hold", "HOLD": "hold", "중립": "hold",
    "Neutral": "hold", "Marketperform": "hold", "Market Perform": "hold",
    "매도": "sell", "Sell": "sell", "SELL": "sell",
    "비중축소": "sell", "Underweight": "sell",
    "비중확대": "buy", "Overweight": "buy",
    "Trading Buy": "buy", "Outperform": "buy",
    "Not Rated": "",
}


def _normalize_code(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    code = ticker.upper().split(".")[0]
    if code.isdigit() and len(code) == 6:
        return code
    return None


def _fetch_html(code: str) -> Optional[str]:
    """Fetch Naver Finance research list page for a stock code."""
    params = {"searchType": "itemCode", "itemCode": code}
    try:
        resp = requests.get(_BASE_URL, params=params, headers=_HEADERS,
                            timeout=_HTTP_TIMEOUT)
        resp.encoding = "euc-kr"
        if resp.status_code != 200 or not resp.text:
            return None
        if len(resp.text) < 500:
            return None
        return resp.text
    except Exception as exc:
        log.warning("naver_research: fetch failed for %s: %s", code, exc)
        return None


def _cell_texts(row_html: str) -> list[str]:
    """Strip a <tr> into a list of plain-text <td>/<th> cell values."""
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.I)
    out = []
    for c in cells:
        c = re.sub(r'<img\s[^>]*?\balt=["\']([^"\']*)["\'][^>]*/?>', r' \1 ', c, flags=re.I)
        txt = re.sub(r"<[^>]+>", " ", c)
        txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
        out.append(" ".join(txt.split()).strip())
    return out


def _parse_rows(html: str, cutoff) -> list[dict]:
    """Parse research report rows from the Naver Finance page.

    Naver Finance research list has columns:
    종목명 | 리포트 제목 | 증권사 | 날짜(YY.MM.DD) | 목표가 | 투자의견

    Column order can vary, so we use pattern matching (same approach
    as hk_consensus_client tolerant parser)."""
    rows: list[dict] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        cells = _cell_texts(row_html)
        if len(cells) < 3:
            continue

        date_str = None
        for c in cells:
            # Naver uses YY.MM.DD format
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", c)
            if m:
                yy, mm, dd = m.group(1), m.group(2), m.group(3)
                year = int(yy) + 2000
                date_str = f"{year}-{mm}-{dd}"
                break
        if not date_str:
            continue
        try:
            from datetime import datetime
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue

        target_val = None
        for c in cells:
            if date_str.replace("-", "") in c.replace(",", "").replace(".", ""):
                continue
            m = re.search(r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+)(?![0-9])", c)
            if m:
                target_val = float(m.group(1).replace(",", ""))
                break

        rating_raw = ""
        _RATING_KEYWORDS = (
            "강력매수", "비중확대", "비중축소", "매수", "매도", "보유", "중립",
            "Trading Buy", "Outperform", "Overweight", "Marketperform",
            "Market Perform", "Underweight", "Buy", "Hold", "Sell", "Neutral",
            "Not Rated",
        )
        for c in cells:
            for kw in _RATING_KEYWORDS:
                if kw.lower() in c.lower():
                    rating_raw = kw
                    break
            if rating_raw:
                break

        broker = ""
        _BROKER_RE = re.compile(r"증권|투자|자산운용|Securities|Investment|리서치", re.I)
        for c in cells:
            if c and len(c) <= 22 and _BROKER_RE.search(c):
                broker = c
                break

        title = ""
        for c in sorted(cells, key=len, reverse=True):
            if c and c != broker and not re.fullmatch(r"[\d,.\-/\s]+", c):
                title = c[:80]
                break

        rows.append({
            "title": title,
            "broker": broker,
            "analyst": "",
            "target": target_val,
            "rating": rating_raw,
            "date": date_str,
        })
    return rows


def fetch_research(ticker: str, days_back: int = 90) -> Optional[dict]:
    """Scrape Naver Finance 종목 리서치 리포트 목록.

    Returns dict with same shape as hk_consensus_client.fetch_consensus:
    {target_price, rating, analyst_count, last_report_date, report_count,
     reports: [...]} or None."""
    code = _normalize_code(ticker)
    if not code:
        return None

    cache_key = f"naver_research_{code}_{date.today().isoformat()}.json"
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                return cached if cached else None
        except Exception as exc:
            log.warning("naver_research: cache read failed for %s: %s", code, exc)

    html = _fetch_html(code)
    if not html:
        return None

    today = date.today()
    cutoff = today - timedelta(days=days_back)
    rows = _parse_rows(html, cutoff)

    if not rows:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("null")
        except Exception:
            pass
        log.info("naver_research: no recent reports for %s (%d-day window)", code, days_back)
        return None

    target_vals = [r["target"] for r in rows if r["target"]]
    avg_target = sum(target_vals) / len(target_vals) if target_vals else None

    rating_counts: dict[str, int] = {}
    for r in rows:
        direction = _RATING_TO_DIRECTION.get(r["rating"], "")
        if direction:
            rating_counts[direction] = rating_counts.get(direction, 0) + 1
    dominant_rating = max(rating_counts, key=rating_counts.get) if rating_counts else ""

    distinct_analysts = len({(r["broker"], r["analyst"]) for r in rows})
    last_date = max(r["date"] for r in rows)

    result = {
        "target_price": avg_target,
        "rating": dominant_rating,
        "analyst_count": distinct_analysts,
        "last_report_date": last_date,
        "report_count": len(rows),
        "reports": sorted(rows, key=lambda r: r["date"], reverse=True)[:15],
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        log.warning("naver_research: cache write failed for %s: %s", code, exc)

    return result
