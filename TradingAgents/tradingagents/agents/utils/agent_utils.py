import logging

from langchain_core.messages import HumanMessage, RemoveMessage

_analyst_log = logging.getLogger("tradingagents.analyst")


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


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )

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


        
