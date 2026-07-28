"""JPX investor-type weekly flow client (JP 投資部門別 売買状況).

Fetches market-wide weekly net buy/sell by investor type from JPX:
  - 外国人 (Foreigners) — JP market largest driver
  - 投資信託 (Investment Trusts) — domestic mutual funds
  - 個人 (Individuals) — retail
  - 事業法人 (Corporations)
  - 自己 (Securities firms, proprietary)

Data is aggregate market-wide (TSE Prime+Standard+Growth, not per-stock).
Published weekly (Thursday/Friday for prior week).

JPX changed URL structure (2026-06): fixed aggregated CSV → per-week XLS
files with dynamic directory hashes. Approach:
  1. Scrape index page → extract latest stock_val_1_*.xls links
  2. Download + parse with xlrd (graceful skip if not installed)
  3. Per-file disk cache (published data is immutable)
  4. Return [{date, foreigners, trusts, ...}] shape

xlrd required: `pip install xlrd`. Missing → graceful None.
No API key required. 96h aggregate cache (weekly data).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.jpx_flow")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "jpx_flow"
_AGG_CACHE_TTL_HOURS = 96       # 4 days — aggregate result
_FILE_CACHE_TTL_HOURS = 24 * 90  # 90 days — per-week XLS (immutable)
_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

_BASE_URL = "https://www.jpx.co.jp"
_INDEX_URL = (
    _BASE_URL + "/markets/statistics-equities/investor-type/index.html"
)

_INVESTOR_KEYWORDS: list[tuple[str, str]] = [
    ("海外投資家", "foreigners"),
    ("Foreigners", "foreigners"),
    ("投資信託", "trusts"),
    ("Investment Trusts", "trusts"),
    ("事業法人", "corporations"),
    ("個人", "individuals"),
    ("Individuals", "individuals"),
    ("自己", "securities"),
    ("Proprietary", "securities"),
]


def _cache_get(key: str, ttl_hours: float = _AGG_CACHE_TTL_HOURS) -> Optional[dict | list]:
    f = _CACHE_DIR / key
    if not f.exists():
        return None
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h >= ttl_hours:
            return None
        return json.loads(f.read_text())
    except Exception:
        return None


def _cache_set(key: str, data) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / key).write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


# ── index page scraper ───────────────────────────────────────────

def _scrape_xls_links() -> list[tuple[str, str]]:
    """Scrape JPX index page for stock_val_1_*.xls URLs.

    Returns [(full_url, YYMMWW), ...] sorted newest first (deduplicated).
    """
    try:
        resp = requests.get(_INDEX_URL, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.warning("jpx_flow: index page returned %d", resp.status_code)
            return []
        pat = (
            r'href="(/markets/statistics-equities/investor-type/'
            r'[^"]+/stock_val_1_(\d{6})\.xls)"'
        )
        matches = re.findall(pat, resp.text)
        matches.sort(key=lambda x: x[1], reverse=True)
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for path, yymw in matches:
            if yymw not in seen:
                seen.add(yymw)
                unique.append((_BASE_URL + path, yymw))
        return unique
    except Exception as exc:
        log.warning("jpx_flow: scrape failed: %s", exc)
        return []


# ── XLS download + parse ─────────────────────────────────────────

def _download_xls(url: str) -> Optional[bytes]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.warning("jpx_flow: %s returned %d", url, resp.status_code)
            return None
        return resp.content
    except Exception as exc:
        log.warning("jpx_flow: download failed: %s", exc)
        return None


def _parse_num_cell(val) -> Optional[int]:
    """Parse XLS cell value to int. Handles float, int, str with commas."""
    if isinstance(val, float):
        if val == 0.0:
            return None
        return int(val)
    if isinstance(val, int):
        return val if val != 0 else None
    s = str(val).replace(",", "").replace(" ", "").strip()
    if not s or s in ("-", "N/A", ""):
        return None
    try:
        v = int(float(s))
        return v if v != 0 else None
    except (ValueError, TypeError):
        return None


def _norm_cell(val) -> str:
    """Normalize cell text: strip + remove full-width spaces."""
    return str(val).replace("　", "").strip()


def _parse_xls_flow(data: bytes) -> list[dict]:
    """Parse JPX investor-type weekly XLS into flow dicts.

    Each file contains 2 weeks of data side by side (columns 3-6 = prev
    week, columns 7-10 = current week). Balance (差引き) is on the 売り
    row when net-negative, on the 買い row when net-positive.

    Returns list of week dicts (up to 2), values converted to 百万円.
    """
    try:
        import xlrd  # noqa: heavy/optional
    except ImportError:
        log.warning("jpx_flow: xlrd not installed — pip install xlrd")
        return []

    try:
        wb = xlrd.open_workbook(file_contents=data)
        sh = wb.sheet_by_index(0)

        # ── find 差引き columns ──
        bal_cols: list[int] = []
        for r in range(min(sh.nrows, 15)):
            for c in range(sh.ncols):
                if "差引" in str(sh.cell_value(r, c)):
                    if c not in bal_cols:
                        bal_cols.append(c)
        if not bal_cols:
            log.info("jpx_flow: no balance columns found")
            return []

        # ── extract year from title ──
        year = ""
        for r in range(min(sh.nrows, 5)):
            m = re.search(r"(\d{4})年", str(sh.cell_value(r, 0)))
            if m:
                year = m.group(1)
                break

        # ── extract week date ranges (e.g. "05/25～05/29") ──
        week_ranges: list[str] = []
        for r in range(min(sh.nrows, 15)):
            for c in range(sh.ncols):
                v = str(sh.cell_value(r, c)).strip()
                m = re.search(
                    r"(\d{1,2})/(\d{1,2})[～~\-]\s*(\d{1,2})/(\d{1,2})", v
                )
                if m and v not in week_ranges:
                    week_ranges.append(v)

        # ── build result dicts with date labels ──
        results: list[dict] = []
        for i, _bc in enumerate(bal_cols):
            d: dict = {}
            if i < len(week_ranges):
                m = re.search(
                    r"(\d{1,2})/(\d{1,2})[～~\-]\s*(\d{1,2})/(\d{1,2})",
                    week_ranges[i],
                )
                if m and year:
                    d["date"] = (
                        f"{year}/{m.group(3).zfill(2)}/{m.group(4).zfill(2)}"
                    )
                else:
                    d["date"] = week_ranges[i]
            results.append(d)

        # ── scan all rows for investor-type keywords ──
        for r in range(sh.nrows):
            label = _norm_cell(sh.cell_value(r, 0))
            if not label:
                continue

            matched_key = None
            for kw, key in _INVESTOR_KEYWORDS:
                if kw in label:
                    matched_key = key
                    break
            if not matched_key:
                continue

            for i, bc in enumerate(bal_cols):
                if i >= len(results):
                    break
                if matched_key in results[i]:
                    continue
                val = _parse_num_cell(sh.cell_value(r, bc)) if bc < sh.ncols else None
                if val is None and r + 1 < sh.nrows:
                    val = _parse_num_cell(sh.cell_value(r + 1, bc)) if bc < sh.ncols else None
                if val is not None:
                    results[i][matched_key] = val // 1000  # 千円 → 百万円

        return [r for r in results if len(r) > 1]
    except Exception as exc:
        log.warning("jpx_flow: XLS parse error: %s", exc)
        return []


# ── public API ───────────────────────────────────────────────────

def fetch_jpx_weekly_flow() -> Optional[list[dict]]:
    """Fetch recent weekly investor-type flow data from JPX.

    Returns list of weekly flow dicts (newest first), values in 百万円.
    """
    ck = f"jpx_flow_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    links = _scrape_xls_links()
    if not links:
        return None

    rows: list[dict] = []
    seen_dates: set[str] = set()

    for url, yymw in links[:8]:
        fk = f"jpx_xls2_{yymw}.json"
        fc = _cache_get(fk, ttl_hours=_FILE_CACHE_TTL_HOURS)
        if fc:
            items = fc if isinstance(fc, list) else [fc]
            for item in items:
                d = item.get("date", "")
                if d and d not in seen_dates:
                    seen_dates.add(d)
                    rows.append(item)
            continue

        data = _download_xls(url)
        if not data:
            continue

        parsed = _parse_xls_flow(data)
        if parsed:
            _cache_set(fk, parsed)
            for item in parsed:
                d = item.get("date", "")
                if d and d not in seen_dates:
                    seen_dates.add(d)
                    rows.append(item)

    if rows:
        rows.sort(key=lambda r: r.get("date", ""), reverse=True)
        _cache_set(ck, rows[:8])
        return rows[:8]
    return None


def format_jpx_flow_block(rows: Optional[list[dict]]) -> str:
    """Format JPX weekly investor flow into a compact text block."""
    if not rows:
        return ""

    def _fmt_oku(val_mil: int) -> str:
        oku = val_mil / 100
        sign = "+" if oku >= 0 else ""
        return f"{sign}{oku:,.0f}億円"

    lines = ["• JPX 投資部門別 週間売買動向 (単位 億円):"]
    for r in rows[:4]:
        dt = r.get("date", "?")
        parts = []
        for label, key in (
            ("外国人", "foreigners"),
            ("投信", "trusts"),
            ("個人", "individuals"),
            ("法人", "corporations"),
        ):
            val = r.get(key)
            if val is not None:
                parts.append(f"{label} {_fmt_oku(val)}")
        if parts:
            lines.append(f"  {dt}: {' / '.join(parts)}")

    if len(rows) >= 2:
        f_latest = rows[0].get("foreigners", 0)
        f_prev = rows[1].get("foreigners", 0)
        if f_latest > 0 and f_prev < 0:
            lines.append("  ⚠️ 외국인 売→買 전환 (직전 주 대비)")
        elif f_latest < 0 and f_prev > 0:
            lines.append("  ⚠️ 외국인 買→売 전환 (직전 주 대비)")

        cum_foreign = sum(r.get("foreigners", 0) for r in rows[:4])
        if abs(cum_foreign) >= 100000:
            lines.append(
                f"  4주 누적 외국인: {_fmt_oku(cum_foreign)}"
                f" — {'강한 매수' if cum_foreign > 0 else '강한 매도'} 기조"
            )

    return "\n".join(lines) if len(lines) > 1 else ""
