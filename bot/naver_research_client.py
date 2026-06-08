"""Naver Finance 종목별 리서치 리포트 scraper.

한경 컨센서스(consensus.hankyung.com) 가 2026 초 JS 렌더링으로 전환되어
정적 HTML scrape 불가 → Naver Finance 종목 리서치 목록을 대체 소스로 활용.

URL: finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode=NNNNNN

키 불필요 (static HTML). 12h 디스크 캐시. 같은 {date, broker, rating,
target, title} 스키마를 반환해 한경 → Naver 교체가 dashboard 와 호환.

테이블 구조 (2026-06-08 강화):
  Naver Finance research list 는 종목명 td 에 rowspan 을 써서 같은
  종목의 여러 리포트가 한 블록을 이루는 구조. 첫 행은 td 7개 (종목명
  포함), 이후 행은 td 6개 (종목명 td 가 rowspan 으로 생략). 컬럼
  순서: [종목명] | 리포트제목 | 증권사 | 날짜(YY.MM.DD) | 목표가 |
  투자의견 | PDF. 목표가 는 콤마 구분 OR plain integer, 투자의견 은
  visible text OR <img alt> OR <td title>/<em>/<strong> 내 키워드.
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

_RATING_KEYWORDS = (
    "강력매수", "적극매수", "비중확대", "비중축소", "매수", "매도", "보유", "중립",
    "Strong Buy", "Trading Buy", "Outperform", "Overweight", "Accumulate",
    "Marketperform", "Market Perform", "Sector Perform",
    "Underperform", "Underweight", "Reduce",
    "Not Rated", "NR", "Coverage Initiated",
    "Buy", "Hold", "Sell", "Neutral",
)

# Korean broker names almost always END with one of these suffixes
# (미래에셋증권 / DB금융투자 / 삼성자산운용 / …). Anchoring to the suffix
# avoids false-matching report TITLES that merely contain 투자/증권
# mid-string (e.g. a "투자의견 상향" 제목). _BROKER_RE is the loose
# fallback for non-standard names (foreign brokers, abbreviations).
_BROKER_SUFFIX_RE = re.compile(
    r"(증권|투자|자산운용|금융투자|리서치|캐피탈|Securities|Investment)\s*$", re.I)
_BROKER_RE = re.compile(
    r"증권|투자|자산운용|금융투자|리서치|캐피탈|Securities|Investment", re.I)
_PLAIN_INT_RE = re.compile(r"(?<![0-9])(\d{4,7})(?![0-9./-])")


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
    """Strip a <tr> into a list of plain-text <td>/<th> cell values.

    Enhanced version (mirroring hk_consensus_client): extracts visible
    text PLUS hidden-but-semantic content — img alt, title/data-value/
    data-text attributes on the <td> tag itself and on inner elements.
    This catches cases where Naver puts the rating in a <td title="매수">
    or an <img alt="Buy"> rather than as visible text.
    """
    raw_cells = re.findall(r"(<t[dh][^>]*>)(.*?)</t[dh]>", row_html, re.DOTALL | re.I)
    out = []
    for tag, inner in raw_cells:
        parts = []
        for attr in ("title", "data-value", "data-text"):
            m = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, re.I)
            if m:
                parts.append(m.group(1))
        inner = re.sub(r'<img\s[^>]*?\balt=["\']([^"\']*)["\'][^>]*/?>', r' \1 ', inner, flags=re.I)
        for attr in ("title", "data-value", "data-text"):
            inner = re.sub(
                rf'<[^>]+?\b{attr}=["\']([^"\']*)["\'][^>]*?>',
                r' \1 ', inner, flags=re.I,
            )
        txt = re.sub(r"<[^>]+>", " ", inner)
        txt = txt.replace("&nbsp;", " ").replace("&amp;", "&")
        combined = " ".join(parts + [txt])
        out.append(" ".join(combined.split()).strip())
    return out


def _parse_rows(html: str, cutoff) -> list[dict]:
    """Tolerant per-row parser using pattern matching (not fixed column
    index) — robust to Naver's rowspan structure where the first column
    (종목명) spans multiple rows, making cell count 6 or 7.

    For each <tr> with ≥3 cells, identify date / target / rating /
    broker / title by pattern. Same approach as hk_consensus_client's
    tolerant parser, enhanced with plain-integer fallback for target
    prices and semantic attribute extraction for ratings.
    """
    rows: list[dict] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        cells = _cell_texts(row_html)
        if len(cells) < 3:
            continue

        # date — Naver uses YY.MM.DD; also accept YYYY-MM-DD / YYYY.MM.DD
        date_str = None
        for c in cells:
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", c)
            if m:
                yy, mm, dd = m.group(1), m.group(2), m.group(3)
                year = int(yy) + 2000
                date_str = f"{year}-{mm}-{dd}"
                break
            m2 = re.search(r"(\d{4})[-./](\d{2})[-./](\d{2})", c)
            if m2:
                date_str = f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
                break
        if not date_str:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue

        # target price — comma-grouped OR plain integer (≥ 1,000).
        # Skip cells containing the date digits to avoid false matches.
        target_val = None
        date_digits = date_str.replace("-", "")
        for c in cells:
            if date_digits in c.replace(",", "").replace(".", ""):
                continue
            m = re.search(r"(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+)(?![0-9])", c)
            if m:
                target_val = float(m.group(1).replace(",", ""))
                break
            m2 = _PLAIN_INT_RE.search(c)
            if m2:
                val = int(m2.group(1))
                if val >= 1000 and not (2000 <= val <= 2099):
                    target_val = float(val)
                    break

        # rating keyword — search all cells (visible + attribute text)
        rating_raw = ""
        for c in cells:
            for kw in _RATING_KEYWORDS:
                if kw.lower() in c.lower():
                    rating_raw = kw
                    break
            if rating_raw:
                break

        # broker — prefer a short cell ENDING with a broker suffix (so a
        # report title containing 투자/증권 mid-string isn't mistaken for the
        # broker), then fall back to a loose in-string match for any
        # non-standard broker name.
        broker = ""
        for c in cells:
            if c and len(c) <= 22 and _BROKER_SUFFIX_RE.search(c):
                broker = c
                break
        if not broker:
            for c in cells:
                if c and len(c) <= 22 and _BROKER_RE.search(c):
                    broker = c
                    break

        # title — longest non-broker, non-numeric cell
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
