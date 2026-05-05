"""TradingAgents wrapper used by the Telegram bot.

Keeps the heavy initialization out of the bot file and exposes a single
synchronous `analyze(ticker, date)` call that returns (summary, full_report).
"""

import sys
from datetime import date as _date
from pathlib import Path

# Make the vendored TradingAgents package importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "TradingAgents"))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def _build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "google"
    config["deep_think_llm"] = "gemini-2.5-flash"
    config["quick_think_llm"] = "gemini-2.5-flash-lite"
    config["google_thinking_level"] = "minimal"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "Korean"
    config["data_vendors"] = {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
        "news_data": "yfinance",
    }
    return config


def analyze(ticker: str, target_date: str | None = None) -> tuple[str, str]:
    """Run TradingAgents on a single ticker.

    Returns (summary, full_report) — both Markdown strings.
    """
    target_date = target_date or _date.today().isoformat()
    ta = TradingAgentsGraph(debug=False, config=_build_config())
    state, decision = ta.propagate(ticker, target_date)

    full = _format_full(state, decision, ticker, target_date)
    summary = _format_summary(state, decision, ticker, target_date)
    return summary, full


def _format_summary(state: dict, decision: str, ticker: str, date_: str) -> str:
    rating = _extract_rating(decision) or "N/A"
    return (
        f"📊 <b>{ticker}</b> ({date_})\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 최종 판정: <b>{rating}</b>\n\n"
        f"{_first_lines(decision, max_lines=8)}"
    )


def _format_full(state: dict, decision: str, ticker: str, date_: str) -> str:
    parts = [f"📋 {ticker} 전체 리포트 ({date_})\n"]
    for key, label in [
        ("market_report", "📈 시장 분석"),
        ("sentiment_report", "💬 감정 분석"),
        ("news_report", "📰 뉴스 분석"),
        ("fundamentals_report", "💰 펀더멘털"),
        ("investment_plan", "🧭 투자 계획"),
        ("trader_investment_plan", "💼 트레이더 제안"),
    ]:
        body = state.get(key) if isinstance(state, dict) else None
        if body:
            parts.append(f"\n## {label}\n{body}")
    parts.append(f"\n## ✅ 최종 결정\n{decision}")
    return "\n".join(parts)


def _extract_rating(decision: str) -> str | None:
    upper = decision.upper()
    for kw in ("OVERWEIGHT", "UNDERWEIGHT", "BUY", "SELL", "HOLD"):
        if kw in upper:
            return kw.title()
    return None


def _first_lines(text: str, max_lines: int = 8) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    head = lines[:max_lines]
    return "\n".join(head) + ("\n…" if len(lines) > max_lines else "")
