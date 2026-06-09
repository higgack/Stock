"""Naver Finance 종목별 리서치 리포트 scraper.

한경 컨센서스(consensus.hankyung.com) 가 2026 초 JS 렌더링으로 전환되어
정적 HTML scrape 불가 → Naver Finance 종목 리서치를 대체 소스로 활용.

2단 fetch 구조 (2026-06-08 실 HTML 검증):
  1) List 페이지 (company_list.naver) — 종목명·제목·증권사·날짜·조회수만
     제공. 목표가·투자의견 컬럼 없음.
  2) Detail 페이지 (company_read.naver?nid=N) — 개별 리포트에 목표가·
     투자의견이 명시적 HTML 태그로 존재:
       목표가   <em class="money"><strong>300,000</strong></em>
       투자의견 <em class="coment">Buy</em>

키 불필요 (static HTML). 12h 디스크 캐시. 같은 {date, broker, rating,
target, title} 스키마를 반환해 한경 → Naver 교체가 dashboard 와 호환.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.naver_research")

_BASE_URL = "https://finance.naver.com/research/company_list.naver"
_DETAIL_URL = "https://finance.naver.com/research/company_read.naver"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "naver_research"
_CACHE_TTL_HOURS = 12
_HTTP_TIMEOUT = 15
_DETAIL_WORKERS = 4

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

_BROKER_SUFFIX_RE = re.compile(
    r"(증권|투자|자산운용|금융투자|리서치|캐피탈|Securities|Investment)\s*$", re.I)
_BROKER_RE = re.compile(
    r"증권|투자|자산운용|금융투자|리서치|캐피탈|Securities|Investment", re.I)

_NID_RE = re.compile(r'company_read\.naver\?nid=(\d+)')
# Detail page regex — allow arbitrary HTML tags between label and value
# (Naver uses <th>목표가</th><td><em>... table structure)
_TARGET_RE = re.compile(
    r'목표가(?:<[^>]*>|\s)*<em[^>]*>\s*<strong[^>]*>([\d,]+)</strong>', re.I)
_RATING_DETAIL_RE = re.compile(
    r'투자의견(?:<[^>]*>|\s)*<em[^>]*>([^<]+)</em>', re.I)
# Class-based fallback — match <em class="money"> / <em class="coment">
# independently (no label prefix required)
_TARGET_CLASS_RE = re.compile(
    r'<em[^>]*class=["\'][^"\']*\bmoney\b[^"\']*["\'][^>]*>\s*'
    r'<strong[^>]*>([\d,]+)</strong>', re.I)
_RATING_CLASS_RE = re.compile(
    r'<em[^>]*class=["\'][^"\']*\bcoment\b[^"\']*["\'][^>]*>'
    r'([^<]+)</em>', re.I)


def _normalize_code(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    code = ticker.upper().split(".")[0]
    if code.isdigit() and len(code) == 6:
        return code
    return None


def _get(url: str, **kwargs) -> Optional[str]:
    """GET a Naver Finance page, return decoded text or None."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_HTTP_TIMEOUT,
                            **kwargs)
        resp.encoding = "euc-kr"
        if resp.status_code != 200 or not resp.text:
            return None
        return resp.text
    except Exception as exc:
        log.warning("naver_research: fetch failed for %s: %s", url, exc)
        return None


def _cell_texts(row_html: str) -> list[str]:
    """Strip a <tr> into a list of plain-text cell values."""
    raw_cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html,
                           re.DOTALL | re.I)
    out = []
    for inner in raw_cells:
        txt = re.sub(r"<[^>]+>", " ", inner)
        txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
               .replace("&middot;", "·"))
        out.append(" ".join(txt.split()).strip())
    return out


# ------------------------------------------------------------------
# Stage 1: list page → report metadata + nid links
# ------------------------------------------------------------------

def _parse_list_page(html: str, cutoff) -> list[dict]:
    """Parse the list page to extract report metadata + nid links.

    Actual columns: 종목명 | 제목(nid link) | 증권사 | 첨부 | 작성일 | 조회수
    """
    rows: list[dict] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html,
                               re.DOTALL | re.I):
        nid_m = _NID_RE.search(row_html)
        if not nid_m:
            continue
        nid = nid_m.group(1)

        cells = _cell_texts(row_html)
        if len(cells) < 4:
            continue

        # date — YY.MM.DD
        date_str = None
        for c in cells:
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", c)
            if m:
                yy, mm, dd = m.group(1), m.group(2), m.group(3)
                date_str = f"{int(yy) + 2000}-{mm}-{dd}"
                break
        if not date_str:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue

        # broker — prefer suffix-anchored, then loose fallback
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

        # title — from the nid link text
        title_m = re.search(
            r'company_read\.naver\?nid=\d+[^"]*"[^>]*>([^<]+)',
            row_html, re.I)
        title = title_m.group(1).strip()[:80] if title_m else ""
        if not title:
            for c in sorted(cells, key=len, reverse=True):
                if c and c != broker and not re.fullmatch(r"[\d,.\-/\s]+", c):
                    title = c[:80]
                    break

        rows.append({
            "nid": nid,
            "title": title,
            "broker": broker,
            "analyst": "",
            "target": None,
            "rating": "",
            "date": date_str,
        })
    return rows


# ------------------------------------------------------------------
# Stage 2: detail pages → target price + rating
# ------------------------------------------------------------------

def _fetch_report_detail(nid: str) -> tuple[Optional[float], str]:
    """Fetch target price and rating from an individual report page."""
    html = _get(f"{_DETAIL_URL}?nid={nid}")
    if not html:
        return None, ""

    target: Optional[float] = None
    for pat in (_TARGET_RE, _TARGET_CLASS_RE):
        m = pat.search(html)
        if m:
            try:
                target = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
            if target:
                break

    rating = ""
    for pat in (_RATING_DETAIL_RE, _RATING_CLASS_RE):
        m = pat.search(html)
        if m:
            raw = m.group(1).strip()
            for kw in _RATING_KEYWORDS:
                if kw.lower() == raw.lower():
                    rating = kw
                    break
            if not rating:
                rating = raw
            if rating:
                break

    return target, rating


# ------------------------------------------------------------------
# Market-wide recent research (전체 시장 최신 리포트 — itemCode 없이)
# ------------------------------------------------------------------

# 종목명 link: <a href="/item/main.naver?code=005930">삼성전자</a>
_ITEM_CODE_RE = re.compile(
    r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', re.I)


def _parse_market_list_page(html: str, cutoff) -> list[dict]:
    """전체 시장 리서치 목록 행 파싱 — 종목코드 + 종목명 포함.

    company_list.naver 를 itemCode 없이 호출하면 전 종목 최신 리포트가
    최신순으로 나온다. 컬럼: 종목명(item link) | 제목(nid link) | 증권사 |
    첨부 | 작성일(YY.MM.DD) | 조회수.
    """
    rows: list[dict] = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.I):
        nid_m = _NID_RE.search(row_html)
        if not nid_m:
            continue
        nid = nid_m.group(1)

        code_m = _ITEM_CODE_RE.search(row_html)
        if not code_m:
            continue
        code = code_m.group(1)
        name = code_m.group(2).strip()

        cells = _cell_texts(row_html)

        date_str = None
        for c in cells:
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", c)
            if m:
                yy, mm, dd = m.group(1), m.group(2), m.group(3)
                date_str = f"{int(yy) + 2000}-{mm}-{dd}"
                break
        if not date_str:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue

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

        title_m = re.search(
            r'company_read\.naver\?nid=\d+[^"]*"[^>]*>([^<]+)', row_html, re.I)
        title = title_m.group(1).strip()[:80] if title_m else ""
        if not title:
            for c in sorted(cells, key=len, reverse=True):
                if (c and c != broker and c != name
                        and not re.fullmatch(r"[\d,.\-/\s]+", c)):
                    title = c[:80]
                    break

        rows.append({
            "nid": nid, "code": code, "name": name,
            "broker": broker, "title": title, "rating": "", "date": date_str,
        })
    return rows


def fetch_recent_research_market(limit: int = 25, days_back: int = 14,
                                 fetch_detail: bool = True) -> list[dict]:
    """전체 시장 최근 종목 리서치 리포트 (Naver Finance 리서치 목록).

    한경 컨센서스가 JS 렌더링으로 정적 scrape 불가 → market.html 의
    '최근 리서치 액션' KR 탭 대체 소스. Returns
    [{code, name, broker, rating, title, date}] (날짜 내림차순) —
    dashboard 의 research_kr 스키마와 호환. 키 불필요, 12h 디스크 캐시.

    fetch_detail=True 면 각 리포트 상세에서 투자의견(rating)을 best-effort
    로 채운다 (실패 시 빈 문자열 — 목록은 그대로 표시)."""
    cache_key = f"naver_market_v1_{date.today().isoformat()}_{limit}.json"
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                return cached or []
        except Exception as exc:
            log.warning("naver_research: market cache read failed: %s", exc)

    today = date.today()
    cutoff = today - timedelta(days=days_back)
    rows: list[dict] = []
    seen_nid: set[str] = set()
    for page in (1, 2):
        html = _get(_BASE_URL, params={"page": page})
        if not html:
            break
        for r in _parse_market_list_page(html, cutoff):
            if r["nid"] in seen_nid:
                continue
            seen_nid.add(r["nid"])
            rows.append(r)
        if len(rows) >= limit:
            break

    rows = rows[:limit]
    if not rows:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("[]")
        except Exception:
            pass
        log.info("naver_research: no recent market reports (%d-day window)",
                 days_back)
        return []

    if fetch_detail:
        detail_map: dict[str, tuple[Optional[float], str]] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_fetch_report_detail, r["nid"]): r["nid"]
                       for r in rows}
            for fut in as_completed(futures):
                nid = futures[fut]
                try:
                    detail_map[nid] = fut.result()
                except Exception:
                    detail_map[nid] = (None, "")
        for r in rows:
            _, rt = detail_map.get(r["nid"], (None, ""))
            r["rating"] = rt

    out = [{
        "code": r["code"], "name": r["name"], "broker": r["broker"],
        "rating": r.get("rating", ""), "title": r["title"], "date": r["date"],
    } for r in rows]

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        log.warning("naver_research: market cache write failed: %s", exc)

    return out


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def fetch_research(ticker: str, days_back: int = 90) -> Optional[dict]:
    """Scrape Naver Finance 종목 리서치 리포트 목록 + 상세.

    Returns dict with same shape as hk_consensus_client.fetch_consensus:
    {target_price, rating, analyst_count, last_report_date, report_count,
     reports: [...]} or None."""
    code = _normalize_code(ticker)
    if not code:
        return None

    cache_key = f"naver_research_v2_{code}_{date.today().isoformat()}.json"
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                return cached if cached else None
        except Exception as exc:
            log.warning("naver_research: cache read failed for %s: %s",
                        code, exc)

    html = _get(_BASE_URL, params={
        "searchType": "itemCode", "itemCode": code})
    if not html:
        return None

    today = date.today()
    cutoff = today - timedelta(days=days_back)
    rows = _parse_list_page(html, cutoff)

    if not rows:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("null")
        except Exception:
            pass
        log.info("naver_research: no recent reports for %s (%d-day window)",
                 code, days_back)
        return None

    # Parallel-fetch detail pages for target + rating
    detail_map: dict[str, tuple[Optional[float], str]] = {}
    with ThreadPoolExecutor(max_workers=_DETAIL_WORKERS) as pool:
        futures = {pool.submit(_fetch_report_detail, r["nid"]): r["nid"]
                   for r in rows}
        for fut in as_completed(futures):
            nid = futures[fut]
            try:
                detail_map[nid] = fut.result()
            except Exception:
                detail_map[nid] = (None, "")

    for r in rows:
        t, rt = detail_map.get(r["nid"], (None, ""))
        r["target"] = t
        r["rating"] = rt
        del r["nid"]

    # Aggregate
    target_vals = [r["target"] for r in rows if r["target"]]
    avg_target = sum(target_vals) / len(target_vals) if target_vals else None

    rating_counts: dict[str, int] = {}
    for r in rows:
        direction = _RATING_TO_DIRECTION.get(r["rating"], "")
        if direction:
            rating_counts[direction] = rating_counts.get(direction, 0) + 1
    dominant_rating = (max(rating_counts, key=rating_counts.get)
                       if rating_counts else "")

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
        log.warning("naver_research: cache write failed for %s: %s",
                    code, exc)

    return result
