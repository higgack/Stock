"""Thin client for MOPS (公開資訊觀測站 / Market Observation Post System).

TW analogue to bot/dart_client.py (KR DART) and bot/edinet_client.py
(JP EDINET). MOPS is the official disclosure platform run by 證券交易所
(TWSE) and 證券櫃檯買賣中心 (TPEx) — carries the same class of data
DART/EDINET provide: 重大訊息 (material announcements), 季報 / 年報
(quarterly/annual reports), 法人說明會 (investor conferences), 內部人
持股 (insider holdings).

Endpoints used (publicly accessible from outside Taiwan, no auth):
  POST /mops/web/ajax_t05st02 — 重大訊息 query (form-encoded)
  GET  /mops/web/ajax_t146sb05 — 內部人持股 (annual schedule)
  GET  /mops/web/t164sb01      — 公司基本資料 (background)

Auth: none. MOPS is freely accessible from outside Taiwan (no GFW, no
geofence). HTML scrape required for most endpoints since the official
"API" is form-POST-based and returns HTML tables.

Caching: per (ticker, day) JSON at ~/.tradingagents/cache/mops/ with 12h
TTL — same pattern as DART/EDINET. 重大訊息 schedule rarely changes
intra-day so 12h cache is generous.

Doc-type filtering: MOPS doesn't use opaque numeric codes like EDINET;
each 重大訊息 row has a human-readable 主旨 (subject) field. We pass
the top-N most recent rows through to the analyst — same prompt-budget
ceiling as DART/EDINET (8 entries).

Design parity with EDINET/DART:
- get_recent_disclosures(ticker, days_back, limit) — same signature
- get_insider_holdings(ticker) — same signature, returns []|list[dict]
- next_earnings_window(ticker) — TW fiscal-year-end 12/31 + 季報
  filing deadlines (Q1 5/15, Q2 8/14, Q3 11/14, Annual 3/31)
- format_mops_tw_block(...) — same shape as DART/EDINET block
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

log = logging.getLogger("bot.mops")

_BASE = "https://mops.twse.com.tw"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "mops"
_CACHE_TTL_HOURS = 12
_HTTP_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
}


def _ticker_to_co_id(ticker: str) -> Optional[str]:
    """Convert a `.TW` / `.TWO` ticker to the 4-digit MOPS 公司代號.
    Almost all TW tickers are 4 digits — TSMC 2330, MediaTek 2454,
    HonHai 2317. Some smaller TPEx entries use 5-6 digits (8299
    Phison) — MOPS handles those equivalently.
    Returns the bare digit string or None if input isn't `.TW`/`.TWO`."""
    if not ticker:
        return None
    code = ticker.upper().split(".")[0]
    if not code.isdigit():
        return None
    if len(code) not in (4, 5, 6):
        return None
    return code


class MopsClient:
    """Per-call MOPS wrapper. Cheap to construct; reuse via
    `get_mops()` for the process-wide cached instance."""

    def __init__(self):
        # MOPS has no API key — left in the signature for parity with
        # DART/EDINET so a future caller doesn't get surprised.
        pass

    # ---- 重大訊息 (material announcements) ----------------------------

    def get_recent_disclosures(
        self, ticker: str, days_back: int = 30, limit: int = 8,
    ) -> list[dict]:
        """Return up to `limit` 重大訊息 filings for this ticker in the
        last `days_back` calendar days, newest first.

        Each entry: {date, subject, doc_type_label, filer_name}.
        doc_type_label is always '重大訊息' for MOPS — TW disclosure
        doesn't use opaque numeric codes like EDINET. The 主旨
        (subject) free-text field carries the actual content type
        ('召開法人說明會', '營運狀況變化', '取得或處分資產' etc.).
        """
        co_id = _ticker_to_co_id(ticker)
        if not co_id:
            return []

        cache_key = f"disclosures_{co_id}_{date.today().isoformat()}.json"
        cache_file = _CACHE_DIR / cache_key
        if cache_file.exists():
            try:
                age_h = (time.time() - cache_file.stat().st_mtime) / 3600
                if age_h < _CACHE_TTL_HOURS:
                    cached = json.loads(cache_file.read_text())
                    return cached[:limit]
            except Exception as exc:
                log.warning("mops: cache read failed for %s: %s", co_id, exc)

        # MOPS 重大訊息 query: POST form-encoded to ajax_t05st02
        # Parameters: encodeURIComponent=1, step=1, firstin=1, off=1,
        # keyword4=(co_id), code1=, TYPEK=all, year=(YYYY-1911 = ROC year),
        # month=(MM), b_date=(DDDD), e_date=(DDDD)
        # Simpler: use t05st01 which returns ALL recent 重大訊息 for the
        # company without date filtering, then filter client-side by
        # `days_back`. Less round-trips on transient errors.
        results: list[dict] = []
        try:
            url = f"{_BASE}/mops/web/ajax_t05st01"
            payload = {
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "keyword4": "",
                "code1": "",
                "TYPEK": "all",
                "co_id": co_id,
                "year": "",
                "month": "",
            }
            resp = requests.post(
                url, headers=_HEADERS, data=payload, timeout=_HTTP_TIMEOUT,
            )
            resp.encoding = "utf-8"
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            log.warning("mops: 重大訊息 fetch failed for %s: %s", co_id, exc)
            return []

        # MOPS returns an HTML table with rows: 公司代號 | 公司名稱 |
        # 發言日期 | 發言時間 | 主旨. Parse with a permissive regex —
        # the table structure is stable but whitespace drifts.
        row_re = re.compile(
            r"<tr[^>]*>"
            r"\s*<td[^>]*>\s*(\d{3,6})\s*</td>"      # 公司代號
            r"\s*<td[^>]*>\s*([^<]+?)\s*</td>"       # 公司名稱
            r"\s*<td[^>]*>\s*(\d{3,4}/\d{1,2}/\d{1,2})\s*</td>"  # 發言日期 (民國YYY/MM/DD)
            r"\s*<td[^>]*>\s*(\d{1,2}:\d{2}:\d{2})\s*</td>"      # 발언時間
            r"\s*<td[^>]*>\s*([^<]+?)\s*</td>",     # 主旨
            re.DOTALL,
        )
        today = date.today()
        cutoff = today - timedelta(days=days_back)
        for m in row_re.finditer(html):
            roc_date = m.group(3)
            filer_name = m.group(2).strip()
            subject = m.group(5).strip()
            try:
                # Republic of China date → Gregorian (ROC year + 1911).
                roc_y, mm, dd = roc_date.split("/")
                greg_year = int(roc_y) + 1911
                d = date(greg_year, int(mm), int(dd))
            except Exception:
                continue
            if d < cutoff:
                continue
            results.append({
                "date": d.isoformat(),
                "subject": subject,
                "doc_type_label": "重대訊息",
                "filer_name": filer_name,
            })

        # Sort newest first; MOPS already does this typically but
        # belt-and-suspenders.
        results.sort(key=lambda r: r["date"], reverse=True)

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception as exc:
            log.warning("mops: cache write failed for %s: %s", co_id, exc)

        return results[:limit]

    # ---- 內部人持股 (insider holdings annual) --------------------------

    def get_insider_holdings(self, ticker: str) -> list[dict]:
        """Pull most-recent 內部人持股 disclosure. MOPS publishes this
        annually (typically June). Returns list of {name, role, pct,
        shares} for top insiders.

        TW MOPS 內部人持股 is annual + scheduled, NOT triggered like
        KR DART's 임원·주요주주 (which fires on every transaction).
        So this is more of a 'last known holdings' snapshot. Good
        enough for the ownership-structure analyst block.
        """
        co_id = _ticker_to_co_id(ticker)
        if not co_id:
            return []

        cache_key = f"insiders_{co_id}_{date.today().isoformat()}.json"
        cache_file = _CACHE_DIR / cache_key
        if cache_file.exists():
            try:
                age_h = (time.time() - cache_file.stat().st_mtime) / 3600
                if age_h < _CACHE_TTL_HOURS:
                    return json.loads(cache_file.read_text())
            except Exception as exc:
                log.warning("mops: insider cache read failed for %s: %s", co_id, exc)

        # MOPS 內部人持股 query — annual 持股餘額 table at t146sb05.
        # Per-row: 職稱 | 姓名 | 選任日期 | 目前持股 | 質押股數 | 質押比.
        # Returns top-N rows newest filings first.
        results: list[dict] = []
        try:
            url = f"{_BASE}/mops/web/ajax_t146sb05"
            payload = {
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "co_id": co_id,
                "TYPEK": "all",
                "year": "",
                "month": "",
            }
            resp = requests.post(
                url, headers=_HEADERS, data=payload, timeout=_HTTP_TIMEOUT,
            )
            resp.encoding = "utf-8"
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            log.warning("mops: 內部人持股 fetch failed for %s: %s", co_id, exc)
            return []

        # Best-effort regex against the typical table row shape. MOPS
        # markup varies slightly per filing year — defensive matching.
        row_re = re.compile(
            r"<tr[^>]*>"
            r"\s*<td[^>]*>\s*([^<]+?)\s*</td>"        # 職稱
            r"\s*<td[^>]*>\s*([^<]+?)\s*</td>"        # 姓名
            r"\s*<td[^>]*>\s*([\d/]+)\s*</td>"        # 選任日期
            r"\s*<td[^>]*>\s*([\d,]+)\s*</td>",       # 持股股數
            re.DOTALL,
        )
        for m in row_re.finditer(html):
            role = m.group(1).strip()
            name = m.group(2).strip()
            try:
                shares = int(m.group(4).replace(",", ""))
            except ValueError:
                shares = 0
            if not name or not role:
                continue
            results.append({
                "name": name,
                "role": role,
                "shares": shares,
                "pct": 0.0,  # MOPS doesn't include pct here; computed
                              # downstream when share-count is available.
            })

        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception as exc:
            log.warning("mops: insider cache write failed for %s: %s", co_id, exc)

        return results

    # ---- next earnings window (TW fiscal-year-end 12/31) ----------------

    def next_earnings_window(self, ticker: str) -> Optional[str]:
        """Infer next quarterly / annual reporting window for a TW
        ticker. TW fiscal year ends 12/31 for nearly all listed companies
        (same as KR, different from JP 3/31). Filing deadlines per
        TWSE / FSC:
          Q1 → 5月15日, Q2 → 8月14日, Q3 → 11月14日, Annual → 3月31日.
        """
        co_id = _ticker_to_co_id(ticker)
        if not co_id:
            return None
        today = date.today()
        quarters = [
            (date(today.year, 5, 15), "Q1 (1-3월) 보고 마감"),
            (date(today.year, 8, 14), "Q2 (4-6월) 보고 마감"),
            (date(today.year, 11, 14), "Q3 (7-9월) 보고 마감"),
            (date(today.year + 1, 3, 31), "Annual (10-12월) 보고 마감"),
        ]
        for due_date, label in quarters:
            if due_date >= today:
                return f"{due_date.isoformat()} {label}"
        return None


_mops_singleton: MopsClient | None = None


def get_mops() -> MopsClient:
    """Process-wide cached MOPS client. Constructor is cheap but reuse
    keeps any future in-memory caches (e.g. 公司基本資料 lookup)
    consistent across calls."""
    global _mops_singleton
    if _mops_singleton is None:
        _mops_singleton = MopsClient()
    return _mops_singleton


def format_mops_tw_block(
    disclosures: list[dict],
    insiders: list[dict],
    earnings_window: Optional[str],
) -> str:
    """Render MOPS data as a prompt-ready bullet block. Empty string
    when all three slots are empty so the caller can omit the section
    header rather than emit a blank block."""
    if not disclosures and not insiders and not earnings_window:
        return ""
    parts: list[str] = []

    if disclosures:
        parts.append(f"최근 重大訊息 (MOPS, 최대 {len(disclosures)}건):")
        for d in disclosures:
            date_s = d.get("date") or "?"
            subject = (d.get("subject") or "").strip()
            if len(subject) > 100:
                subject = subject[:97] + "..."
            parts.append(f"  • {date_s}: {subject}")

    if insiders:
        # Top 5 by shares (most TW insiders have meaningful holdings).
        top = sorted(insiders, key=lambda r: r.get("shares", 0), reverse=True)[:5]
        parts.append(f"\n內部人持股 (최근 公告 기준, 상위 {len(top)}):")
        for r in top:
            name = r.get("name") or "?"
            role = (r.get("role") or "").strip()
            shares = r.get("shares") or 0
            role_part = f" ({role})" if role else ""
            parts.append(f"  • {name}{role_part}: {shares:,} 주")

    if earnings_window:
        parts.append(f"\n다음 정기보고서 윈도: {earnings_window}")

    return "\n".join(parts)
