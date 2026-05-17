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


def get_kr_foreign_ownership_trend(ticker: str, days_back: int = 30) -> Optional[dict]:
    """30-day foreign ownership rate trajectory for a KR ticker.

    Pulls KRX's daily foreign-ownership series (한도소진률 or 지분율
    depending on the column variant pykrx returns) and reports:
      - current_pct: today's foreign holding %
      - start_pct: the % `days_back` calendar days ago
      - change_pp: current - start (percentage points)
      - days: actual trading days covered

    Used to surface trend signals like "외국인이 꾸준히 늘리는 중
    (+1.2pp 30일)" that single-day net-purchase data doesn't show.
    Returns None on any failure (graceful)."""
    code = _normalize_code(ticker)
    if not code:
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"foreign_own_{code}_{today_str}_{days_back}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        return None

    end = date.today()
    start = end - timedelta(days=days_back + 10)
    try:
        df = stock.get_exhaustion_rates_of_foreign_investment(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: foreign ownership fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty or len(df) < 2:
        return None

    # pykrx column names vary by version; try the common candidates
    # in order of how likely they are to carry the rate.
    pct_col = None
    for c in ("지분율", "보유비중", "한도소진률", "한도소진율"):
        if c in df.columns:
            pct_col = c
            break
    if pct_col is None:
        return None

    try:
        current = float(df[pct_col].iloc[-1])
        start_val = float(df[pct_col].iloc[0])
    except Exception:
        return None

    result = {
        "current_pct": current,
        "start_pct": start_val,
        "change_pp": current - start_val,
        "days": int(len(df)),
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    return result


def get_kr_short_balance_trend(ticker: str, days_back: int = 30) -> Optional[dict]:
    """30-day short-selling balance trajectory for a KR ticker.

    Returns:
      - current_pct: today's short balance as % of float
      - start_pct: % at the start of the window
      - change_pp: current - start
      - days: trading days covered

    Rising short balance is a bearish positioning signal; collapsing
    short balance with rising price suggests a squeeze. Neither shows
    up in the daily net-purchase data. Returns None on any failure."""
    code = _normalize_code(ticker)
    if not code:
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"short_{code}_{today_str}_{days_back}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        return None

    end = date.today()
    start = end - timedelta(days=days_back + 10)
    try:
        df = stock.get_shorting_balance_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: short balance fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty or len(df) < 2:
        return None

    pct_col = None
    for c in ("비중", "공매도비중", "잔고비중"):
        if c in df.columns:
            pct_col = c
            break
    if pct_col is None:
        return None

    try:
        current = float(df[pct_col].iloc[-1])
        start_val = float(df[pct_col].iloc[0])
    except Exception:
        return None

    result = {
        "current_pct": current,
        "start_pct": start_val,
        "change_pp": current - start_val,
        "days": int(len(df)),
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    return result


def format_trend_for_prompt(foreign: Optional[dict], short: Optional[dict]) -> str:
    """Render the foreign-ownership and short-balance trends as a
    prompt block. Direction labels + magnitude qualifiers so the
    analyst reads it as a discrete signal."""
    lines: list[str] = []
    if foreign:
        cur = foreign.get("current_pct", 0)
        chg = foreign.get("change_pp", 0)
        days = foreign.get("days", "?")
        direction = "증가" if chg > 0 else "감소" if chg < 0 else "변동 없음"
        magnitude = "큰 폭" if abs(chg) >= 1.0 else "소폭" if abs(chg) >= 0.2 else "미미"
        lines.append(
            f"- 외국인 지분율 {days}거래일 추이: {cur:.2f}% (시작 {foreign.get('start_pct', 0):.2f}% → 변동 {chg:+.2f}pp, {magnitude} {direction})"
        )
    if short:
        cur = short.get("current_pct", 0)
        chg = short.get("change_pp", 0)
        days = short.get("days", "?")
        direction = "증가" if chg > 0 else "감소" if chg < 0 else "변동 없음"
        magnitude = "큰 폭" if abs(chg) >= 1.0 else "소폭" if abs(chg) >= 0.2 else "미미"
        squeeze_note = ""
        if chg < -0.5:
            squeeze_note = " — 잠재 squeeze 청산 진행 중"
        elif chg > 1.0:
            squeeze_note = " — 베어 positioning 강화"
        lines.append(
            f"- 공매도 잔고 {days}거래일 추이: {cur:.2f}% (시작 {short.get('start_pct', 0):.2f}% → 변동 {chg:+.2f}pp, {magnitude} {direction}){squeeze_note}"
        )
    return "\n".join(lines)
