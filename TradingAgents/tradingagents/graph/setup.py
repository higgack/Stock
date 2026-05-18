# TradingAgents/graph/setup.py

from typing import Any, Dict
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import *
from tradingagents.agents.utils.agent_states import AgentState

from .conditional_logic import ConditionalLogic


_ANALYST_REPORT_FIELD = {
    "market":       "market_report",
    "social":       "sentiment_report",
    "news":         "news_report",
    "fundamentals": "fundamentals_report",
}


def _make_pre_debate_guard(selected_analysts: list[str]):
    """Factory for the pre-debate guard node, closed over the analyst set
    that actually ran. ETF analyses skip the social analyst, so the guard
    must not flag sentiment_report's empty initial value as a failure.

    The conditional edge after this node decides proceed-to-debate vs
    short-circuit-to-END. When aborting, we also stub the debate / risk /
    decision state fields so trading_graph._log_state and the propagate()
    return path don't KeyError on the partial state — the bot's
    _check_reports_or_raise still detects the failed analyst reports and
    converts that into a Korean retry message before any of these empty
    placeholder values are user-visible.
    """
    keys_to_check = [
        _ANALYST_REPORT_FIELD[a] for a in selected_analysts
        if a in _ANALYST_REPORT_FIELD
    ]

    def guard(state):
        from tradingagents.agents.utils.agent_utils import looks_failed_report
        aborting = any(
            looks_failed_report(state.get(k, "") or "")
            for k in keys_to_check
        )
        if not aborting:
            return {}
        return {
            "investment_plan": "",
            "trader_investment_plan": "",
            "final_trade_decision": "",
        }

    return guard, keys_to_check


def _make_retry_bump(analyst_type: str):
    """Tiny node that increments an analyst's retry counter and clears its
    cached report.

    Routed to from "Msg Clear X" when ConditionalLogic.should_retry_X
    decides the report is unusable. Clearing the report is what lets the
    re-run actually overwrite the bad value rather than leaving it in
    state (the `Annotated[str, ...]` reducer is last-write-wins by default,
    so this is just defensive housekeeping). Messages have already been
    cleared by Msg Clear, so the analyst restarts with the standard
    "Continue" placeholder.
    """
    count_key = f"{analyst_type}_retry_count"
    if analyst_type == "social":
        report_key = "sentiment_report"
    else:
        report_key = f"{analyst_type}_report"

    def bump(state):
        return {
            count_key: (state.get(count_key, 0) or 0) + 1,
            report_key: "",
        }

    return bump


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: Dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        decision_thinking_llm: Any = None,
        decision_thinking_llm_light: Any = None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        # Optional dedicated tier for final-decision nodes (research manager,
        # trader, portfolio/risk manager). Falls back to deep when caller
        # didn't configure one — behavior identical to before this option
        # existed.
        self.decision_thinking_llm = decision_thinking_llm or deep_thinking_llm
        # Light variant of the decision LLM (Option 4 cost reduction).
        # Used by Portfolio Manager when the four analysts are unanimous,
        # where less synthesis is required. Falls back to the heavy
        # decision LLM when the caller didn't configure one.
        self.decision_thinking_llm_light = (
            decision_thinking_llm_light or self.decision_thinking_llm
        )
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        # Create analyst nodes
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        # Analysts use deep_thinking_llm because tool-calling reliability
        # matters more than speed here. Debate/risk nodes below stay on
        # quick_thinking_llm.
        if "market" in selected_analysts:
            analyst_nodes["market"] = create_market_analyst(
                self.deep_thinking_llm
            )
            delete_nodes["market"] = create_msg_delete()
            tool_nodes["market"] = self.tool_nodes["market"]

        if "social" in selected_analysts:
            analyst_nodes["social"] = create_social_media_analyst(
                self.deep_thinking_llm
            )
            delete_nodes["social"] = create_msg_delete()
            tool_nodes["social"] = self.tool_nodes["social"]

        if "news" in selected_analysts:
            analyst_nodes["news"] = create_news_analyst(
                self.deep_thinking_llm
            )
            delete_nodes["news"] = create_msg_delete()
            tool_nodes["news"] = self.tool_nodes["news"]

        if "fundamentals" in selected_analysts:
            analyst_nodes["fundamentals"] = create_fundamentals_analyst(
                self.deep_thinking_llm
            )
            delete_nodes["fundamentals"] = create_msg_delete()
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        # research_manager / trader / portfolio_manager are the three nodes
        # that produce or finalize the BUY/HOLD/SELL call. Routing them to
        # the decision tier (Pro by default) lifts the quality of the user-
        # facing recommendation without paying Pro prices for the analyst
        # and debate phases.
        research_manager_node = create_research_manager(self.decision_thinking_llm)
        trader_node = create_trader(self.decision_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(
            self.decision_thinking_llm,
            llm_light=self.decision_thinking_llm_light,
        )

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for analyst_type, node in analyst_nodes.items():
            workflow.add_node(f"{analyst_type.capitalize()} Analyst", node)
            workflow.add_node(
                f"Msg Clear {analyst_type.capitalize()}", delete_nodes[analyst_type]
            )
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])
            # One-shot retry bump per analyst. ConditionalLogic.should_retry_X
            # routes here from Msg Clear X when the report looks unusable;
            # this node bumps the counter (capping retries) and clears the
            # bad report so the next analyst pass can overwrite it cleanly.
            workflow.add_node(
                f"Retry {analyst_type.capitalize()}",
                _make_retry_bump(analyst_type),
            )

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)

        # Define edges
        # Start with the first analyst
        first_analyst = selected_analysts[0]
        workflow.add_edge(START, f"{first_analyst.capitalize()} Analyst")

        # Connect analysts in sequence
        for i, analyst_type in enumerate(selected_analysts):
            current_analyst = f"{analyst_type.capitalize()} Analyst"
            current_tools = f"tools_{analyst_type}"
            current_clear = f"Msg Clear {analyst_type.capitalize()}"

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # After Msg Clear, decide: retry this analyst once if its report
            # looks unusable, otherwise advance to the next analyst (or to
            # the pre-debate guard after the last analyst).
            if i < len(selected_analysts) - 1:
                advance_target = f"{selected_analysts[i+1].capitalize()} Analyst"
            else:
                advance_target = "Pre Debate Guard"
            current_retry = f"Retry {analyst_type.capitalize()}"
            workflow.add_conditional_edges(
                current_clear,
                getattr(self.conditional_logic, f"should_retry_{analyst_type}"),
                {
                    "retry": current_retry,
                    "advance": advance_target,
                },
            )
            # Retry bump runs the analyst again with a fresh message slate.
            workflow.add_edge(current_retry, current_analyst)

        # Pre-debate guard: short-circuit to END if any analyst report is
        # still unusable after its retry. Saves Bull/Bear/Trader/Risk/PM
        # cost and prevents the decision LLM from hallucinating around
        # the missing data. Both the guard node and its conditional edge
        # are closed over `selected_analysts` so analyses that drop the
        # social analyst (ETFs) don't trigger a false abort on the empty
        # initial sentiment_report.
        guard_node, guard_keys = _make_pre_debate_guard(selected_analysts)
        workflow.add_node("Pre Debate Guard", guard_node)
        workflow.add_conditional_edges(
            "Pre Debate Guard",
            self.conditional_logic.make_should_proceed_to_debate(guard_keys),
            {
                "proceed": "Bull Researcher",
                "abort": END,
            },
        )

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
