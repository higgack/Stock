"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools
import logging

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import build_instrument_context, get_language_instruction
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


_trader_log = logging.getLogger("tradingagents.trader")


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = build_instrument_context(company_name)

        # F1-MVP Gemini context caching (2026-05-19). Same shape as
        # research_manager — use cached_content when available.
        cache_name = state.get("gemini_cache_name", "")
        if cache_name:
            try:
                active_llm = llm.bind(cached_content=cache_name)
                active_structured_llm = bind_structured(
                    active_llm, TraderProposal, "Trader (cached)",
                )
                _trader_log.info("trader-cache: using gemini cache %s", cache_name)
            except Exception as exc:
                _trader_log.warning(
                    "trader-cache: bind(cached_content) failed (%s) — fallback",
                    exc,
                )
                active_structured_llm = structured_llm
        else:
            active_structured_llm = structured_llm
        investment_plan = state["investment_plan"]
        past_context = state.get("past_context", "")
        lessons_block = (
            "\n\nLessons from prior decisions on this name and recent cross-"
            "ticker outcomes (use these to weigh confidence — if the last "
            "BUY call already played out, factor that into whether to repeat "
            "or reverse the stance):\n"
            f"{past_context}"
            if past_context
            else ""
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan."
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}"
                    f"{lessons_block}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            active_structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
