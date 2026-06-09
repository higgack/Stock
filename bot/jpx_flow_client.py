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
    ("外国人", "foreigners"),
    ("外国法人", "foreigners"),
    ("Foreigners", "foreigners"),
    ("投資信託", "trusts"),
    ("Investment Trusts", "trusts"),
    ("個人", "individuals"),
    ("Individuals", "individuals"),
    ("事業法人", "corporations"),
    ("Corporations", "corporations"),
    ("自己", "securities"),
    ("証券会社", "securities"),
    ("Proprietary", "securities"),
    ("Securities", "securities"),
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
    YYMMWW: YY=western year last 2 digits, MM=month, WW=week number.
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


def _yymw_to_label(yymw: str) -> str:
    """'260504' → '2026/05 W4'."""
    yy, mm, ww = yymw[:2], yymw[2:4], yymw[4:6]
    return f"20{yy}/{mm} W{int(ww)}"


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
        return int(val)
    if isinstance(val, int):
        return val
    s = str(val).replace(",", "").replace(" ", "").strip()
    if not s or s in ("-", "N/A", ""):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _parse_xls_flow(data: bytes, date_label: str) -> Optional[dict]:
    """Parse a JPX investor-type weekly XLS into flow dict.

    Searches for 売り/買い/差引 columns + investor-type keyword rows,
    extracts net buy/sell per investor type.
    Returns {date, foreigners, trusts, individuals, corporations, securities}
    or None.
    """
    try:
        import xlrd  # noqa: heavy/optional
    except ImportError:
        log.warning("jpx_flow: xlrd not installed — pip install xlrd")
        return None

    try:
        wb = xlrd.open_workbook(file_contents=data)
        sheet = wb.sheet_by_index(0)

        # ── find header row with 売り / 買い / 差引 ──
        sell_col = buy_col = net_col = -1
        header_row = -1

        for r in range(min(sheet.nrows, 20)):
            for c in range(min(sheet.ncols, 30)):
                v = str(sheet.cell_value(r, c)).strip()
                if "売り" in v or v == "Sell":
                    sell_col = c
                    header_row = r
                elif "買い" in v or v == "Buy":
                    buy_col = c
                elif "差引" in v or "差し引" in v or v == "Net":
                    net_col = c

        if header_row < 0 or (sell_col < 0 and buy_col < 0 and net_col < 0):
            log.info("jpx_flow: no header row found in XLS")
            return None

        # ── try to extract actual date from title cells ──
        actual_date = date_label
        for r in range(min(header_row, 10)):
            for c in range(min(sheet.ncols, 10)):
                v = str(sheet.cell_value(r, c)).strip()
                m = re.search(r"(\d{4})[/.\-年](\d{1,2})[/.\-月](\d{1,2})", v)
                if m:
                    actual_date = (
                        f"{m.group(1)}/{m.group(2).zfill(2)}"
                        f"/{m.group(3).zfill(2)}"
                    )
                    break
            if actual_date != date_label:
                break

        # ── extract investor-type net values ──
        result: dict = {"date": actual_date}

        for r in range(header_row + 1, sheet.nrows):
            row_text = ""
            for c in range(min(sheet.ncols, 5)):
                v = str(sheet.cell_value(r, c)).strip()
                if v:
                    row_text += " " + v

            matched_key = None
            for kw, key in _INVESTOR_KEYWORDS:
                if kw in row_text and key not in result:
                    matched_key = key
                    break

            if not matched_key:
                continue

            net_val = None
            if net_col >= 0 and net_col < sheet.ncols:
                net_val = _parse_num_cell(sheet.cell_value(r, net_col))

            if net_val is None and buy_col >= 0 and sell_col >= 0:
                buy_v = _parse_num_cell(
                    sheet.cell_value(r, buy_col) if buy_col < sheet.ncols else None
                )
                sell_v = _parse_num_cell(
                    sheet.cell_value(r, sell_col) if sell_col < sheet.ncols else None
                )
                if buy_v is not None and sell_v is not None:
                    net_val = buy_v - sell_v

            if net_val is not None:
                result[matched_key] = net_val

        return result if len(result) > 1 else None
    except Exception as exc:
        log.warning("jpx_flow: XLS parse error: %s", exc)
        return None


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
    for url, yymw in links[:8]:
        fk = f"jpx_week_{yymw}.json"
        fc = _cache_get(fk, ttl_hours=_FILE_CACHE_TTL_HOURS)
        if fc:
            rows.append(fc)
            continue

        data = _download_xls(url)
        if not data:
            continue

        label = _yymw_to_label(yymw)
        parsed = _parse_xls_flow(data, label)
        if parsed:
            _cache_set(fk, parsed)
            rows.append(parsed)

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
