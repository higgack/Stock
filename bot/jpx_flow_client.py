"""JPX investor-type weekly flow client (JP 投資部門別 売買状況).

Fetches market-wide weekly net buy/sell by investor type from JPX:
  - 外国人 (Foreigners) — JP market largest driver
  - 投資信託 (Investment Trusts) — domestic mutual funds
  - 個人 (Individuals) — retail
  - 法人 (Corporations)
  - 証券会社 (Securities firms, proprietary)

Data is aggregate market-wide (TSE 1部+2部, not per-stock).
Published weekly (Thursday/Friday for prior week).

Source: JPX CSV at https://www.jpx.co.jp/markets/statistics-equities/
investor-type/nlsgeu000005b1d7-att/stock_val_by_sec_[1|2]s.csv

No API key required. 24h disk cache (weekly data).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.jpx_flow")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "jpx_flow"
_CACHE_TTL_HOURS = 96  # 4 days — JPX publishes Thu/Fri, ~2 refreshes/week
_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

_TSE1_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities"
    "/investor-type/nlsgeu000005b1d7-att/stock_val_by_sec_1s.csv"
)
_TSE2_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities"
    "/investor-type/nlsgeu000005b1d7-att/stock_val_by_sec_2s.csv"
)

_INVESTOR_MAP = {
    "法人": "corporations",
    "個人": "individuals",
    "外国人": "foreigners",
    "証券会社": "securities",
    "投資信託": "trusts",
}

_INVESTOR_MAP_EN = {
    "Proprietary": "securities",
    "Brokerage": "brokerage",
    "Foreigners": "foreigners",
    "Individuals": "individuals",
    "Investment Trusts": "trusts",
    "Corporations": "corporations",
    "Others": "others",
    "Securities Cos.": "securities",
}


def _cache_get(key: str) -> Optional[dict]:
    f = _CACHE_DIR / key
    if not f.exists():
        return None
    try:
        age_h = (time.time() - f.stat().st_mtime) / 3600
        if age_h >= _CACHE_TTL_HOURS:
            return None
        return json.loads(f.read_text())
    except Exception:
        return None


def _cache_set(key: str, data):
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (_CACHE_DIR / key).write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _parse_num(s: str) -> int:
    """Parse number string, handling commas and negative."""
    if not s or s.strip() in ("", "-", "N/A"):
        return 0
    cleaned = s.replace(",", "").replace(" ", "").strip()
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return 0


def _fetch_csv(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.warning("jpx_flow: %s returned %d", url, resp.status_code)
            return None
        for enc in ("shift_jis", "cp932", "utf-8"):
            try:
                return resp.content.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return resp.text
    except Exception as exc:
        log.warning("jpx_flow: fetch failed: %s", exc)
        return None


def _parse_flow_csv(text: str) -> Optional[list[dict]]:
    """Parse JPX investor-type CSV into weekly flow rows.

    Returns list of dicts sorted by date (newest first):
        [{"date": "YYYY/MM/DD", "foreigners": net_buy_sell, "trusts": ..., ...}]
    Values are in 百万円 (millions of yen).
    """
    if not text:
        return None

    lines = text.strip().splitlines()
    if len(lines) < 3:
        return None

    header_idx = None
    for i, line in enumerate(lines):
        if "売り" in line or "Sell" in line or "期間" in line or "Period" in line:
            header_idx = i
            break

    if header_idx is None:
        for i, line in enumerate(lines):
            if any(k in line for k in ("外国人", "Foreigners", "個人", "法人")):
                header_idx = max(0, i - 1)
                break

    if header_idx is None:
        return None

    reader = csv.reader(io.StringIO("\n".join(lines[header_idx:])))
    headers = []
    try:
        headers = next(reader)
    except StopIteration:
        return None

    investor_cols: dict[str, tuple[int, int]] = {}
    for col_idx, h in enumerate(headers):
        h_clean = h.strip()
        for jp_key, en_key in {**_INVESTOR_MAP, **_INVESTOR_MAP_EN}.items():
            if jp_key in h_clean:
                if en_key not in investor_cols:
                    investor_cols[en_key] = (col_idx, -1)
                else:
                    prev_buy, _ = investor_cols[en_key]
                    investor_cols[en_key] = (prev_buy, col_idx)
                break

    if not investor_cols:
        return None

    rows: list[dict] = []
    for csv_row in reader:
        if len(csv_row) < 3:
            continue
        date_str = csv_row[0].strip()
        if not date_str or not any(c.isdigit() for c in date_str):
            continue

        entry: dict = {"date": date_str}
        for inv_key, (buy_idx, sell_idx) in investor_cols.items():
            buy_val = _parse_num(csv_row[buy_idx]) if buy_idx >= 0 and buy_idx < len(csv_row) else 0
            sell_val = _parse_num(csv_row[sell_idx]) if sell_idx >= 0 and sell_idx < len(csv_row) else 0
            if sell_idx < 0:
                entry[inv_key] = buy_val
            else:
                entry[inv_key] = buy_val - sell_val
        rows.append(entry)

    return sorted(rows, key=lambda r: r.get("date", ""), reverse=True) if rows else None


def fetch_jpx_weekly_flow() -> Optional[list[dict]]:
    """Fetch recent weekly investor-type flow data from JPX.

    Returns list of weekly flow dicts (newest first), values in 百万円.
    """
    ck = f"jpx_flow_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    text = _fetch_csv(_TSE1_URL)
    if not text:
        text = _fetch_csv(_TSE2_URL)
    if not text:
        return None

    rows = _parse_flow_csv(text)
    if rows:
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
