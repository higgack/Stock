"""Research Manager: turns the bull/bear debate into a structured investment plan for the trader."""

from __future__ import annotations

import logging

from tradingagents.agents.schemas import ResearchPlan, render_research_plan
from tradingagents.agents.utils.agent_utils import build_instrument_context, get_language_instruction
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


_rm_log = logging.getLogger("tradingagents.research_manager")


def create_research_manager(llm):
    structured_llm = bind_structured(llm, ResearchPlan, "Research Manager")

    def research_manager_node(state) -> dict:
        # F1-MVP Gemini context caching (2026-05-19). When analyzer.py
        # successfully created a CachedContent at run start, state holds
        # the cache resource name. Bind it to the LLM so this invocation
        # references cached input tokens (billed at ~25% rate) instead
        # of re-billing the full instrument_context.
        cache_name = state.get("gemini_cache_name", "")
        if cache_name:
            try:
                active_llm = llm.bind(cached_content=cache_name)
                active_structured_llm = bind_structured(
                    active_llm, ResearchPlan, "Research Manager (cached)",
                )
                _rm_log.info("rm-cache: using gemini cache %s", cache_name)
            except Exception as exc:
                _rm_log.warning(
                    "rm-cache: bind(cached_content) failed (%s) — falling back to non-cached",
                    exc,
                )
                active_structured_llm = structured_llm
        else:
            active_structured_llm = structured_llm
        instrument_context = build_instrument_context(state["company_of_interest"])
        history = state["investment_debate_state"].get("history", "")

        investment_debate_state = state["investment_debate_state"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"\n\n**Lessons from prior decisions and outcomes** "
            "(use these to calibrate confidence — past misses on this name "
            "or sector should make you more cautious about repeating the same "
            "thesis):\n"
            f"{past_context}\n"
            if past_context
            else ""
        )

        # Language instruction issued at BOTH the top and the bottom of
        # the prompt (2026-05-19 fix). The debate history block below is
        # long English (Bull/Bear quick_thinking_llm output); Gemini's
        # language-matching defaults to that surrounding context unless
        # the directive is loud + repeated. Top placement ensures the
        # LLM commits to the output language BEFORE reading the English
        # context. Bottom placement reinforces immediately before
        # generation. 茅台 600519.SS 2026-05-19 first-CN-run had the
        # research_manager emit full English ("Okay, let's break this
        # down...") + mixed phrases ("be 신중함 with sizing") because
        # only the bottom directive existed.
        language_directive = get_language_instruction()

        prompt = language_directive + f"""

As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis; recommend taking or growing the position
- **Overweight**: Constructive view; recommend gradually increasing exposure
- **Hold**: Balanced view; recommend maintaining the current position
- **Underweight**: Cautious view; recommend trimming exposure
- **Sell**: Strong conviction in the bear thesis; recommend exiting or avoiding the position

Commit to a clear stance whenever the debate's strongest arguments warrant one; reserve Hold for situations where the evidence on both sides is genuinely balanced.{lessons_line}

---

**EVALUATION HORIZON — read carefully:**

Your recommendation is evaluated over a **5-trading-day** window (the
memory log resolves entries by 5-day raw return vs SPY/sector alpha).
Calibrate the verdict accordingly:

- **Pure-valuation bear theses** (e.g. "PER 44배 is expensive", "PEG > 2 is rich")
  are TYPICALLY 6-12 month theses. Over 5 trading days, momentum,
  flows, and short-term catalysts dominate valuation. Do NOT downgrade
  a momentum leader to Sell/Underweight on a valuation argument ALONE
  unless there is also a concrete near-term catalyst (earnings within
  5 days, FOMC, guide cut, regulatory event, etc).

- **Wall Street consensus disagreement matters**: if the pre-fetched
  market-signals block shows a Buy/Strong Buy consensus with material
  upside to target (>10%), your Sell/Underweight verdict carries a
  higher burden of proof — explicitly name the near-term catalyst that
  Wall Street is missing.

- **Strong momentum + bull-aligned analysts**: if all four analysts
  lean Buy/Overweight AND the stock is up materially over 30D/90D,
  flipping to Sell/Underweight is rarely the 5-day-correct call.
  Prefer Hold or a smaller-step Underweight (trim, not exit) in that
  setup.

- **Hold is not a cop-out** when the directional signal is weak — a
  4-0 analyst lean toward Buy with a 5-day window of high volatility
  is usually best served by Hold, not by overriding the consensus.

- **Sell-side vs buy-side — keep them separate**: the "Wall Street
  consensus 목표가" is a sell-side ANALYST average (and often stale
  for weeks after a sharp rally; the pre-fetched block flags this
  with a ⚠️ when the gap is >=20%). It is NOT the same signal as
  the institutional/insider HOLDING percentage from the same block.
  A target-price gap describes analyst opinion; the holding %
  describes buy-side positioning. Do NOT cite a stale-target gap as
  evidence that "institutions think it's overpriced" — those are
  different actors with different incentives and different update
  cadences. If you reference the target gap, also reference the ⚠️
  staleness flag when it's present.

---

**Debate History:**
{history}""" + language_directive

        investment_plan = invoke_structured_or_freetext(
            active_structured_llm,
            llm,
            prompt,
            render_research_plan,
            "Research Manager",
        )

        new_investment_debate_state = {
            "judge_decision": investment_plan,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": investment_plan,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": investment_plan,
        }

    return research_manager_node
