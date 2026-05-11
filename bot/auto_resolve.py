"""Background pending-entry resolver.

The accuracy card on the dashboard counts only RESOLVED memory-log
entries. Resolution normally happens at the start of the next analysis
of the same ticker (TradingAgentsGraph._resolve_pending_entries) — but
if the user analyzes a ticker once and never returns, that pending
entry stays pending forever and never enters the accuracy stats.

This script walks ALL pending entries (across every ticker) and resolves
any whose 5-trading-day window has elapsed: fetches the realized return
and SPY-relative alpha via yfinance, writes them back to the memory log,
and triggers a dashboard regen so the card updates.

Skips the LLM-generated 'reflection' field that the in-graph resolver
writes — that field exists for the memory feedback loop (the next
analysis of the same ticker reads past reflections to inform its
prompt). Auto-resolved entries get a placeholder reflection so the
schema stays consistent; if the user later re-analyzes that ticker,
the pre-existing reflection placeholder is fine — past_context just
sees a less rich entry, no crash.

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
        # Buffer for weekends / holidays so a 5-trading-day window has 7
        # calendar days of price data to slice from.
        end = start + timedelta(days=holding_days + 7)
        if end > datetime.now():
            return None, None, None
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
        if len(stock) < 2 or len(bench) < 2:
            return None, None, None

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
    pending = memory.get_pending_entries()
    if not pending:
        print("auto_resolve: 0 pending entries — nothing to do")
        return 0

    log.info("auto_resolve: scanning %d pending entries", len(pending))
    updates = []
    for entry in pending:
        ticker = entry.get("ticker") or ""
        trade_date = entry.get("date") or ""
        if not ticker or not trade_date:
            continue
        raw, alpha, days = _fetch_returns(ticker, trade_date)
        if raw is None:
            continue  # window not yet elapsed, or yfinance unavailable
        updates.append({
            "ticker": ticker,
            "trade_date": trade_date,
            "raw_return": raw,
            "alpha_return": alpha,
            "holding_days": days,
            "reflection": _AUTO_RESOLVED_REFLECTION,
        })

    if not updates:
        print(
            f"auto_resolve: scanned {len(pending)} pending — none ready "
            "(window not elapsed or yfinance unavailable)"
        )
        return 0

    memory.batch_update_with_outcomes(updates)
    log.info("auto_resolve: resolved %d / %d pending entries", len(updates), len(pending))

    # Trigger dashboard regen so the accuracy card shows the new resolutions
    # immediately. Failure here is non-fatal — the regen will still happen
    # on the next successful analysis.
    try:
        from bot.dashboard import regenerate_index
        regenerate_index()
        log.info("auto_resolve: dashboard regenerated")
    except Exception as exc:
        log.warning("auto_resolve: dashboard regen failed: %s", exc)

    print(
        f"auto_resolve: resolved {len(updates)} / {len(pending)} pending entries"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
