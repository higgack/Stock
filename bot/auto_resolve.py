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
import sys
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.config import get_config

log = logging.getLogger("bot.auto_resolve")


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

    # ── Summary + dashboard regen ──────────────────────────────────
    if not pass1_updates and not pass2_updates:
        print(
            f"auto_resolve: scanned {len(pending)} pending + "
            f"{len(resolved_after_p1)} resolved — nothing ready "
            "(windows not elapsed or yfinance unavailable)"
        )
        return 0

    log.info(
        "auto_resolve: %d pass1 (5d) + %d pass2 (15d/30d) updates applied",
        len(pass1_updates), len(pass2_updates),
    )

    try:
        from bot.dashboard import regenerate_index
        regenerate_index()
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
