"""KRX trading-flow data via pykrx — KR-only investor-type net purchases.

The KR market is dominated by foreign / institutional flow on short
horizons: a stock can have great fundamentals and still drop 10% in a
week when foreigners net-sell across the board. yfinance has nothing
on this; KRX is the authoritative source and pykrx wraps the public
endpoint cleanly.

What we expose to the analyzer for KR tickers:

- 5-day net purchases by investor type (foreign / institutional /
  individual), in KRW
- Direction labels ('우호적' / '비우호적' / '중립') for the prompt
  to read as plain text rather than parse the integer

Graceful degradation everywhere: pykrx import missing, network blip,
empty response — return None and let the rest of the analysis run
without the flow block. Cached on disk per (ticker, today) at
~/.tradingagents/cache/pykrx/ — KRX flow data only updates once daily
after the 3:30 KST close so the cache stays valid all day.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.pykrx")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "pykrx"
_CACHE_TTL_HOURS = 12  # KRX flow updates once a day; 12h is generous


def _normalize_code(ticker: str) -> Optional[str]:
    """Strip .KS/.KQ suffix, validate as 6-digit KRX code."""
    code = (ticker or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        return None
    return code


def get_kr_trading_flow(ticker: str, days_back: int = 5) -> Optional[dict]:
    """5-day investor-type net purchase summary for a KR ticker.

    Returns:
        dict with keys:
          - foreign_net (int, KRW)
          - institutional_net (int, KRW)
          - individual_net (int, KRW)
          - days (int, actual trading days covered — may be < days_back
            around holidays)
        OR None when pykrx is missing, the ticker is invalid, KRX
        returns empty, or any exception occurs.

    Sign convention: positive = net buy by that investor group over
    the window. Foreign + institutional + individual net purchases
    sum to ~zero by construction (every buy has a sell).
    """
    code = _normalize_code(ticker)
    if not code:
        return None

    # Cache check — flow data is daily, key by today's date.
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"flow_{code}_{today_str}_{days_back}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("pykrx: cache read failed for %s: %s", code, exc)

    try:
        from pykrx import stock
    except ImportError:
        log.warning("pykrx not installed; KR trading flow unavailable")
        return None

    end = date.today()
    # Buffer for weekends + KR public holidays in the window.
    start = end - timedelta(days=days_back + 10)

    try:
        df = stock.get_market_trading_value_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: trading flow fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty:
        log.info("pykrx: empty trading flow response for %s", code)
        return None

    # Take last N trading days. pykrx returns one row per trading day
    # (already excludes weekends/holidays), sorted ascending by date.
    recent = df.tail(days_back)
    if recent.empty:
        return None

    # Sum investor-type net values. pykrx column names differ slightly
    # by version; try the modern set first, fall back to alternates.
    def _sum_col(*candidates) -> int:
        for col in candidates:
            if col in recent.columns:
                try:
                    return int(recent[col].sum())
                except Exception:
                    continue
        return 0

    foreign = _sum_col("외국인합계", "외국인", "외국인투자자")
    institutional = _sum_col("기관합계", "기관", "기관투자자")
    individual = _sum_col("개인")

    result = {
        "foreign_net": foreign,
        "institutional_net": institutional,
        "individual_net": individual,
        "days": int(len(recent)),
    }

    # Cache (best-effort)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception as exc:
        log.warning("pykrx: cache write failed for %s: %s", code, exc)

    return result


def format_flow_for_prompt(flow: dict) -> str:
    """Render the trading-flow dict as a Korean bullet list for the
    instrument context. Adds a directional label so the analyst can
    read it as a discrete signal without parsing the integer."""
    if not flow:
        return ""

    def _label(val: int) -> str:
        if val > 0:
            return "순매수"
        if val < 0:
            return "순매도"
        return "중립"

    def _fmt_krw(val: int) -> str:
        """Render KRW with 억 unit. KRX flow numbers are typically in
        the tens-of-billions range; 억 is the natural read."""
        if val > 0:
            return f"+₩{val / 1e8:,.1f}억"
        if val < 0:
            return f"-₩{abs(val) / 1e8:,.1f}억"
        return "±₩0.0억"

    days = flow.get("days", "?")
    foreign = flow.get("foreign_net", 0)
    inst = flow.get("institutional_net", 0)
    indiv = flow.get("individual_net", 0)

    return (
        f"- 외국인 {days}거래일 누적: {_fmt_krw(foreign)} ({_label(foreign)})\n"
        f"- 기관 {days}거래일 누적: {_fmt_krw(inst)} ({_label(inst)})\n"
        f"- 개인 {days}거래일 누적: {_fmt_krw(indiv)} ({_label(indiv)})"
    )
