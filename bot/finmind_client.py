"""FinMind client — free TW financial data (50+ datasets).

Covers the major TW gaps:
  - 재무제표: 손익계산서 / 재무상태표 / 현금흐름표
  - 밸류에이션: PER / PBR / 배당수익률 히스토리
  - 실적: 월매출 (TW unique — 상장사 10일 이내 월매출 공시 의무)
  - 주주: 집보戶 주권분산표 (TDCC shareholding dispersion)

API: https://api.finmindtrade.com/api/v4/data
Auth: token optional (300 req/hr without, 600 with free registration).
All data is free.  No key required for basic use.

12h disk cache per (ticker, dataset, date).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("bot.finmind")

_API = "https://api.finmindtrade.com/api/v4/data"
_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "finmind"
_CACHE_TTL_HOURS = 12
_TIMEOUT = 20


def _tw_code(ticker: str) -> Optional[str]:
    """Extract numeric code from '2330.TW' or '6488.TWO'."""
    if not ticker:
        return None
    parts = ticker.upper().split(".")
    if len(parts) == 2 and parts[1] in ("TW", "TWO"):
        return parts[0]
    return None


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


def _fetch(dataset: str, stock_id: str, start: str, **extra) -> Optional[list]:
    """Call FinMind API.  Returns list of row dicts or None."""
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start,
        **extra,
    }
    try:
        resp = requests.get(_API, params=params, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.warning("finmind: %s returned %d", dataset, resp.status_code)
            return None
        j = resp.json()
        if j.get("status") != 200 or not j.get("data"):
            return None
        return j["data"]
    except Exception as exc:
        log.warning("finmind: %s fetch failed: %s", dataset, exc)
        return None


# ------------------------------------------------------------------
# Financial Statements
# ------------------------------------------------------------------

def fetch_income_statement(ticker: str, quarters: int = 8) -> Optional[list]:
    """Recent quarterly income statement rows."""
    code = _tw_code(ticker)
    if not code:
        return None
    ck = f"income_{code}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    start = (date.today() - timedelta(days=quarters * 100)).strftime("%Y-%m-%d")
    rows = _fetch("TaiwanStockFinancialStatements", code, start)
    if rows:
        _cache_set(ck, rows)
    return rows


def fetch_balance_sheet(ticker: str, quarters: int = 8) -> Optional[list]:
    """Recent quarterly balance sheet rows."""
    code = _tw_code(ticker)
    if not code:
        return None
    ck = f"balance_{code}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    start = (date.today() - timedelta(days=quarters * 100)).strftime("%Y-%m-%d")
    rows = _fetch("TaiwanStockBalanceSheet", code, start)
    if rows:
        _cache_set(ck, rows)
    return rows


def fetch_cashflow(ticker: str, quarters: int = 8) -> Optional[list]:
    """Recent quarterly cashflow statement rows."""
    code = _tw_code(ticker)
    if not code:
        return None
    ck = f"cashflow_{code}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    start = (date.today() - timedelta(days=quarters * 100)).strftime("%Y-%m-%d")
    rows = _fetch("TaiwanStockCashFlowsStatement", code, start)
    if rows:
        _cache_set(ck, rows)
    return rows


# ------------------------------------------------------------------
# Monthly Revenue (TW unique — mandatory 10-day disclosure)
# ------------------------------------------------------------------

def fetch_monthly_revenue(ticker: str, months: int = 13) -> Optional[list]:
    """Monthly revenue (營收) — TW-specific mandatory disclosure."""
    code = _tw_code(ticker)
    if not code:
        return None
    ck = f"revenue_{code}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    start = (date.today() - timedelta(days=months * 35)).strftime("%Y-%m-%d")
    rows = _fetch("TaiwanStockMonthRevenue", code, start)
    if rows:
        _cache_set(ck, rows)
    return rows


# ------------------------------------------------------------------
# Valuation (PER / PBR / Dividend Yield)
# ------------------------------------------------------------------

def fetch_per_pbr(ticker: str, days: int = 90) -> Optional[list]:
    """Daily PER / PBR / Dividend Yield history."""
    code = _tw_code(ticker)
    if not code:
        return None
    ck = f"perpbr_{code}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = _fetch("TaiwanStockPER", code, start)
    if rows:
        _cache_set(ck, rows)
    return rows


# ------------------------------------------------------------------
# Shareholding Dispersion (TDCC 集保戶股權分散表)
# ------------------------------------------------------------------

def fetch_shareholding(ticker: str) -> Optional[list]:
    """TDCC shareholding dispersion — weekly snapshot of holder-size tiers."""
    code = _tw_code(ticker)
    if not code:
        return None
    ck = f"holding_{code}_{date.today().isoformat()}.json"
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    start = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = _fetch("TaiwanStockShareholding", code, start)
    if rows:
        _cache_set(ck, rows)
    return rows


# ------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------

def format_monthly_revenue_block(rows: Optional[list]) -> str:
    """Format monthly revenue into a compact text block."""
    if not rows:
        return ""
    lines = ["• TW 월매출 (단위: 千元, FinMind/TWSE 공식):"]
    sorted_rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
    for r in sorted_rows[:13]:
        rev = r.get("revenue", 0)
        yoy = r.get("revenue_year_growth_rate")
        mom = r.get("revenue_month_growth_rate")
        rev_str = f"{rev:,.0f}" if rev else "N/A"
        yoy_str = f"YoY {yoy:+.1f}%" if yoy is not None else ""
        mom_str = f"MoM {mom:+.1f}%" if mom is not None else ""
        d = r.get("date", "?")
        parts = [rev_str]
        if yoy_str:
            parts.append(yoy_str)
        if mom_str:
            parts.append(mom_str)
        lines.append(f"  {d}: {' / '.join(parts)}")
    return "\n".join(lines)


def format_per_pbr_block(rows: Optional[list]) -> str:
    """Format latest PER/PBR snapshot."""
    if not rows:
        return ""
    sorted_rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
    latest = sorted_rows[0] if sorted_rows else {}
    per = latest.get("PER")
    pbr = latest.get("PBR")
    div_yield = latest.get("dividend_yield")
    if not any([per, pbr, div_yield]):
        return ""
    parts = []
    if per:
        parts.append(f"PER {per:.1f}x")
    if pbr:
        parts.append(f"PBR {pbr:.2f}x")
    if div_yield:
        parts.append(f"배당수익률 {div_yield:.2f}%")
    return f"• FinMind 밸류에이션 ({latest.get('date', '?')}): {' / '.join(parts)}"


def format_shareholding_block(rows: Optional[list]) -> str:
    """Format TDCC shareholding dispersion."""
    if not rows:
        return ""
    sorted_rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
    latest_date = sorted_rows[0].get("date", "") if sorted_rows else ""
    latest = [r for r in sorted_rows if r.get("date") == latest_date]
    if not latest:
        return ""
    lines = [f"• TDCC 집보戶 주권분산표 ({latest_date}):"]
    for r in latest:
        tier = r.get("HoldingSharesLevel", "?")
        pct = r.get("percent", 0)
        holders = r.get("people", 0)
        if pct and float(pct) >= 1.0:
            lines.append(f"  {tier}: {float(pct):.1f}% ({int(holders):,}명)")
    return "\n".join(lines) if len(lines) > 1 else ""
