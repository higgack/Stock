"""Background pending-entry resolver — 5d + 15d + 30d multi-window.

The accuracy card on the dashboard counts only RESOLVED memory-log
entries. Resolution happens in two waves:

  (1) Initial 5-trading-day resolution. Pending entries enter the
      resolved set once 5 trading days of price data are available.
      yfinance fetches the realized return + sector-ETF alpha; both
      go into the entry tag (backward-compatible schema).
  (2) Long-horizon follow-up. Each resolved entry then gets 15d and
      30d outcomes appended once the corresponding window elapses.
      These live in an OUTCOMES section inside the entry body so the
      tag schema stays stable. Independent per-window — a flaky
      yfinance call at 15d doesn't block 30d.

Dashboard card / detail surfaces all three when available, stacked
in 5 → 15 → 30 order. Top-level accuracy stat stays 5d-only (user's
explicit choice — long-horizon adds variance the headline shouldn't
swallow).

Skips the LLM-generated 'reflection' field that the in-graph resolver
writes — that field exists for the memory feedback loop (the next
analysis of the same ticker reads past reflections to inform its
prompt). Auto-resolved entries get a placeholder reflection so the
schema stays consistent.

Run as `python -m bot.auto_resolve` from the bot host (no arguments).
Prints a one-line summary to stdout for systemd / parent-process logs.
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.config import get_config

log = logging.getLogger("bot.auto_resolve")

# Screener uses a separate memory log (~/.tradingagents/memory/screener_
# memory.md) so its 6-18M thesis picks don't pollute the NOAH /ticker
# 5-day evaluator dashboard. Same TAG_RE format → TradingMemoryLog with
# overridden path resolves it with identical 5d/15d/30d window logic.
# Sector ETF alpha (_resolve_benchmark) used as-is per ticker.
_SCREENER_MEMORY_PATH = (
    Path.home() / ".tradingagents" / "memory" / "screener_memory.md"
)


_AUTO_RESOLVED_REFLECTION = (
    "(자동 해소 — 5거래일 윈도 경과로 백그라운드 resolver가 raw / alpha만 기록.)"
)

# Long-horizon evaluation windows. 5d stays in the entry tag; 15d and
# 30d are appended to the body's OUTCOMES section by a separate pass.
# Calendar-day gates: trading_days * ~1.4 + 3 buffer for weekends, so
# 15 → 21 calendar / 30 → 42 calendar. Gate just decides when to start
# attempting; the per-fetch readiness check inside _fetch_returns
# (len(stock) ≥ 2) determines whether to resolve at the attempted day.
_LONG_HORIZON_WINDOWS = (15, 30)
_LONG_HORIZON_GATE_CALENDAR_DAYS = {
    15: 21,
    30: 42,
}

# Screener 전용 horizon (캘린더 일수, 사용자 정책 2026-05-29):
# screener 는 6-18M thesis 라 NOAH /ticker 5거래일과 별개. 측정 기간을
# 한달/3달/6개월 (캘린더) 로. target_date 가 영업일이 아니면 yfinance 가
# 영업일만 반환하므로 자동으로 그 다음 영업일 close 가 사용된다.
_SCREENER_PASS1_CALENDAR_DAYS = 30   # 1개월
_SCREENER_LONG_HORIZON_WINDOWS = (90, 180)   # 3개월, 6개월
_SCREENER_GATE_CALENDAR_DAYS = {
    90: 93,    # +3 buffer (target+다음 영업일이 today 이하)
    180: 183,
}


def _fetch_returns_calendar(
    ticker: str, trade_date: str, calendar_days: int,
) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """Calendar-day variant of `_fetch_returns` for screener (long-horizon
    thesis). target = trade_date + calendar_days. yfinance 가 영업일만
    반환하므로, target 이 weekend/holiday 면 그 다음 영업일 close 가
    자동으로 첫 매칭 row 가 된다. raw/alpha = (close_target - close_start)
    / close_start, alpha 는 sector ETF 또는 SPY 대비.

    Returns (raw, alpha, actual_calendar_days) — actual_days 는 trade_date
    부터 사용된 close 까지의 캘린더 일수 (영업일 fallback 포함, 실제 측정
    구간). None on insufficient data."""
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        # +3 calendar-day buffer 로 target+next-bday 가 today 안에 들어왔는지
        if start + timedelta(days=calendar_days + 3) > datetime.now():
            return None, None, None
        end = datetime.now() + timedelta(days=1)
        end_str = end.strftime("%Y-%m-%d")

        benchmark_symbol = "SPY"
        try:
            from tradingagents.agents.utils.sector_strength_tools import _resolve_benchmark
            bm = _resolve_benchmark(ticker)
            if bm and bm[0]:
                benchmark_symbol = bm[0]
        except Exception:
            pass

        stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
        bench = yf.Ticker(benchmark_symbol).history(start=trade_date, end=end_str)
        if len(bench) < 2 and benchmark_symbol != "SPY":
            benchmark_symbol = "SPY"
            bench = yf.Ticker(benchmark_symbol).history(start=trade_date, end=end_str)
        if len(stock) < 2 or len(bench) < 2:
            return None, None, None

        target = start + timedelta(days=calendar_days)
        # tz-naive 비교를 위해 stock.index 를 date 로
        def _first_idx_on_or_after(df, t):
            for i, ts in enumerate(df.index):
                d = ts.date() if hasattr(ts, "date") else ts
                if d >= t.date():
                    return i
            return len(df) - 1  # target 이 미래면 마지막 close

        s_i = _first_idx_on_or_after(stock, target)
        b_i = _first_idx_on_or_after(bench, target)
        if s_i == 0 or b_i == 0:
            return None, None, None  # 시작 close 와 동일 → 측정 불가

        raw = float(
            (stock["Close"].iloc[s_i] - stock["Close"].iloc[0])
            / stock["Close"].iloc[0]
        )
        bench_ret = float(
            (bench["Close"].iloc[b_i] - bench["Close"].iloc[0])
            / bench["Close"].iloc[0]
        )
        alpha = raw - bench_ret
        used_date = stock.index[s_i]
        used = (used_date.date() if hasattr(used_date, "date") else used_date)
        actual_cal = (used - start.date()).days
        return raw, alpha, actual_cal
    except Exception as exc:
        log.warning("fetch_returns_calendar: %s %s+%dd failed: %s",
                    ticker, trade_date, calendar_days, exc)
        return None, None, None


def _fetch_returns(
    ticker: str, trade_date: str, holding_days: int = 5
) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """Mirror of TradingAgentsGraph._fetch_returns kept dependency-light so
    this script doesn't have to load the full langgraph stack.

    Alpha uses the ticker's matched SECTOR ETF when available (SOXX for
    semis, XLF for banks, TAN for solar, etc.), with SPY as fallback —
    same logic as trading_graph._fetch_returns, so in-graph resolutions
    and background auto-resolves produce identical numbers."""
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        # Readiness gate — tightened from the old `holding_days + 7`
        # calendar-day buffer (= 12 days for the default 5-trading-day
        # window). The old gate kept JPM 2026-05-09 in 'pending' through
        # 5/18 even though all 5 trading days (5/11–5/15) were already
        # in yfinance — because end_calc = 5/9 + 12 = 5/21 > today (5/18)
        # so the function bailed before even attempting the fetch.
        # The +3 calendar-day buffer is the minimum that absorbs a
        # weekend transition: a Friday-or-Saturday trade_date plus the
        # subsequent weekend still has 5 trading days settle within
        # holding_days + 3 calendar days. Past this gate we attempt the
        # fetch and rely on the per-fetch readiness check below to
        # determine whether enough closes actually landed.
        if start + timedelta(days=holding_days + 3) > datetime.now():
            return None, None, None
        # Fetch end = today + 1 day so we catch closes that just landed.
        # Bigger windows wasted bandwidth without buying anything — the
        # readiness check below caps the number of closes we actually use.
        end = datetime.now() + timedelta(days=1)
        end_str = end.strftime("%Y-%m-%d")

        benchmark_symbol = "SPY"
        try:
            from tradingagents.agents.utils.sector_strength_tools import _resolve_benchmark
            bm = _resolve_benchmark(ticker)
            if bm and bm[0]:
                benchmark_symbol = bm[0]
        except Exception:
            pass

        stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
        bench = yf.Ticker(benchmark_symbol).history(start=trade_date, end=end_str)
        # m9 (2026-05-29 audit): thin sector ETF (<2 closes) → SPY fallback
        # so a sparsely-traded benchmark can't block an otherwise-resolvable
        # entry forever. raw return is a unitless ratio → alpha stays valid
        # (broad-market instead of sector benchmark). Mirrors the in-graph
        # _fetch_returns copy in trading_graph.py.
        if len(bench) < 2 and benchmark_symbol != "SPY":
            log.info("fetch_returns: %s benchmark %s thin (<2 closes) — SPY fallback",
                     ticker, benchmark_symbol)
            benchmark_symbol = "SPY"
            bench = yf.Ticker(benchmark_symbol).history(start=trade_date, end=end_str)
        # Require at least 2 closes (start + 1 outcome day) — same lenient
        # minimum the old code used. actual_days below caps at whatever
        # is available so a 5-trading-day query that only finds 4 closes
        # returns a 4-day return rather than blocking the entry forever.
        # JPM 2026-05-09 surfaced this: by 2026-05-18 yfinance has 5
        # closes (5/11-5/15) but the 6th (5/18) lands only after US close
        # 04:00 KST 5/19. A 5-day-only requirement keeps the entry pending
        # for an extra day with no real upside.
        if len(stock) < 2 or len(bench) < 2:
            return None, None, None
        # actual_days = min(holding_days, available - 1) so we cap at
        # the holding_days target but accept partial windows. The dashboard
        # outcome line will read 'days=N' from the log if you want to spot
        # partial-window resolutions later.
        actual_days = min(holding_days, len(stock) - 1, len(bench) - 1)
        raw = float(
            (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
            / stock["Close"].iloc[0]
        )
        bench_ret = float(
            (bench["Close"].iloc[actual_days] - bench["Close"].iloc[0])
            / bench["Close"].iloc[0]
        )
        if math.isnan(raw) or math.isnan(bench_ret):
            log.warning("fetch_returns: %s/%s — NaN in Close (yfinance data gap)", ticker, trade_date)
            return None, None, None
        log.info(
            "fetch_returns: %s/%s — raw=%.4f bench=%s ret=%.4f alpha=%.4f days=%d",
            ticker, trade_date, raw, benchmark_symbol, bench_ret, raw - bench_ret,
            actual_days,
        )
        return raw, raw - bench_ret, actual_days
    except Exception as exc:
        log.warning("fetch_returns: %s on %s failed: %s", ticker, trade_date, exc)
        return None, None, None


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        level=logging.INFO,
    )

    cfg = get_config()
    memory = TradingMemoryLog(cfg)
    all_entries = memory.load_entries()
    pending = [e for e in all_entries if e.get("pending")]
    resolved = [e for e in all_entries if not e.get("pending")]

    # ── Pass 1: pending → resolved (5-day window) ──────────────────
    pass1_updates = []
    for entry in pending:
        ticker = entry.get("ticker") or ""
        trade_date = entry.get("date") or ""
        if not ticker or not trade_date:
            continue
        raw, alpha, days = _fetch_returns(ticker, trade_date, holding_days=5)
        if raw is None:
            continue
        pass1_updates.append({
            "ticker": ticker, "trade_date": trade_date,
            "raw_return": raw, "alpha_return": alpha, "holding_days": days,
            "reflection": _AUTO_RESOLVED_REFLECTION,
        })

    if pending:
        log.info(
            "auto_resolve pass1 (5d): scanned %d pending → %d ready",
            len(pending), len(pass1_updates),
        )
    if pass1_updates:
        memory.batch_update_with_outcomes(pass1_updates)

    # ── Pass 2: resolved → long-horizon (15d, 30d) ─────────────────
    # Re-load AFTER pass1 so newly-resolved entries get considered for
    # 15d in the same run (rare — typically a 15d-ready entry already
    # had its 5d resolved in a prior cycle, but harmless to re-check).
    resolved_after_p1 = [e for e in memory.load_entries() if not e.get("pending")]
    pass2_updates = []
    for entry in resolved_after_p1:
        ticker = entry.get("ticker") or ""
        trade_date = entry.get("date") or ""
        if not ticker or not trade_date:
            continue
        existing_days = {o["days"] for o in (entry.get("outcomes_extra") or [])}
        for window in _LONG_HORIZON_WINDOWS:
            if window in existing_days:
                continue  # already resolved at this window
            # Calendar-day gate per window. Conservative ratio: 1 trading
            # day ≈ 1.4 calendar days + 3-day buffer for weekends / holidays.
            try:
                start = datetime.strptime(trade_date, "%Y-%m-%d")
            except ValueError:
                continue
            gate_days = _LONG_HORIZON_GATE_CALENDAR_DAYS[window]
            if start + timedelta(days=gate_days) > datetime.now():
                continue  # window not yet elapsed
            raw, alpha, days = _fetch_returns(ticker, trade_date, holding_days=window)
            if raw is None:
                continue
            pass2_updates.append({
                "ticker": ticker, "trade_date": trade_date,
                "days": window, "raw_return": raw, "alpha_return": alpha,
            })

    if resolved_after_p1:
        log.info(
            "auto_resolve pass2 (15d+30d): scanned %d resolved → %d long-horizon ready",
            len(resolved_after_p1), len(pass2_updates),
        )
    if pass2_updates:
        memory.batch_append_long_horizon_outcomes(pass2_updates)

    # ── Screener pass: 1개월 → 3개월 → 6개월 (캘린더, screener_memory.md)
    # screener 는 6-18M thesis (사용자 정책 2026-05-29) — NOAH /ticker 5거래일
    # 과 별개 horizon. _fetch_returns_calendar 가 target = trade_date +
    # N캘린더일 의 첫 영업일 close 를 사용 (weekend/holiday 자동 fallback).
    # Idempotent. screener 메모리 미존재 시 skip.
    scr_pass1_updates: list[dict] = []
    scr_pass2_updates: list[dict] = []
    if _SCREENER_MEMORY_PATH.exists():
        log.info("auto_resolve: scanning screener_memory.md")
        scr_cfg = dict(cfg)
        scr_cfg["memory_log_path"] = str(_SCREENER_MEMORY_PATH)
        scr_memory = TradingMemoryLog(scr_cfg)
        scr_entries = scr_memory.load_entries()
        scr_pending = [e for e in scr_entries if e.get("pending")]
        # Pass 1: 1개월 (30 캘린더일)
        for entry in scr_pending:
            ticker = entry.get("ticker") or ""
            trade_date = entry.get("date") or ""
            if not ticker or not trade_date:
                continue
            raw, alpha, days = _fetch_returns_calendar(
                ticker, trade_date, _SCREENER_PASS1_CALENDAR_DAYS,
            )
            if raw is None:
                continue
            scr_pass1_updates.append({
                "ticker": ticker, "trade_date": trade_date,
                "raw_return": raw, "alpha_return": alpha, "holding_days": days,
                "reflection": _AUTO_RESOLVED_REFLECTION,
            })
        if scr_pending:
            log.info(
                "auto_resolve screener pass1 (1m): scanned %d pending → %d ready",
                len(scr_pending), len(scr_pass1_updates),
            )
        if scr_pass1_updates:
            scr_memory.batch_update_with_outcomes(scr_pass1_updates)

        # Pass 2: 3개월(90 캘린더) + 6개월(180 캘린더)
        scr_resolved_after_p1 = [
            e for e in scr_memory.load_entries() if not e.get("pending")
        ]
        for entry in scr_resolved_after_p1:
            ticker = entry.get("ticker") or ""
            trade_date = entry.get("date") or ""
            if not ticker or not trade_date:
                continue
            existing_days = {o["days"] for o in (entry.get("outcomes_extra") or [])}
            for window in _SCREENER_LONG_HORIZON_WINDOWS:
                if window in existing_days:
                    continue
                try:
                    start = datetime.strptime(trade_date, "%Y-%m-%d")
                except ValueError:
                    continue
                gate_days = _SCREENER_GATE_CALENDAR_DAYS[window]
                if start + timedelta(days=gate_days) > datetime.now():
                    continue
                raw, alpha, days = _fetch_returns_calendar(
                    ticker, trade_date, window,
                )
                if raw is None:
                    continue
                scr_pass2_updates.append({
                    "ticker": ticker, "trade_date": trade_date,
                    "days": window, "raw_return": raw, "alpha_return": alpha,
                })
        if scr_resolved_after_p1:
            log.info(
                "auto_resolve screener pass2 (3m+6m): scanned %d resolved → %d ready",
                len(scr_resolved_after_p1), len(scr_pass2_updates),
            )
        if scr_pass2_updates:
            scr_memory.batch_append_long_horizon_outcomes(scr_pass2_updates)

    # ── Summary + dashboard regen ──────────────────────────────────
    if not pass1_updates and not pass2_updates and not scr_pass1_updates and not scr_pass2_updates:
        print(
            f"auto_resolve: scanned {len(pending)} pending + "
            f"{len(resolved_after_p1)} resolved — nothing ready "
            "(windows not elapsed or yfinance unavailable)"
        )
        return 0

    log.info(
        "auto_resolve: %d pass1 (5d) + %d pass2 (15d/30d) updates applied "
        "(+ screener: %d pass1 (1m) + %d pass2 (3m/6m))",
        len(pass1_updates), len(pass2_updates),
        len(scr_pass1_updates), len(scr_pass2_updates),
    )

    try:
        from bot.dashboard import regenerate_index, regenerate_screener_index
        regenerate_index()
        regenerate_screener_index()
        log.info("auto_resolve: dashboard regenerated")
    except Exception as exc:
        log.warning("auto_resolve: dashboard regen failed: %s", exc)

    print(
        f"auto_resolve: {len(pass1_updates)} resolved (5d) + "
        f"{len(pass2_updates)} long-horizon (15d/30d) updates"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
