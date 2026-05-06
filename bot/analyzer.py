"""TradingAgents wrapper used by the Telegram bot.

Keeps the heavy initialization out of the bot file and exposes a single
synchronous `analyze(ticker, date)` call that returns (summary, full_report).
"""

import logging
import re
import sys
from datetime import date as _date
from pathlib import Path

# Make the vendored TradingAgents package importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "TradingAgents"))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from bot import cache as _cache

log = logging.getLogger("stock-bot.analyzer")

# Skip the social-media analyst — it overlaps ~80% with the news analyst on
# Gemini and accounts for ~15-20% of total Gemini spend per analysis.
_SELECTED_ANALYSTS = ["market", "news", "fundamentals"]


def _build_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "google"
    # Hybrid model strategy:
    # - deep_think_llm (flash) handles tool-using analysts and final managers
    # - quick_think_llm (flash-lite) handles the debate/risk nodes that don't
    #   need tools, trading ~25% of total cost for slightly less polish on
    #   intermediate text. graph/setup.py routes analysts to deep_think_llm.
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

    Returns (summary, full_report) — both Markdown strings. Same
    (ticker, target_date) pair is served from disk cache to avoid paying
    for Gemini twice on the same trading day.
    """
    target_date = target_date or _date.today().isoformat()
    ticker = ticker.upper()

    cached = _cache.get(ticker, target_date)
    if cached is not None:
        log.info("cache hit for %s/%s", ticker, target_date)
        return cached

    ta = TradingAgentsGraph(
        debug=False,
        config=_build_config(),
        selected_analysts=_SELECTED_ANALYSTS,
    )
    state, decision = ta.propagate(ticker, target_date)

    full = _format_full(state, decision, ticker, target_date)
    summary = _format_summary(state, decision, ticker, target_date)
    _cache.put(ticker, target_date, summary, full)
    return summary, full


def _format_summary(state: dict, decision: str, ticker: str, date_: str) -> str:
    rating = _extract_rating(decision) or "N/A"
    return (
        f"📊 **{ticker}** ({date_})\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 최종 판정: **{rating}**\n\n"
        f"{_first_lines(decision, max_lines=8)}"
    )


_REPORT_SECTIONS = [
    ("market_report", "📈 시장 분석", "market"),
    ("sentiment_report", "💬 감정 분석", "social"),
    ("news_report", "📰 뉴스 분석", "news"),
    ("fundamentals_report", "💰 펀더멘털", "fundamentals"),
    ("investment_plan", "🧭 투자 계획", None),
    ("trader_investment_plan", "💼 트레이더 제안", None),
]


def _format_full(state: dict, decision: str, ticker: str, date_: str) -> str:
    parts = [f"📋 {ticker} 전체 리포트 ({date_})\n"]
    for key, label, analyst_id in _REPORT_SECTIONS:
        if analyst_id is not None and analyst_id not in _SELECTED_ANALYSTS:
            continue  # we never ran this analyst — skip the section entirely
        body = state.get(key) if isinstance(state, dict) else None
        parts.append(f"\n## {label}\n{_clean_section(body)}")
    parts.append(f"\n## ✅ 최종 결정\n{decision}")
    return "\n".join(parts)


_GARBAGE_MARKERS = (
    '{"error"',
    '"error":',
    "tool_code",
    "default_api.",
    "print(default_api",
)
_FAILURE_PLACEHOLDER = "_(이번 분석은 모델 응답 오류로 미완성. 다른 티커로 재시도해보세요.)_"

# Patterns the agents sometimes emit that read as noise to a human reader.
_FINAL_PROPOSAL_RE = re.compile(
    r"^[^\n]*FINAL TRANSACTION PROPOSAL[^\n]*$",
    re.MULTILINE | re.IGNORECASE,
)
# Raw tool-result fragments: news payload pieces that the agent should have
# summarized but instead pasted verbatim. The dumps contain literal '\n'
# escape sequences (two characters: a backslash followed by 'n'), not real
# newlines, so the patterns match those escapes too.
_LINK_LINE_RE = re.compile(r"\\?nLink:\s*[^\s\\]+", re.IGNORECASE)
_TOOL_HEADER_RE = re.compile(
    r"###\s+[^()\n\\]{1,300}\(source:\s*[^)\n\\]+\)",
    re.IGNORECASE,
)
_ESCAPED_NEWLINES_RE = re.compile(r"(?:\\n){2,}")

# Comma-separated big numbers (≥ 1,000,000) and bare 9+-digit integers,
# optionally followed by 달러/원. The lookarounds also exclude word chars,
# '.', '/', and '-' so digit runs inside URLs (e.g. 'articles/.../12345678.html')
# don't get rewritten into '약 12.3백만'.
_LARGE_NUM_RE = re.compile(
    r"(?<![\w./\-])(\d{1,3}(?:,\d{3}){2,}|\d{9,})(\s*(?:달러|원))?(?![\w./\-])"
)
# Decimals with 5+ digits after the dot — collapse to 4 places for readability.
_LONG_DECIMAL_RE = re.compile(r"(?<![\d.])(\d+\.\d{5,})(?![\d.])")
# Runaway repetition Gemini sometimes emits ('--------…' x thousands of chars).
_RUNAWAY_CHAR_RE = re.compile(r"([\-=_*#~])\1{29,}")


def _abbrev_korean(num: int) -> str:
    if num >= 10**12:
        return f"약 {num / 10**12:.2f}조"
    if num >= 10**8:
        return f"약 {num / 10**8:.0f}억"
    if num >= 10**6:
        return f"약 {num / 10**6:.1f}백만"
    return ""


def _abbrev_match(m: re.Match) -> str:
    raw = m.group(0)
    num_str = m.group(1).replace(",", "")
    unit = (m.group(2) or "").strip()
    try:
        num = int(num_str)
    except ValueError:
        return raw
    abbreviated = _abbrev_korean(num)
    if not abbreviated:
        return raw
    return f"{abbreviated} {unit}".rstrip() if unit else abbreviated


def _round_long_decimal(m: re.Match) -> str:
    try:
        return f"{float(m.group(1)):.4f}"
    except ValueError:
        return m.group(0)


def _polish(body: str) -> str:
    """Strip noise patterns that agents occasionally leak into their text.

    Removes per-section 'FINAL TRANSACTION PROPOSAL' lines (the rating is
    already extracted into the summary, so showing them again — sometimes
    contradicting each other — just confuses the reader), raw tool-result
    fragments like literal '\\nLink: https://...' and '### Title (source: X)'
    headers, and collapses leftover literal '\\n\\n' escape sequences and
    runs of blank lines.
    """
    body = _FINAL_PROPOSAL_RE.sub("", body)
    body = _LINK_LINE_RE.sub("", body)
    body = _TOOL_HEADER_RE.sub("", body)
    body = _ESCAPED_NEWLINES_RE.sub("\n\n", body)
    # Any remaining stray literal '\n' becomes a real newline.
    body = body.replace("\\n", "\n")
    body = _RUNAWAY_CHAR_RE.sub("", body)
    body = _LARGE_NUM_RE.sub(_abbrev_match, body)
    body = _LONG_DECIMAL_RE.sub(_round_long_decimal, body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def _clean_section(body) -> str:
    """Replace empty or obviously-broken agent output with a clear placeholder.

    Gemini occasionally emits a JSON error blob, raw tool_code, or no content
    at all instead of a real report. Surface that as an explicit failure marker
    so the reader knows the section is missing rather than silently dropping
    it (which used to leave the report header followed by the next section).
    Otherwise pass the body through `_polish` to remove leaked noise.
    """
    if not body or not body.strip():
        return _FAILURE_PLACEHOLDER
    head = body.strip()[:500]
    if any(m in head for m in _GARBAGE_MARKERS):
        return _FAILURE_PLACEHOLDER
    return _polish(body)


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
