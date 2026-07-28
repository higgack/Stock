"""HKEX Stock Connect daily flow client (backup for AKShare HSGT).

Fetches daily northbound/southbound net buy from HKEX official data:
  - Northbound (滬股通+深股通): mainland investors → HK stocks
  - Southbound (港股通): HK/foreign investors → mainland stocks

Serves as independent backup when AKShare is unavailable or stale.
Data is aggregate (not per-stock), daily with ~1 day lag.

API: HKEX mutual market data
No API key required. 12h disk cache.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.hkex_connect")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "hkex_connect"
_CACHE_TTL_HOURS = 12
_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_NORTH_URL = (
    "https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx"
    "?t=sh"
)
_SOUTH_URL = (
    "https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx"
    "?t=hk"
)

_TURNOVER_API = "https://www.hkex.com.hk/mutual-market/stock-connect/statistics/historical-daily"


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


def _parse_amount(s: str) -> Optional[float]:
    """Parse amount string like 'RMB12,345.67M' or 'HKD1,234.56M'.
    Returns value in millions."""
    if not s or s.strip() in ("", "-", "N/A"):
        return None
    cleaned = s.strip()
    for prefix in ("RMB", "HKD", "CNY", "HK$"):
        cleaned = cleaned.replace(prefix, "")
    cleaned = cleaned.replace(",", "").strip()
    is_billion = cleaned.endswith("B")
    cleaned = cleaned.rstrip("MB").strip()
    try:
        val = float(cleaned)
        if is_billion:
            val *= 1000
        return val
    except (ValueError, TypeError):
        return None


def _fetch_daily_turnover() -> Optional[dict]:
    """Fetch recent Stock Connect daily turnover from HKEX JSON API.

    Returns dict with northbound/southbound arrays of daily net buy.
    Values in RMB/HKD millions.
    """
    ck = f"hkex_turnover_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    try:
        nb_url = (
            "https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market"
            "/Stock-Connect/Statistics/Historical-Daily/Northbound.json"
        )
        sb_url = (
            "https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market"
            "/Stock-Connect/Statistics/Historical-Daily/Southbound.json"
        )

        result = {"northbound": [], "southbound": []}

        for direction, url in (("northbound", nb_url), ("southbound", sb_url)):
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                rows = data if isinstance(data, list) else data.get("data", [])
                for row in rows[-10:]:
                    dt = row.get("date") or row.get("Date") or ""
                    net = row.get("net_buy") or row.get("DailyQuotaBalance") or 0
                    if isinstance(net, str):
                        net = _parse_amount(net) or 0
                    result[direction].append({"date": dt, "net_buy": float(net)})
            except Exception as exc:
                log.warning("hkex_connect: %s fetch failed: %s", direction, exc)

        if result["northbound"] or result["southbound"]:
            _cache_set(ck, result)
            return result
    except Exception as exc:
        log.warning("hkex_connect: daily turnover fetch failed: %s", exc)

    return None


def fetch_stock_connect_flow() -> Optional[dict]:
    """Fetch Stock Connect aggregate flow (backup for AKShare HSGT).

    Returns:
        {
            "northbound_5d": float (RMB 억원),
            "southbound_5d": float (HKD 억원),
            "daily": [{"date": str, "nb": float, "sb": float}, ...],
            "source": "hkex",
        }
    """
    ck = f"hkex_flow_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    turnover = _fetch_daily_turnover()
    if not turnover:
        return None

    nb_list = turnover.get("northbound", [])
    sb_list = turnover.get("southbound", [])

    nb_5d = sum(r.get("net_buy", 0) for r in nb_list[-5:]) if nb_list else 0
    sb_5d = sum(r.get("net_buy", 0) for r in sb_list[-5:]) if sb_list else 0

    daily: list[dict] = []
    for i in range(min(5, max(len(nb_list), len(sb_list)))):
        nb_entry = nb_list[-(i + 1)] if i < len(nb_list) else {}
        sb_entry = sb_list[-(i + 1)] if i < len(sb_list) else {}
        dt = nb_entry.get("date") or sb_entry.get("date") or ""
        daily.append({
            "date": dt,
            "nb": nb_entry.get("net_buy", 0),
            "sb": sb_entry.get("net_buy", 0),
        })

    result = {
        "northbound_5d": nb_5d / 100,
        "southbound_5d": sb_5d / 100,
        "daily": daily,
        "source": "hkex",
    }
    _cache_set(ck, result)
    return result


def format_hkex_flow_block(flow: Optional[dict]) -> str:
    """Format HKEX Stock Connect flow into a compact text block."""
    if not flow:
        return ""
    nb = flow.get("northbound_5d")
    sb = flow.get("southbound_5d")
    if nb is None and sb is None:
        return ""

    lines = ["• HKEX Stock Connect 5거래일 (HKEX 공식):"]
    if nb is not None:
        sign = "+" if nb >= 0 else ""
        direction = "매수" if nb > 0 else "매도"
        lines.append(f"  Northbound (北向): {sign}{nb:.1f}億RMB ({direction})")
    if sb is not None:
        sign = "+" if sb >= 0 else ""
        direction = "매수" if sb > 0 else "매도"
        lines.append(f"  Southbound (南向): {sign}{sb:.1f}億HKD ({direction})")

    if nb is not None and sb is not None:
        if nb > 10 and sb > 10:
            lines.append("  ✅ 양방향 순매수 — 글로벌 리스크온 신호")
        elif nb < -10 and sb < -10:
            lines.append("  ⚠️ 양방향 순매도 — 글로벌 리스크오프 신호")

    return "\n".join(lines)
