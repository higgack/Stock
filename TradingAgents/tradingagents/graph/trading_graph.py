# TradingAgents/graph/trading_graph.py

import logging
import math
import os
from pathlib import Path
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional

import yfinance as yf

logger = logging.getLogger(__name__)

from langgraph.prebuilt import ToolNode

from tradingagents.llm_clients import create_llm_client

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news
)

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        # Per-tier output caps: deep models (analysts, managers) get a
        # larger budget for the long final reports; quick models (debate,
        # risk, trader) get a tighter cap to control cost.
        deep_kwargs = dict(llm_kwargs)
        if self.config.get("deep_max_output_tokens"):
            deep_kwargs["max_output_tokens"] = self.config["deep_max_output_tokens"]
        quick_kwargs = dict(llm_kwargs)
        if self.config.get("quick_max_output_tokens"):
            quick_kwargs["max_output_tokens"] = self.config["quick_max_output_tokens"]
        # Decision tier (research_manager / trader / portfolio_manager).
        # Optional — when not configured the deep tier is reused, so the
        # behavior is identical to before this option existed.
        decision_kwargs = dict(deep_kwargs)
        if self.config.get("decision_max_output_tokens"):
            decision_kwargs["max_output_tokens"] = self.config["decision_max_output_tokens"]

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **deep_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **quick_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()

        decision_model = self.config.get("decision_think_llm")
        if decision_model and decision_model != self.config["deep_think_llm"]:
            decision_client = create_llm_client(
                provider=self.config["llm_provider"],
                model=decision_model,
                base_url=self.config.get("backend_url"),
                **decision_kwargs,
            )
            self.decision_thinking_llm = decision_client.get_llm()
        else:
            # No dedicated decision tier — fall back to the deep tier.
            self.decision_thinking_llm = self.deep_thinking_llm

        # Light variant of the decision LLM for PM consensus paths
        # (Option 4 cost reduction, 2026-05-18). thinking_budget=2048
        # vs the default 4096 — when all four analysts agree on
        # direction, synthesis is mostly summarisation rather than
        # conflict resolution, so half the thinking budget suffices.
        # Conflict cases route to the full decision_thinking_llm
        # unchanged.  Only the Gemini 2.5 Pro path honours the
        # override; non-Google providers and Gemini 3 receive the
        # same LLM object (no quality / cost change).
        # Rule applies universally — US + KR + JP + TW (+ future CN).
        decision_light_kwargs = dict(decision_kwargs)
        decision_light_kwargs["thinking_budget_override"] = (
            self.config.get("pm_consensus_thinking_budget", 2048)
        )
        decision_light_model = (
            decision_model
            if decision_model and decision_model != self.config["deep_think_llm"]
            else self.config["deep_think_llm"]
        )
        try:
            decision_light_client = create_llm_client(
                provider=self.config["llm_provider"],
                model=decision_light_model,
                base_url=self.config.get("backend_url"),
                **decision_light_kwargs,
            )
            self.decision_thinking_llm_light = decision_light_client.get_llm()
        except Exception:
            # Provider that doesn't honour the override → reuse the
            # heavy LLM. PM logic falls back transparently.
            self.decision_thinking_llm_light = self.decision_thinking_llm


        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
            decision_thinking_llm=self.decision_thinking_llm,
            decision_thinking_llm_light=self.decision_thinking_llm_light,
        )

        self.propagator = Propagator()
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        # Per-request HTTP timeout, applied to every provider that supports
        # it. Without this a single hung Gemini call can burn the whole
        # 10-minute analysis budget. Lets the caller's retry kick in.
        if self.config.get("llm_request_timeout"):
            kwargs["timeout"] = self.config["llm_request_timeout"]

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5
    ) -> Tuple[Optional[float], Optional[float], Optional[int]]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        Alpha is computed against the ticker's matched SECTOR ETF when one
        is available (e.g. PLUG → TAN, NVDA → SOXX, JPM → XLF), with SPY
        as the fallback for tickers we can't classify. Comparing against
        the sector is a fairer scorecard: a +3% week on PLUG only beats
        the market alpha test if PLUG actually outperformed the solar
        sector — otherwise the bot is just riding sector beta.

        Returns (raw_return, alpha_return, actual_holding_days) or
        (None, None, None) if price data is unavailable (too recent, delisted,
        or network error).
        """
        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            # m8 (2026-05-29 audit): align with bot/auto_resolve.py's tightened
            # +3 gate (was +7 here). The two copies independently resolve the
            # same pending entry; a 7 vs 3 divergence let them settle it on
            # different days with different partial-window actual_days. +3 is
            # the minimum that absorbs a weekend transition (5 trading days
            # settle within holding_days + 3 calendar days); the per-fetch
            # ≥2-close readiness check below caps partial windows.
            end = start + timedelta(days=holding_days + 3)
            # If the holding window hasn't elapsed yet there are no returns to
            # score. Bailing here also stops yfinance from logging '$TICKER:
            # possibly delisted' for a forward range it can't possibly serve.
            if end > datetime.now():
                return None, None, None
            end_str = end.strftime("%Y-%m-%d")

            # Resolve benchmark — sector ETF preferred, SPY fallback.
            benchmark_symbol = "SPY"
            try:
                from tradingagents.agents.utils.sector_strength_tools import _resolve_benchmark
                bm = _resolve_benchmark(ticker)
                if bm and bm[0]:
                    benchmark_symbol = bm[0]
            except Exception:
                pass  # SPY default already set

            stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
            bench = yf.Ticker(benchmark_symbol).history(start=trade_date, end=end_str)

            # m9 (2026-05-29 audit): a thin sector ETF (<2 closes in the
            # window) would otherwise block an otherwise-resolvable entry
            # forever. Fall back to SPY (always liquid). raw return is a
            # unitless ratio so the alpha stays arithmetically valid — just
            # benchmarked against the broad US market instead of the sector.
            if len(bench) < 2 and benchmark_symbol != "SPY":
                logger.info(
                    "fetch_returns: %s benchmark %s thin (<2 closes) — SPY fallback",
                    ticker, benchmark_symbol,
                )
                benchmark_symbol = "SPY"
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
            alpha = raw - bench_ret
            if math.isnan(raw) or math.isnan(bench_ret):
                logger.warning(
                    "fetch_returns: %s/%s — NaN in Close (yfinance data gap)", ticker, trade_date
                )
                return None, None, None
            logger.info(
                "fetch_returns: %s/%s — raw=%.4f bench=%s ret=%.4f alpha=%.4f days=%d",
                ticker, trade_date, raw, benchmark_symbol, bench_ret, alpha, actual_days,
            )
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s (will retry next run): %s",
                ticker, trade_date, e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(ticker, entry["date"])
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
            )
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def propagate(self, company_name, trade_date):
        """Run the trading agents graph for a company on a specific date.

        When ``checkpoint_enabled`` is set in config, the graph is recompiled
        with a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.
        """
        self.ticker = company_name

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        self._resolve_pending_entries(company_name)

        # Recompile with a checkpointer if the user opted in.
        if self.config.get("checkpoint_enabled"):
            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], company_name
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            step = checkpoint_step(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )
            if step is not None:
                logger.info(
                    "Resuming from step %d for %s on %s", step, company_name, trade_date
                )
            else:
                logger.info("Starting fresh for %s on %s", company_name, trade_date)

        try:
            return self._run_graph(company_name, trade_date)
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def _run_graph(self, company_name, trade_date):
        """Execute the graph and write the resulting state to disk and memory log."""
        # F1 + F7 (2026-05-29 audit): drop per-run instrument caches so this
        # analysis re-fetches fresh intraday data, and so the context memo
        # starts empty (the up-to-8 build_instrument_context calls this run
        # then share one build per distinct shape instead of re-fanning out
        # ~20 prefetch tasks each time).
        try:
            from tradingagents.agents.utils.agent_utils import (
                clear_instrument_caches,
            )
            clear_instrument_caches()
        except Exception as exc:
            logger.warning("clear_instrument_caches failed: %s", exc)

        # Initialize state — inject memory log context for PM.
        past_context = self.memory_log.get_past_context(company_name)

        # F1-MVP Gemini context caching (2026-05-19). Create one
        # CachedContent containing this ticker's full instrument_context
        # at analysis start. Pass cache name through AgentState so
        # decision-tier nodes (RM/Trader/PM) can reference it when
        # invoking their Pro LLM. Cleanup in finally block.
        gemini_cache = None
        gemini_cache_name = ""
        try:
            from bot.gemini_cache_manager import maybe_create_cache
            from tradingagents.agents.utils.agent_utils import (
                build_instrument_context,
            )
            # Decision-tier nodes use the full (non-sliced, analyst_id=None)
            # instrument_context — same shape they consume during invocation.
            # Build once here so the cache contents exactly match what the
            # nodes will see.
            common_ctx = build_instrument_context(company_name)
            gemini_cache_name, gemini_cache = maybe_create_cache(
                company_name, common_ctx,
            )
        except Exception as exc:
            logger.warning("gemini cache setup failed for %s: %s", company_name, exc)

        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            past_context=past_context,
            gemini_cache_name=gemini_cache_name,
        )
        args = self.propagator.get_graph_args()

        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        if self.config.get("checkpoint_enabled"):
            tid = thread_id(company_name, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        try:
            if self.debug:
                trace = []
                for chunk in self.graph.stream(init_agent_state, **args):
                    if len(chunk["messages"]) == 0:
                        pass
                    else:
                        chunk["messages"][-1].pretty_print()
                        trace.append(chunk)
                final_state = trace[-1]
            else:
                final_state = self.graph.invoke(init_agent_state, **args)
        finally:
            # Best-effort cache cleanup. Gemini auto-expires at TTL even
            # if delete() never runs, so a failure here is harmless.
            if gemini_cache is not None:
                gemini_cache.delete()

        # Store current state for reflection.
        self.curr_state = final_state

        # Log state to disk.
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        self.memory_log.store_decision(
            ticker=company_name,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )

        # Clear checkpoint on successful completion to avoid stale state.
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )

        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
