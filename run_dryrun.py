"""
Gemini-powered dry-run of TradingAgents on a single US stock.

Usage:
    python run_dryrun.py                    # defaults: NVDA on a recent date
    python run_dryrun.py AAPL 2025-12-01    # custom ticker / date

Token-saving choices:
  - Both deep and quick LLMs use Gemini Flash (cheapest tier).
  - max_debate_rounds = 1 (minimum).
  - yfinance for all data (no extra API keys).
"""

import sys
from pathlib import Path

# Make the cloned TradingAgents package importable
sys.path.insert(0, str(Path(__file__).parent / "TradingAgents"))

from dotenv import load_dotenv
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

load_dotenv()

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "google"
config["deep_think_llm"] = "gemini-2.5-flash"        # heavy reasoning
config["quick_think_llm"] = "gemini-2.5-flash-lite"  # fast/cheap analyst calls
config["google_thinking_level"] = "minimal"          # cheapest thinking budget
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1
config["output_language"] = "Korean"
config["data_vendors"] = {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "yfinance",
    "news_data": "yfinance",
}

ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
date = sys.argv[2] if len(sys.argv) > 2 else "2025-12-01"

print(f"=== Dry-run: {ticker} on {date} ===")
ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate(ticker, date)
print("\n=== FINAL DECISION ===")
print(decision)
