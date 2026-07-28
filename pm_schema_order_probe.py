#!/usr/bin/env python3
"""Fix 3 behavioral A/B probe — does reasoning-before-enum reduce the
text↔enum divergence that produced ALAB 2026-05-26 (PM thesis argued
Hold, rating enum emitted Buy)?

Runs ONLY on a host with GOOGLE_API_KEY + langchain_google_genai (the
production VM) — the analysis sandbox has neither. Self-contained A/B:
binds the SAME decision model with two PortfolioDecision variants —

  • EnumFirst       (rating field declared FIRST — the old order)
  • ReasoningFirst  (investment_thesis first, rating after — Fix 3)

— feeds an identical divergence-tempting prompt (the ALAB scenario:
analysts lean Hold on an overbought extreme, but a loud "next Nvidia"
bull narrative tempts a Buy), invokes each K times, and measures how
often the returned `rating` enum CONTRADICTS the direction its own
`investment_thesis` argues. Lower mismatch on ReasoningFirst confirms
both that Gemini honours field-declaration order AND that the reorder
helps.

Usage (on the VM):
    python3 pm_schema_order_probe.py            # 8 trials per arm
    python3 pm_schema_order_probe.py --trials 12
    python3 pm_schema_order_probe.py --model gemini-2.5-pro

Prints a mismatch-rate table + sample mismatches. Read-only: makes no
code changes, writes nothing, only LLM calls (small — costs a few cents).
"""

from __future__ import annotations

import argparse
import os
import sys
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


# ── API key bootstrap (mirror cache_cost_diagnostic._ensure_api_key) ────────
def _ensure_api_key() -> bool:
    """Load GOOGLE_API_KEY from the shell env or ~/stock/.env etc. into the
    process. Never prints the value. Returns True when a key is present."""
    if os.environ.get("GOOGLE_API_KEY"):
        return True
    for env_path in (
        Path.home() / "stock" / ".env",
        Path.home() / ".env",
        Path(__file__).resolve().parent / ".env",
    ):
        try:
            if not env_path.is_file():
                continue
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GOOGLE_API_KEY=") and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["GOOGLE_API_KEY"] = val
                        return True
        except Exception:
            continue
    return bool(os.environ.get("GOOGLE_API_KEY"))


class _Rating(str, Enum):
    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


# ── A: old order — rating enum declared FIRST ───────────────────────────────
class EnumFirst(BaseModel):
    rating: _Rating = Field(description="The final position rating. One of Buy / Overweight / Hold / Underweight / Sell.")
    executive_summary: str = Field(description="A concise action plan. Two to four sentences.")
    investment_thesis: str = Field(description="Detailed reasoning anchored in the analysts' debate.")


# ── B: Fix 3 — reasoning FIRST, rating after ────────────────────────────────
class ReasoningFirst(BaseModel):
    investment_thesis: str = Field(description="Detailed reasoning anchored in the analysts' debate. Write this BEFORE settling on the rating.")
    rating: _Rating = Field(description="The final position rating that follows from the investment thesis above. One of Buy / Overweight / Hold / Underweight / Sell.")
    executive_summary: str = Field(description="A concise action plan. Two to four sentences.")


# ── Divergence-tempting fixture (the ALAB 2026-05-26 scenario) ──────────────
_PROMPT = """You are the Portfolio Manager. Decide the final 5-trading-day position rating for ALAB (Astera Labs).

Analyst inputs (all four lean toward caution on a 5-day horizon):
- Market: Strong uptrend BUT RSI(14)=79.2 (overbought), price 77% above the 50-day SMA, above the upper Bollinger band. Conclusion: HOLD for the 5-day horizon; expect a near-term pullback.
- Sentiment: Loud "next Nvidia" AI narrative, analysts bullish long-term. BUT current price $306.88 is 20.5% ABOVE the Wall Street target $244.10. Conclusion: HOLD — consensus target is stale, not a 5-day driver.
- News: No fresh company-specific catalyst in the window. Conclusion: HOLD.
- Fundamentals: Excellent growth (FY25 revenue +115%) BUT PER 208.8 / Fwd PER 73 — extreme valuation. Conclusion: HOLD on the 5-day horizon.

The bull case is genuinely exciting (CXL moat, AI data-center secular growth). But every analyst flagged that the 5-trading-day setup is overbought and over-valued, and the Trader proposed Hold (no new entry). Decide the rating for the NEXT 5 TRADING DAYS, write the investment thesis grounded in the evidence above, and a short executive action plan."""


def _thesis_direction(thesis: str) -> str:
    """Crude direction judge for the thesis prose: 'buy' / 'sell' / 'hold'.
    Looks for the concluding stance — bullish vs bearish vs neutral language."""
    t = (thesis or "").lower()
    # Strong neutral/hold signals first (the thesis SHOULD conclude Hold here).
    hold_cues = ("hold", "neutral", "wait", "sit tight", "no new entry",
                 "stay on the sidelines", "maintain", "관망", "보유", "유지")
    sell_cues = ("sell", "underweight", "trim", "reduce", "exit", "downside",
                 "overvalued", "overbought pullback", "매도", "축소")
    buy_cues = ("buy", "overweight", "add", "accumulate", "enter", "초과",
                "비중확대", "매수")
    # Weight the CONCLUDING third of the thesis more (final stance).
    tail = t[len(t) * 2 // 3:]
    def score(cues, text):
        return sum(text.count(c) for c in cues)
    h = score(hold_cues, t) + 2 * score(hold_cues, tail)
    s = score(sell_cues, t) + 2 * score(sell_cues, tail)
    b = score(buy_cues, t) + 2 * score(buy_cues, tail)
    if h >= s and h >= b:
        return "hold"
    if s > b:
        return "sell"
    return "buy"


def _rating_direction(rating: str) -> str:
    r = (rating or "").lower()
    if r in ("buy", "overweight"):
        return "buy"
    if r in ("sell", "underweight"):
        return "sell"
    return "hold"


def _run_arm(llm_base, schema, label: str, trials: int) -> dict:
    from tradingagents.agents.utils.structured import bind_structured  # type: ignore
    structured = bind_structured(llm_base, schema, label)
    results = {"label": label, "n": 0, "mismatch": 0, "ratings": [], "samples": []}
    for i in range(trials):
        try:
            out = structured.invoke(_PROMPT)
        except Exception as exc:
            print(f"  [{label}] trial {i+1} failed: {exc}")
            continue
        rating = out.rating.value if hasattr(out, "rating") else "?"
        thesis = getattr(out, "investment_thesis", "") or ""
        rd, td = _rating_direction(rating), _thesis_direction(thesis)
        results["n"] += 1
        results["ratings"].append(rating)
        if rd != td:
            results["mismatch"] += 1
            if len(results["samples"]) < 3:
                results["samples"].append(
                    f"rating={rating} (dir={rd}) vs thesis-dir={td}: "
                    f"…{thesis[-180:].strip()}"
                )
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=8, help="trials per arm")
    ap.add_argument("--model", default="gemini-2.5-pro")
    args = ap.parse_args()

    if not _ensure_api_key():
        print("GOOGLE_API_KEY not found (shell env or ~/stock/.env). Run on the VM.")
        return 1
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except Exception as exc:
        print(f"langchain_google_genai unavailable ({exc}). Run inside the bot venv on the VM.")
        return 1
    # Make the tradingagents package importable when run from repo root.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "TradingAgents"))

    llm = ChatGoogleGenerativeAI(model=args.model, temperature=0.4)

    print(f"PM schema-order A/B — model={args.model}, {args.trials} trials/arm")
    print("Hypothesis: ReasoningFirst should show fewer rating↔thesis mismatches.\n")

    arm_a = _run_arm(llm, EnumFirst, "EnumFirst(old)", args.trials)
    arm_b = _run_arm(llm, ReasoningFirst, "ReasoningFirst(Fix3)", args.trials)

    print("\n================ RESULT ================")
    for arm in (arm_a, arm_b):
        n = arm["n"] or 1
        pct = 100.0 * arm["mismatch"] / n
        print(f"{arm['label']:>22}: {arm['mismatch']}/{arm['n']} mismatched "
              f"({pct:.0f}%)  ratings={arm['ratings']}")
    print("========================================")
    for arm in (arm_a, arm_b):
        if arm["samples"]:
            print(f"\n{arm['label']} mismatch samples:")
            for s in arm["samples"]:
                print(f"  - {s}")
    print("\nInterpretation: a meaningfully lower mismatch rate on "
          "ReasoningFirst confirms Gemini honours field order AND that "
          "Fix 3 reduces enum↔text divergence. Comparable rates → the "
          "reorder is harmless but the win comes from the Fix 1/2 guards.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
