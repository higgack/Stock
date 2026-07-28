"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    # Field order is deliberate: the free-text rationale is generated BEFORE
    # the recommendation enum so the model reasons its way to the verdict
    # rather than committing to a rating first and rationalising afterwards.
    # Structured-output providers (Gemini response_schema / OpenAI json_schema)
    # generate fields in schema-declaration order, so reasoning-before-enum
    # reduces the text↔enum divergence seen on ALAB 2026-05-26 (PM thesis
    # argued Hold but the enum came out Buy). strategic_actions depends on the
    # recommendation, so it follows the enum.
    # Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation that follows from the rationale "
            "above. Exactly one of Buy / Overweight / Hold / Underweight / "
            "Sell. Reserve Hold for situations where the evidence on both "
            "sides is genuinely balanced; otherwise commit to the side with "
            "the stronger arguments."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    # Reasoning-before-enum ordering (see ResearchPlan note): the case is
    # written first, then the action enum follows from it, then the numeric
    # levels that depend on the chosen action.
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    action: TraderAction = Field(
        description=(
            "The transaction direction that follows from the reasoning above. "
            "Exactly one of Buy / Hold / Sell."
        ),
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )
    kill_trigger: Optional[str] = Field(
        default=None,
        description=(
            "Thesis-breaking event/data (NOT a price level — stop_loss covers "
            "price). One short line naming the specific news, guidance, or "
            "macro datum that would invalidate the case and warrant immediate "
            "exit/reverse regardless of price. Examples: 'Q1 revenue miss vs "
            "guide', '美 對中 수출규제 발표', '50일 SMA 데드크로스 + 거래량 "
            "급증', '경쟁사 qual 통과 공식 발표'. Distinct from stop_loss "
            "(price-based) and Plan catalyst (positive trigger)."
        ),
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    if proposal.kill_trigger:
        parts.extend(["", f"**Kill Trigger**: {proposal.kill_trigger}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    # Field order is deliberate (ALAB 2026-05-26 fix): investment_thesis (the
    # evidence-anchored reasoning) is generated FIRST so the model reasons to
    # its verdict; the rating enum follows the thesis; the executive_summary
    # action plan and the numeric targets — all consequences of the rating —
    # come after. Structured-output providers generate fields in declaration
    # order, so this minimises the text↔enum divergence where the PM thesis
    # argued Hold but the rating enum emitted Buy (committed before reasoning).
    # render_pm_decision keeps the display order (Rating first) so parse_rating
    # and the report layout are unchanged.
    # Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. Write this BEFORE settling on the rating. If prior lessons "
            "are referenced in the prompt context, incorporate them; otherwise "
            "rely solely on the current analysis."
        ),
    )
    rating: PortfolioRating = Field(
        description=(
            "The final position rating that follows from the investment thesis "
            "above. Exactly one of Buy / Overweight / Hold / Underweight / "
            "Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: Optional[str] = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# ESON (Efficient Structured Object Notation) handoff serializers
# ---------------------------------------------------------------------------


def _eson_encode_cell(val: Any) -> str:
    """Encode a single cell value as bare string or JSON (ESON format)."""
    if val is None:
       return 'null'
    if isinstance(val, bool):
       return 'true' if val else 'false'
    if isinstance(val, (int, float)):
       return str(val)
    if isinstance(val, str):
       if not val or val[0] in ('"', '[', '{') or val[0].isspace() or val[-1].isspace():
           return json.dumps(val)
       if '\t' in val or '\r' in val or '\n' in val:
           return json.dumps(val)
       if val in ('null', 'true', 'false'):
           return json.dumps(val)
       try:
           float(val)
           return json.dumps(val)
       except ValueError:
           return val
    return json.dumps(val, separators=(',', ':'))


def research_plan_to_eson(plan: ResearchPlan, ticker: str) -> str:
    """Convert ResearchPlan to ESON for Research Manager → Trader handoff.

    ESON is lossless and cuts ~50% tokens vs JSON for agent-to-agent pipes.
    Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).
    """
    lines = ['!eson/1', f'ticker={_eson_encode_cell(ticker)}', 'plan{{recommendation,rationale,strategic_actions}}']
    row = [
       plan.recommendation.value,
       plan.rationale,
       plan.strategic_actions,
    ]
    lines.append('\t'.join(_eson_encode_cell(v) for v in row))
    return '\n'.join(lines) + '\n'


def trader_proposal_to_eson(proposal: TraderProposal, ticker: str) -> str:
    """Convert TraderProposal to ESON for Trader → Portfolio Manager handoff.

    Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).
    """
    lines = ['!eson/1', f'ticker={_eson_encode_cell(ticker)}', 'proposal{{action,reasoning,entry_price,stop_loss,position_sizing,kill_trigger}}']
    row = [
       proposal.action.value,
       proposal.reasoning,
       proposal.entry_price,
       proposal.stop_loss,
       proposal.position_sizing,
       proposal.kill_trigger,
    ]
    lines.append('\t'.join(_eson_encode_cell(v) for v in row))
    return '\n'.join(lines) + '\n'
