"""TWSE / TPEx 三大法人 (institutional investor) daily flow client.

Fetches per-stock daily net buy/sell for the three institutional investor
categories:
  - 外資及陸資 (Foreign Investors incl. mainland China capital)
  - 投信 (Investment Trusts — mutual funds)
  - 自營商 (Dealers / proprietary traders)

Two exchanges:
  TWSE (listed, .TW suffix)  → T86 endpoint
  TPEx  (OTC,   .TWO suffix) → 3itrade_hedge endpoint

Data is in 股 (shares).  We convert to estimated NT$ via close price for
the LLM (prevents share-count-only confusion on magnitude).

No API key required.  12h disk cache per (ticker, date).
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

log = logging.getLogger("bot.twse_flow")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "twse_flow"
_CACHE_TTL_HOURS = 12
_TIMEOUT = 15

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*",
    "Referer": "https://www.twse.com.tw/",
}

_TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
_TPEX_3INSTO = (
    "https://www.tpex.org.tw/web/stock/3insto/daily_trade"
    "/3itrade_hedge_result.php"
)


def _normalize_code(ticker: str) -> tuple[Optional[str], str]:
    """Return (numeric code, exchange) from '2330.TW' or '6488.TWO'."""
    if not ticker:
        return None, ""
    parts = ticker.upper().split(".")
    if len(parts) != 2:
        return None, ""
    code, suffix = parts
    if suffix == "TW":
        return code, "TWSE"
    if suffix == "TWO":
        return code, "TPEx"
    return None, ""


def _parse_int(s: str) -> int:
    """Parse comma-separated int string, e.g. '10,000,000' → 10000000."""
    if not s or s.strip() in ("", "-", "N/A"):
        return 0
    return int(s.replace(",", "").strip())


# ------------------------------------------------------------------
# TWSE (listed)
# ------------------------------------------------------------------

def _fetch_twse_day(dt: date) -> Optional[dict]:
    """Fetch T86 三大法人買賣超日報 for one date.  Returns {code: row_dict}."""
    date_str = dt.strftime("%Y%m%d")
    try:
        resp = requests.get(
            _TWSE_T86,
            params={"date": date_str, "selectType": "ALL", "response": "json"},
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        j = resp.json()
        if j.get("stat") != "OK" or not j.get("data"):
            return None
    except Exception as exc:
        log.warning("twse_flow: TWSE T86 fetch failed for %s: %s", date_str, exc)
        return None

    result: dict[str, dict] = {}
    for row in j["data"]:
        if len(row) < 19:
            continue
        code = row[0].strip()
        result[code] = {
            "foreign_net": _parse_int(row[4]),
            "trust_net": _parse_int(row[10]),
            "dealer_net": _parse_int(row[11]),
            "total_net": _parse_int(row[18]),
        }
    return result


# ------------------------------------------------------------------
# TPEx (OTC)
# ------------------------------------------------------------------

def _fetch_tpex_day(dt: date) -> Optional[dict]:
    """Fetch TPEx 三大法人 for one date.  Returns {code: row_dict}."""
    roc_year = dt.year - 1911
    date_str = f"{roc_year}/{dt.month:02d}/{dt.day:02d}"
    try:
        resp = requests.get(
            _TPEX_3INSTO,
            params={"l": "zh-tw", "d": date_str, "se": "EW", "t": "D", "o": "json"},
            headers={
                **_HEADERS,
                "Referer": "https://www.tpex.org.tw/",
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        j = resp.json()
        rows = j.get("aaData")
        if not rows:
            return None
    except Exception as exc:
        log.warning("twse_flow: TPEx fetch failed for %s: %s", date_str, exc)
        return None

    result: dict[str, dict] = {}
    for row in rows:
        if len(row) < 14:
            continue
        code = str(row[0]).strip()
        result[code] = {
            "foreign_net": _parse_int(str(row[4])),
            "trust_net": _parse_int(str(row[8])),
            "dealer_net": _parse_int(str(row[12])),
            "total_net": (
                _parse_int(str(row[4]))
                + _parse_int(str(row[8]))
                + _parse_int(str(row[12]))
            ),
        }
    return result


# ------------------------------------------------------------------
# Walk-back to find recent trading days
# ------------------------------------------------------------------

def _recent_trading_days(n: int = 5) -> list[date]:
    """Return up to *n* recent dates that might be trading days (Mon-Fri)."""
    today = date.today()
    days: list[date] = []
    d = today
    attempts = 0
    while len(days) < n and attempts < 15:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
        attempts += 1
    return days


def _fetch_all_days(exchange: str, days: list[date]) -> list[Optional[dict]]:
    """Fetch multiple days sequentially (TWSE rate-limits concurrent)."""
    fetcher = _fetch_twse_day if exchange == "TWSE" else _fetch_tpex_day
    results: list[Optional[dict]] = []
    for d in days:
        data = fetcher(d)
        results.append(data)
        if data is not None:
            time.sleep(0.5)
    return results


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def fetch_institutional_flow(ticker: str) -> Optional[dict]:
    """Fetch 三大法人 daily + 5-day cumulative for a single ticker.

    Returns:
        {
            "today": {"foreign": int, "trust": int, "dealer": int, "total": int},
            "5d":    {"foreign": int, "trust": int, "dealer": int, "total": int},
            "daily": [{"date": "YYYY-MM-DD", "foreign": ..., ...}, ...],
            "unit": "shares",
        }
        or None on failure.  Values are in 股 (shares).
    """
    code, exchange = _normalize_code(ticker)
    if not code:
        return None

    cache_key = f"twse_flow_{code}_{date.today().isoformat()}.json"
    cache_file = _CACHE_DIR / cache_key
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                return cached if cached else None
        except Exception:
            pass

    days = _recent_trading_days(5)
    if not days:
        return None

    day_maps = _fetch_all_days(exchange, days)

    today_data: Optional[dict] = None
    daily: list[dict] = []
    cum = {"foreign": 0, "trust": 0, "dealer": 0, "total": 0}
    found_any = False

    for i, (d, dmap) in enumerate(zip(days, day_maps)):
        if dmap is None:
            continue
        row = dmap.get(code)
        if row is None:
            continue
        found_any = True
        entry = {
            "date": d.isoformat(),
            "foreign": row["foreign_net"],
            "trust": row["trust_net"],
            "dealer": row["dealer_net"],
            "total": row["total_net"],
        }
        daily.append(entry)
        cum["foreign"] += row["foreign_net"]
        cum["trust"] += row["trust_net"]
        cum["dealer"] += row["dealer_net"]
        cum["total"] += row["total_net"]
        if today_data is None:
            today_data = entry

    if not found_any:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("null")
        except Exception:
            pass
        return None

    result = {
        "today": today_data,
        "5d": cum,
        "daily": daily,
        "unit": "shares",
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass

    return result


def format_twse_flow_block(flow: dict, close_price: Optional[float] = None) -> str:
    """Format 三大法人 data into a text block for instrument context.

    If *close_price* (NT$) is provided, share counts are also shown as
    estimated NT$ amounts for magnitude context.
    """
    if not flow:
        return ""
    lines: list[str] = []

    def _fmt(shares: int) -> str:
        sign = "+" if shares >= 0 else ""
        s = f"{sign}{shares:,}股"
        if close_price and close_price > 0:
            est_ntd = shares * close_price
            if abs(est_ntd) >= 1e8:
                s += f" (≈{sign}{est_ntd / 1e8:.1f}億NT$)"
            elif abs(est_ntd) >= 1e4:
                s += f" (≈{sign}{est_ntd / 1e4:.0f}萬NT$)"
        return s

    today = flow.get("today") or {}
    if today:
        lines.append(f"• 최근 거래일 ({today.get('date', '?')}):")
        lines.append(f"  外資 {_fmt(today.get('foreign', 0))}")
        lines.append(f"  投信 {_fmt(today.get('trust', 0))}")
        lines.append(f"  自營商 {_fmt(today.get('dealer', 0))}")
        lines.append(f"  三大法人 합계 {_fmt(today.get('total', 0))}")

    cum = flow.get("5d") or {}
    if cum:
        lines.append(f"• 5거래일 누적:")
        lines.append(f"  外資 {_fmt(cum.get('foreign', 0))}")
        lines.append(f"  投信 {_fmt(cum.get('trust', 0))}")
        lines.append(f"  自營商 {_fmt(cum.get('dealer', 0))}")
        lines.append(f"  三大法人 합계 {_fmt(cum.get('total', 0))}")

        if close_price and close_price > 0:
            for label, key in (("外資", "foreign"), ("投信", "trust")):
                val_shares = cum.get(key, 0)
                est_ntd = val_shares * close_price
                if abs(est_ntd) < 1e8:
                    lines.append(
                        f"  ⚠️ {label} 5일 누적 ≈{est_ntd / 1e8:.2f}億NT$"
                        f" — ±1億NT$ 미만은 noise level, dominant variable 인용 주의"
                    )

        f5 = cum.get("foreign", 0)
        t5 = cum.get("trust", 0)
        d5 = cum.get("dealer", 0)

        if close_price and close_price > 0:
            f5_ntd = f5 * close_price
            t5_ntd = t5 * close_price
            if f5_ntd <= -1e8 and t5_ntd >= 1e8:
                lines.append(
                    f"  ⚠️ 外資 매도 vs 投信 매수 — 기관 간 방향 분리 주의"
                )
            elif f5_ntd >= 1e8 and t5_ntd >= 1e8:
                lines.append(
                    f"  ✅ 외자+투신 동반 매수 — 기관 합의 긍정"
                )

    daily = flow.get("daily") or []
    if len(daily) >= 2:
        lines.append(f"• 일별 추이 (최근→과거):")
        for entry in daily[:5]:
            d_str = entry.get("date", "?")
            lines.append(
                f"  {d_str}: 外{_fmt(entry.get('foreign', 0))}"
                f" / 投{_fmt(entry.get('trust', 0))}"
                f" / 自{_fmt(entry.get('dealer', 0))}"
            )

    return "\n".join(lines)
