"""Shared fixtures + sys.modules patching for bot/ harness regression tests.

Module-level patching must happen BEFORE any test file imports a bot.*
module, so it lives here rather than in individual test files. Order:
  1. sys.path: add repo root so `import bot.*` resolves.
  2. Heavy dep mocking: tradingagents / langchain_core are not installed in
     the system python used by pytest — mock them out so bot.analyzer (and
     other modules that do lazy `from tradingagents...` imports) can be
     imported without an API key or the full langchain stack.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

# ── 1. sys.path ────────────────────────────────────────────────────────────
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# Also add TradingAgents sub-package root so `import tradingagents` works
# even when the real package is present (used by test_analyzer_pure.py).
_TA_ROOT = REPO_ROOT / "TradingAgents"
if str(_TA_ROOT) not in sys.path:
    sys.path.insert(0, str(_TA_ROOT))

# ── 2. Heavy dep mocking ───────────────────────────────────────────────────
# Mock everything that isn't available in the lightweight test environment.
# Bot modules that do `from tradingagents... import X` get a MagicMock
# attribute back, which is sufficient for pure-function testing.
_HEAVY_MOCKS = [
    # LangChain / LangGraph (not installed in test env)
    "langchain_core",
    "langchain_core.callbacks",
    "langchain_core.callbacks.base",
    "langchain_core.messages",
    "langchain_core.prompts",
    "langgraph",
    "langgraph.graph",
    # yfinance (network dep — mock to keep tests offline)
    "yfinance",
    # tradingagents heavy internal modules
    "tradingagents.graph.trading_graph",
    "tradingagents.default_config",
    "tradingagents.agents.utils.agent_utils",
    "tradingagents.agents.utils.sector_strength_tools",
    "tradingagents.dataflows.config",
    # bot internal modules with heavy deps
    "bot.cache",
    "bot.usage_tracker",
]

for _mod in _HEAVY_MOCKS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# DEFAULT_CONFIG needs to be a real dict (bot.analyzer does .copy() on it)
try:
    sys.modules["tradingagents.default_config"].DEFAULT_CONFIG = {}
except Exception:
    pass
