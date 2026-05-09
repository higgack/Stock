import logging
import re

from langchain_core.messages import HumanMessage, RemoveMessage

_analyst_log = logging.getLogger("tradingagents.analyst")


# Markers that strongly suggest the analyst output is broken (raw error blob,
# unprocessed tool_code, etc) rather than a real Korean-language report. Kept
# here, near the analyst infrastructure, so the graph-level retry conditional
# uses the same definition the bot's downstream rendering has settled on.
_FAILURE_MARKERS = (
    "tool_code",
    '"error"',
    "Traceback",
    "Internal Server Error",
    "InvalidArgument",
)

# Apology-mode openings: the analyst gave up on a tool call and started
# narrating the failure as if it were the report. Real reports never start
# this way. Kept narrow — only phrases that clearly signal "I'm reporting
# my own tool failure" rather than incidental analytical use of the words.
_APOLOGY_MARKERS = (
    "죄송합니다",
    "도구를 사용할 수 없습니다",
    "도구가 유효하지 않다",
    "도구 호출에 실패",
    "도구 오류로 인해",
    "도구를 호출할 수 없",
    "가져오는 데 오류가 발생",
    "데이터를 가져오지 못했",
    "데이터를 가져올 수 없",
)

# Raw CSV / TSV bleed-through: when the fundamentals analyst pastes the
# yfinance financial-statement dump verbatim instead of summarising. The
# pattern matches lines like:
#   Total Revenue,33172000000.0,31797000000.0,29771000000.0,27518000000.0
# A handful of legitimate inline tables won't match (Markdown uses pipes),
# so 3+ such lines is a strong signal the analyst skipped its job.
_RAW_CSV_LINE_RE = re.compile(
    r"(?m)^[A-Za-z][A-Za-z0-9 _]+,-?\d+(?:\.\d+)?(?:,-?\d+(?:\.\d+)?){2,}\s*$"
)


def looks_failed_report(text: str) -> bool:
    """Return True if an analyst's final report looks unusable.

    Mirrors bot.analyzer._clean_section's failure heuristics so the graph
    retries on the same conditions the bot would otherwise mask with a
    placeholder. Anything passing this check is a candidate for a single
    in-graph retry; on second failure the bot raises.
    """
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if len(stripped) > 200_000:
        return True
    head = stripped[:500]
    if any(m in head for m in _FAILURE_MARKERS):
        return True
    # Apology-mode: the analyst opened with "죄송합니다 + 도구 오류" instead
    # of a real report. Even when followed by paragraphs of hallucinated
    # text, the leading apology means the LLM was filling a gap rather than
    # analyzing real data — retry once with a clean message slate.
    if any(m in head for m in _APOLOGY_MARKERS):
        return True
    # Raw financial-CSV bleed (≥3 numeric data rows): fundamentals analyst
    # pasted the tool's CSV instead of summarising it. Real reports use
    # Markdown tables (pipe-delimited), not comma-delimited rows.
    if len(_RAW_CSV_LINE_RE.findall(stripped)) >= 3:
        return True
    if len(stripped) < 200:
        return True
    text_chars = sum(1 for c in stripped if c.isalnum())
    if text_chars < 80:
        return True
    return False



def _content_to_str(result) -> str:
    """Normalize an LLM message's content into a single string. Some Gemini
    responses come back as a list of content parts; collapse those to plain
    text so callers can treat content as a string uniformly."""
    content = result.content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            else:
                text = getattr(part, "text", None)
                if text:
                    pieces.append(text)
        return "".join(pieces)
    return content or ""


def finalize_analyst_result(prompt, llm, messages, result, analyst_name: str):
    """Extract a non-empty report string from an analyst's chain invocation.

    Returns (message_to_record, report_text). If the analyst stopped tool-
    calling but emitted empty content (Gemini sometimes hits MAX_TOKENS or
    just returns nothing after a heavy tool result), re-invoke the LLM once
    WITHOUT tools and explicitly demand a written report from the message
    history we already have. This recovers the common failure mode where a
    section ends up as "_(이번 분석은 모델 응답 오류로 미완성. 다른 티커로
    재시도해보세요.)_" downstream.

    On total failure (retry also empty), returns the original result and an
    empty string — the caller's downstream placeholder logic still handles
    that gracefully.
    """
    if result.tool_calls:
        # Still in the tool-calling phase; the graph will route us back here.
        return result, ""
    text = _content_to_str(result)
    if text.strip():
        return result, text
    _analyst_log.warning(
        "%s analyst finished with empty content and no tool calls — retrying without tools",
        analyst_name,
    )
    finalize_msg = HumanMessage(
        content=(
            "지금까지 호출한 도구 결과를 바탕으로 추가 도구 호출 없이 한국어로 분석 보고서를"
            " 즉시 작성해라. 도구 호출이나 빈 응답은 허용되지 않는다. 데이터가 부족하면"
            " 부족한 점을 명시하고 가능한 범위에서 결론을 내려라."
        )
    )
    try:
        retry = (prompt | llm).invoke(messages + [finalize_msg])
    except Exception as exc:
        _analyst_log.warning("%s analyst retry raised: %s", analyst_name, exc)
        return result, ""
    text = _content_to_str(retry)
    if text.strip():
        _analyst_log.info(
            "%s analyst retry recovered %d chars", analyst_name, len(text)
        )
        return retry, text
    _analyst_log.warning("%s analyst retry also returned empty content", analyst_name)
    return result, ""


# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)
from tradingagents.agents.utils.risk_metrics_tools import get_risk_metrics
from tradingagents.agents.utils.macro_context_tools import get_macro_context
from tradingagents.agents.utils.sector_strength_tools import get_sector_relative_strength


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return (
        f" CRITICAL LANGUAGE REQUIREMENT: Write your ENTIRE response in {lang}."
        f" Every section header, paragraph, bullet, and table cell MUST be in {lang}."
        f" Do not include English prose anywhere in the response, even in the conclusion."
        f" Technical symbols (ticker codes, indicator names like RSI, MACD) may stay as-is,"
        f" but every explanatory sentence must be in {lang}."
    )


def get_analyst_directive() -> str:
    """Force analysts to call tools immediately instead of asking the user for parameters."""
    return (
        " EXECUTION RULES (MANDATORY):"
        " 1) Begin by calling the appropriate tools immediately to gather raw data."
        " 2) Do NOT ask the user for clarification, parameters, date ranges, or indicator selections —"
        " use sensible defaults: the most recent ~28 days for date ranges, and all standard indicators"
        " when relevant. The current date is provided in this prompt; use it as the end date and"
        " subtract 28 days for the start date."
        " 3) Your final message must be a complete written report based on the tool results."
        " Never respond with a question or a request for input — that is a failure."
        " 4) Keep tool calls focused: fetch only what you need to write the report. Do not call the"
        " same tool repeatedly with different parameters trying to gather everything available —"
        " one or two well-chosen calls is enough."
    )


# yfinance.Ticker(t).info is a network call; cache the lookups in-process
# so a repeated /TICKER inside the same analysis (e.g. analyst rerun) or
# back-to-back /compare doesn't pay the round-trip again. None entries
# mean "we tried and it failed"; we don't retry within a process lifetime.
_QUOTE_TYPE_CACHE: dict[str, str | None] = {}


def _quote_type(ticker: str) -> str | None:
    """Best-effort yfinance lookup of an instrument's quoteType. Returns
    e.g. "EQUITY", "ETF", "MUTUALFUND", "INDEX", "CURRENCY", or None when
    yfinance won't say. Cached per-process."""
    if ticker in _QUOTE_TYPE_CACHE:
        return _QUOTE_TYPE_CACHE[ticker]
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        qt = info.get("quoteType") or info.get("typeDisp") or None
        if isinstance(qt, str):
            qt = qt.upper()
    except Exception as exc:
        _analyst_log.warning("quoteType lookup failed for %s: %s", ticker, exc)
        qt = None
    _QUOTE_TYPE_CACHE[ticker] = qt
    return qt


def is_etf(ticker: str) -> bool:
    """True if yfinance reports this ticker as an ETF (or close cousin
    like a 3X leveraged fund). Conservative: when yfinance is unsure,
    we assume EQUITY so the standard analyst path runs."""
    qt = _quote_type(ticker) or ""
    return qt in ("ETF", "ETN", "MUTUALFUND")


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified
    tickers and adjust their data expectations for non-equity products."""
    base = (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )
    qt = _quote_type(ticker)
    if qt in ("ETF", "ETN", "MUTUALFUND"):
        # ETFs / leveraged funds have no company news, no executive
        # quotes, no earnings transcripts. The analyst's standard "company
        # news" and "social sentiment" paths return empty for these,
        # which previously bled into the report as "데이터 없음" placeholders.
        # Tell the model up front: this is a fund, analyse the structure
        # and the underlying instead of inventing company-style narrative.
        base += (
            f"\n\nIMPORTANT: `{ticker}` is a {qt} (fund product), not a single"
            " company. Do NOT search for company-specific news, executive"
            " statements, earnings, or insider transactions. Instead analyse:"
            " (1) the fund's structure (leverage, expense ratio, tracking"
            " target), (2) the macro / sector / country exposure of the"
            " underlying, (3) recent flow data and price action, and"
            " (4) leveraged-fund-specific risks like daily-reset volatility"
            " decay where applicable. If a tool returns no company news or"
            " social sentiment, that is expected — note it briefly and move"
            " on. Do NOT apologise or call the tool 'broken'."
        )
    return base

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
