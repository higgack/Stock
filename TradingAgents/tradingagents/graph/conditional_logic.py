# TradingAgents/graph/conditional_logic.py

import logging

from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.agent_utils import looks_failed_report

_log = logging.getLogger("tradingagents.retry")

# At most one retry per analyst — a persistently broken upstream (Gemini
# 503 storm, yfinance dead) shouldn't trap the graph in a loop, and each
# retry doubles that analyst's cost. The bot's analyzer.py raises if any
# report is still failed after the graph completes.
_MAX_ANALYST_RETRIES = 1


def _analysts_unanimous(state) -> bool:
    """Return True when every analyst that produced a readable stance
    leans the same direction (매수 / 보유 / 매도). Skipped analysts
    (empty reports) don't vote — same convention bot/analyzer.py uses
    for the stance bar. Requires at least 2 voting analysts to avoid
    treating a single-analyst state as 'unanimous'.

    Used by `should_continue_debate` to trim the Bear turn when there
    is no real disagreement to rebut. Hold-only consensus also counts:
    a 4-of-4 Hold is still unanimous direction, and Bear's job there
    is to rebut a bull case that wasn't made.
    """
    try:
        from bot.analyzer import _extract_stance
        from tradingagents.agents.managers.portfolio_manager import (
            _stance_to_direction,
        )
    except Exception:
        return False

    report_keys = (
        "market_report", "sentiment_report",
        "news_report", "fundamentals_report",
    )
    directions: list[str] = []
    for key in report_keys:
        body = state.get(key)
        if not body or not isinstance(body, str):
            continue
        stance = _extract_stance(body)
        if not stance:
            continue
        directions.append(_stance_to_direction(stance))

    if len(directions) < 2:
        return False
    return len(set(directions)) == 1


def _should_retry(state, analyst: str, report_key: str) -> str:
    """Shared body for should_retry_market/social/news/fundamentals.

    Returns "retry" if the analyst's report on `state` still looks unusable
    AND we haven't hit the per-analyst retry cap yet; "advance" otherwise.
    """
    report = state.get(report_key, "") or ""
    count = state.get(f"{analyst}_retry_count", 0) or 0
    if looks_failed_report(report) and count < _MAX_ANALYST_RETRIES:
        _log.warning(
            "analyst %s produced unusable report (%d chars), retrying (%d/%d)",
            analyst, len(report), count + 1, _MAX_ANALYST_RETRIES,
        )
        return "retry"
    return "advance"


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    def should_retry_market(self, state: AgentState) -> str:
        return _should_retry(state, "market", "market_report")

    def should_retry_social(self, state: AgentState) -> str:
        return _should_retry(state, "social", "sentiment_report")

    def should_retry_news(self, state: AgentState) -> str:
        return _should_retry(state, "news", "news_report")

    def should_retry_fundamentals(self, state: AgentState) -> str:
        return _should_retry(state, "fundamentals", "fundamentals_report")

    def make_should_proceed_to_debate(self, report_keys: list[str]):
        """Build a should_proceed_to_debate conditional closed over the
        analyst report keys that should be checked (skipping social for
        ETFs etc). The guard returns "proceed" only if every selected
        report passes looks_failed_report.

        Bull/Bear → Research Manager → Trader → Risk debate → Portfolio
        Manager is roughly 60–70% of an analysis's LLM cost. If any
        analyst's final report still looks unusable, the decision LLMs
        will hallucinate around the gap and produce an authoritative-
        looking BUY/SELL on a hollow foundation. Better to short-circuit
        to END now: bot.analyzer's _check_reports_or_raise will then
        surface a clear retry message instead of a fabricated decision.
        """
        def should_proceed(state: AgentState) -> str:
            for key in report_keys:
                if looks_failed_report(state.get(key, "") or ""):
                    _log.warning(
                        "pre-debate guard: %s still failed after retry — "
                        "short-circuiting to END (saves debate/decision cost)",
                        key,
                    )
                    return "abort"
            return "proceed"
        return should_proceed

    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_market"
        return "Msg Clear Market"

    def should_continue_social(self, state: AgentState):
        """Determine if social media analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_social"
        return "Msg Clear Social"

    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_news"
        return "Msg Clear News"

    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls:
            return "tools_fundamentals"
        return "Msg Clear Fundamentals"

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue.

        Standard flow: Bull → Bear → Research Manager (count reaches
        2 * max_debate_rounds, default 2).

        Cost-reduction shortcut (Option 3, 2026-05-18): when the four
        analyst reports show unanimous direction (all 매수 / 보유 / 매도
        — counting only analysts that actually ran), skip the Bear
        rebuttal after the first Bull turn and proceed directly to the
        Research Manager. Bear's contribution when there's nothing to
        rebut is mostly boilerplate restating the bull case in reverse;
        cutting it saves ~1 LLM call per unanimous run with negligible
        quality impact. Conflict cases (3-1 split, 2-2 split, abstains
        + disagreement) still get the full Bull/Bear pass — this only
        trims the unambiguous consensus path.

        Rule applies to all analyses going forward; covers US + KR +
        JP + TW (+ future CN) equally. No market-specific branching.
        """
        count = state["investment_debate_state"]["count"]
        threshold = 2 * self.max_debate_rounds

        # Shortcut: after first Bull turn, if analysts are unanimous,
        # skip Bear and go straight to Research Manager.
        if count >= 1 and _analysts_unanimous(state):
            return "Research Manager"

        if count >= threshold:  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Portfolio Manager"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"
