"""US institutional holders client — yfinance 13F-sourced top holders.

yfinance exposes SEC 13F-derived institutional ownership data:
  - major_holders: summary (% insiders, % institutions, total # filers)
  - institutional_holders: top 10 institutions (name, shares, %, date, value)

No API key required. 12h disk cache per (ticker, date).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.us_holders")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "us_holders"
_CACHE_TTL_HOURS = 12
_TIMEOUT = 15


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


def _is_us_ticker(ticker: str) -> bool:
    if not ticker:
        return False
    parts = ticker.split(".")
    if len(parts) == 1:
        return True
    return parts[-1].upper() not in (
        "TW", "TWO", "KS", "KQ", "T", "HK", "SS", "SZ", "BJ",
        "L", "AS", "PA", "DE", "MI", "OL", "ST", "HE", "CO",
        "AX", "NS", "BO",
    )


def fetch_institutional_holders(ticker: str) -> Optional[dict]:
    """Fetch top institutional holders + summary from yfinance (13F-sourced).

    Returns:
        {
            "summary": {"pct_insiders": float, "pct_institutions": float,
                        "pct_float_institutions": float, "num_institutions": int},
            "top_holders": [{"name": str, "shares": int, "pct": float,
                             "value": int, "date": str}, ...],
        }
        or None on failure.
    """
    if not _is_us_ticker(ticker):
        return None

    sym = ticker.split(".")[0] if "." not in ticker else ticker
    ck = f"holders_{sym}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
    except Exception:
        return None

    result: dict = {"summary": {}, "top_holders": []}

    try:
        mh = t.major_holders
        if mh is not None and not mh.empty:
            for _, row in mh.iterrows():
                val = row.iloc[0]
                label = str(row.iloc[1]) if len(row) > 1 else ""
                label_lower = label.lower()
                if isinstance(val, str) and "%" in val:
                    val = float(val.replace("%", "").strip())
                elif isinstance(val, (int, float)):
                    val = float(val)
                else:
                    continue
                if "insider" in label_lower:
                    result["summary"]["pct_insiders"] = val
                elif "institution" in label_lower and "float" in label_lower:
                    result["summary"]["pct_float_institutions"] = val
                elif "institution" in label_lower:
                    if val > 1000:
                        result["summary"]["num_institutions"] = int(val)
                    else:
                        result["summary"]["pct_institutions"] = val
    except Exception:
        pass

    try:
        ih = t.institutional_holders
        if ih is not None and not ih.empty:
            for _, row in ih.head(10).iterrows():
                entry: dict = {}
                for col in ih.columns:
                    v = row[col]
                    col_lower = col.lower()
                    if "holder" in col_lower:
                        entry["name"] = str(v) if v is not None else "?"
                    elif "share" in col_lower:
                        entry["shares"] = int(v) if v is not None else 0
                    elif col_lower in ("pcthold", "pctheld", "% out", "pct"):
                        entry["pct"] = round(float(v) * 100, 2) if v is not None else 0
                    elif "value" in col_lower:
                        entry["value"] = int(v) if v is not None else 0
                    elif "date" in col_lower:
                        entry["date"] = str(v)[:10] if v is not None else ""
                if entry.get("name"):
                    result["top_holders"].append(entry)
    except Exception:
        pass

    if not result["summary"] and not result["top_holders"]:
        return None

    _cache_set(ck, result)
    return result


def format_holders_block(data: Optional[dict]) -> str:
    """Format institutional holders into a compact text block."""
    if not data:
        return ""
    lines: list[str] = []

    summary = data.get("summary") or {}
    if summary:
        parts = []
        pi = summary.get("pct_insiders")
        if pi is not None:
            parts.append(f"Insiders {pi:.1f}%")
        pinst = summary.get("pct_institutions")
        if pinst is not None:
            parts.append(f"Institutions {pinst:.1f}%")
        num = summary.get("num_institutions")
        if num:
            parts.append(f"{num:,} filers")
        if parts:
            lines.append(f"• Ownership summary: {' / '.join(parts)}")

    holders = data.get("top_holders") or []
    if holders:
        lines.append("• Top institutional holders (SEC 13F):")
        for h in holders[:8]:
            name = h.get("name", "?")
            pct = h.get("pct", 0)
            shares = h.get("shares", 0)
            dt = h.get("date", "")
            pct_str = f"{pct:.2f}%" if pct else "N/A"
            shares_str = f"{shares:,}" if shares else "N/A"
            dt_str = f" ({dt})" if dt else ""
            lines.append(f"  {name}: {pct_str} ({shares_str}주){dt_str}")

    return "\n".join(lines) if lines else ""
