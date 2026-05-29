"""M2 offline backtest — PM override two-layer conflict (read-only).

2026-05-29 audit M2. The in-graph discipline (`_enforce_pm_override_
discipline`, portfolio_manager.py) aligns the PM verdict UP to the analyst
majority (buy→Buy / sell→Sell). The analyzer layer (Fix F/G,
`_check_pm_override_required`, analyzer.py) independently forces HOLD when
the Trader disagrees (Fix G) or analysts are unanimous-against (Fix F).
When BOTH act on the same run the two policies CONFLICT — in-graph trusts
the analyst majority, Fix G trusts the Trader.

This module decides the policy FROM DATA, not guesswork:

  1. Walk the full-state logs the graph already writes
     (results_dir/<ticker>/TradingAgentsStrategy_logs/full_states_log_*.json).
  2. Replay both override layers (reusing the production functions — no
     logic re-implementation / drift) to find the conflict set.
  3. Join the conflict set with the resolved 5d outcomes in the memory log.
  4. Report, per candidate policy, whether following the analyst-aligned
     verdict or the forced Hold would have produced better 5d calls.

ZERO production impact — pure read-only analysis. Run on the bot host:

    cd ~/stock && .venv/bin/python -m bot.pm_override_audit
    # optional: --logs-dir, --memory, --json

Candidate policies compared:
  • legacy   — current behaviour (in-graph then Fix F/G; Hold wins conflicts)
  • policyA  — analyst-primacy: when the in-graph sentinel is present (PM
               already aligned to the analyst majority), DON'T let Fix G
               drag it back to Hold. Fix F/G still apply on the free-text
               path (no sentinel) where in-graph was bypassed.

Quality metric: for each conflict, look up the realized 5d raw return.
  • If policyA verdict is Buy  → realized P&L of following it = +raw.
  • If policyA verdict is Sell → realized P&L of following it = -raw.
  • legacy (Hold) → 0 (no position).
A net-positive policyA P&L means Fix G was wrongly suppressing good calls;
net-negative means the forced Hold was protective. The decision gate
(CLAUDE.md M2): adopt policyA only if it beats legacy on mean P&L AND
hit-rate AND keeps the Hold-rate delta within ±5pp.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

_HOME = Path.home() / ".tradingagents"
_DEFAULT_LOGS = _HOME / "logs"          # results_dir default (default_config.py)
_DEFAULT_MEMORY = _HOME / "memory" / "trading_memory.md"

_SENTINEL = "[PM override discipline 자동 보정]"

# Resolved memory tag: [YYYY-MM-DD | TICKER | Rating | +x.x% | +x.xp | Nd]
_RESOLVED_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|\s*"
    r"(Buy|Overweight|Hold|Underweight|Sell)\s*\|\s*"
    r"([+-][\d.]+)%\s*\|\s*([+-][\d.]+)p?\s*\|\s*(\d+)d\]"
)

_DIR = {  # rating → directional sign for P&L of "following the verdict"
    "Buy": +1, "Overweight": +1, "Hold": 0, "Underweight": -1, "Sell": -1,
}


def parse_resolved_outcomes(memory_path: Path) -> dict:
    """Return {(ticker, date): raw_return_fraction} from resolved memory
    tags. raw_return is the 5d (first-window) realized return as a fraction
    (e.g. +0.053 for +5.3%). Best-effort; missing file → {}."""
    out: dict[tuple[str, str], float] = {}
    if not memory_path.is_file():
        return out
    try:
        for m in _RESOLVED_RE.finditer(memory_path.read_text("utf-8")):
            date, ticker, _rating, raw_pct, _alpha, _days = m.groups()
            key = (ticker.strip(), date.strip())
            # First resolved window (5d) wins; later 15d/30d lines repeat the
            # tag with a different holding — keep the earliest (smallest d).
            if key not in out:
                out[key] = float(raw_pct) / 100.0
    except Exception:
        pass
    return out


def _state_from_log(log: dict) -> dict:
    """Reconstruct the dict shape the analyzer override functions expect
    from a full_states_log entry. The log stores the trader plan under
    'trader_investment_decision'; the analyzer reads 'trader_investment_
    plan' — remap it."""
    return {
        "market_report": log.get("market_report", ""),
        "sentiment_report": log.get("sentiment_report", ""),
        "news_report": log.get("news_report", ""),
        "fundamentals_report": log.get("fundamentals_report", ""),
        "investment_plan": log.get("investment_plan", ""),
        "trader_investment_plan": log.get("trader_investment_decision", ""),
        "trade_date": log.get("trade_date", ""),
        "company_of_interest": log.get("company_of_interest", ""),
    }


def replay(log: dict) -> Optional[dict]:
    """Replay both override layers on one state-log entry. Returns a dict
    describing the legacy vs policyA verdict + whether this is a conflict,
    or None if the entry can't be evaluated."""
    # Reuse the PRODUCTION functions — no logic drift.
    from bot.analyzer import (
        _check_pm_override_required,
        _extract_rating,
        _get_unanimous_analyst_direction,
    )

    decision = log.get("final_trade_decision", "") or ""
    if not decision:
        return None
    state = _state_from_log(log)
    in_graph_rating = _extract_rating(decision)  # post in-graph (sentinel-aware)
    sentinel = _SENTINEL in decision

    try:
        override_rating, override_note = _check_pm_override_required(state, decision)
    except Exception:
        override_rating, override_note = None, ""

    legacy_verdict = override_rating or in_graph_rating

    # policyA: if in-graph already aligned PM to the analyst majority
    # (sentinel present) and the analyzer layer is forcing Hold, respect the
    # in-graph result instead. Fix F/G unchanged on the no-sentinel path.
    if sentinel and override_rating == "Hold" and in_graph_rating not in (None, "Hold"):
        policyA_verdict = in_graph_rating
        conflict = True
    else:
        policyA_verdict = legacy_verdict
        conflict = False

    return {
        "ticker": (log.get("company_of_interest") or "").strip(),
        "trade_date": (log.get("trade_date") or "").strip(),
        "unanimous": _get_unanimous_analyst_direction(state),
        "in_graph_rating": in_graph_rating,
        "sentinel": sentinel,
        "legacy_verdict": legacy_verdict,
        "policyA_verdict": policyA_verdict,
        "conflict": conflict,
    }


def _verdict_pnl(verdict: Optional[str], raw_return: float) -> Optional[float]:
    """Realized P&L of FOLLOWING a verdict over the resolved window.
    Buy/Overweight → +raw, Sell/Underweight → -raw, Hold → 0."""
    sign = _DIR.get(verdict or "")
    if sign is None:
        return None
    return sign * raw_return


def run_backtest(logs_dir: Path, memory_path: Path) -> dict:
    outcomes = parse_resolved_outcomes(memory_path)
    pattern = str(logs_dir / "**" / "TradingAgentsStrategy_logs" / "full_states_log_*.json")
    files = glob.glob(pattern, recursive=True)

    n_total = 0
    n_conflict = 0
    verdict_counts = {"legacy": defaultdict(int), "policyA": defaultdict(int)}
    # Quality accumulation over conflicts that HAVE a resolved 5d outcome.
    joined = 0
    legacy_pnl_sum = 0.0
    policyA_pnl_sum = 0.0
    legacy_hits = 0
    policyA_hits = 0
    conflict_rows = []

    for fp in files:
        try:
            log = json.loads(Path(fp).read_text("utf-8"))
        except Exception:
            continue
        r = replay(log)
        if r is None:
            continue
        n_total += 1
        verdict_counts["legacy"][r["legacy_verdict"] or "N/A"] += 1
        verdict_counts["policyA"][r["policyA_verdict"] or "N/A"] += 1
        if not r["conflict"]:
            continue
        n_conflict += 1
        key = (r["ticker"], r["trade_date"])
        raw = outcomes.get(key)
        row = dict(r, raw_5d=raw)
        conflict_rows.append(row)
        if raw is None:
            continue
        joined += 1
        lp = _verdict_pnl(r["legacy_verdict"], raw)       # Hold → 0
        pp = _verdict_pnl(r["policyA_verdict"], raw)       # Buy/Sell → ±raw
        if lp is not None:
            legacy_pnl_sum += lp
            legacy_hits += 1 if lp > 0 else 0
        if pp is not None:
            policyA_pnl_sum += pp
            policyA_hits += 1 if pp > 0 else 0

    def _holdrate(counts):
        tot = sum(counts.values()) or 1
        return counts.get("Hold", 0) / tot

    return {
        "n_state_logs": len(files),
        "n_evaluated": n_total,
        "n_conflict": n_conflict,
        "conflict_rate": (n_conflict / n_total) if n_total else 0.0,
        "n_conflict_with_outcome": joined,
        "legacy_hold_rate": _holdrate(verdict_counts["legacy"]),
        "policyA_hold_rate": _holdrate(verdict_counts["policyA"]),
        "legacy_mean_pnl_on_conflicts": (legacy_pnl_sum / joined) if joined else None,
        "policyA_mean_pnl_on_conflicts": (policyA_pnl_sum / joined) if joined else None,
        "legacy_hit_rate_on_conflicts": (legacy_hits / joined) if joined else None,
        "policyA_hit_rate_on_conflicts": (policyA_hits / joined) if joined else None,
        "verdict_dist_legacy": dict(verdict_counts["legacy"]),
        "verdict_dist_policyA": dict(verdict_counts["policyA"]),
        "conflict_rows": conflict_rows,
    }


def _print_report(res: dict) -> None:
    print("=" * 64)
    print("M2 PM-override two-layer backtest (read-only)")
    print("=" * 64)
    print(f"state logs found        : {res['n_state_logs']}")
    print(f"evaluated runs          : {res['n_evaluated']}")
    print(f"M2 conflicts            : {res['n_conflict']}"
          f"  ({res['conflict_rate']*100:.1f}% of runs)")
    print(f"  …with resolved 5d outcome: {res['n_conflict_with_outcome']}")
    print(f"Hold-rate  legacy={res['legacy_hold_rate']*100:.1f}%"
          f"  policyA={res['policyA_hold_rate']*100:.1f}%"
          f"  (Δ {(res['policyA_hold_rate']-res['legacy_hold_rate'])*100:+.1f}pp)")
    lp = res["legacy_mean_pnl_on_conflicts"]
    pp = res["policyA_mean_pnl_on_conflicts"]
    lh = res["legacy_hit_rate_on_conflicts"]
    ph = res["policyA_hit_rate_on_conflicts"]
    if res["n_conflict_with_outcome"]:
        print("-" * 64)
        print("Quality on the conflict set (following the verdict, 5d realized):")
        print(f"  legacy  (forced Hold) : mean P&L {lp*100:+.2f}%"
              f"  hit-rate {lh*100:.0f}%   [Hold ⇒ 0% by definition]")
        print(f"  policyA (analyst-align): mean P&L {pp*100:+.2f}%"
              f"  hit-rate {ph*100:.0f}%")
        print("-" * 64)
        verdict = (
            "policyA BEATS legacy — Fix G was suppressing good calls"
            if (pp is not None and lp is not None and pp > lp)
            else "legacy ≥ policyA — the forced Hold was protective"
        )
        print(f"VERDICT: {verdict}")
        print("Gate (CLAUDE.md M2): adopt policyA only if mean P&L↑ AND "
              "hit-rate↑ AND |Hold-rate Δ| ≤ 5pp.")
    else:
        print("-" * 64)
        print("No conflicts with a resolved 5d outcome yet — let the Phase-0")
        print("audit JSONL + memory outcomes accumulate, then re-run.")
    print("=" * 64)


def main() -> None:
    ap = argparse.ArgumentParser(description="M2 PM-override backtest (read-only)")
    ap.add_argument("--logs-dir", default=str(_DEFAULT_LOGS),
                    help="results_dir holding <ticker>/TradingAgentsStrategy_logs/")
    ap.add_argument("--memory", default=str(_DEFAULT_MEMORY),
                    help="trading_memory.md path")
    ap.add_argument("--json", action="store_true", help="emit raw JSON")
    args = ap.parse_args()

    res = run_backtest(Path(args.logs_dir), Path(args.memory))
    if args.json:
        # conflict_rows can be large; keep summary keys only for --json
        summary = {k: v for k, v in res.items() if k != "conflict_rows"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_report(res)


if __name__ == "__main__":
    main()
