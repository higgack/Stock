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

# Apology-mode openings (Korean): the analyst gave up on a tool call and
# started narrating the failure as if it were the report. Real reports
# never start this way. Kept narrow — only phrases that clearly signal
# "I'm reporting my own tool failure" rather than incidental analytical
# use of the words. Scanned in head[:500] only; legitimate reports may
# mention "데이터 누락" deeper in the body when discussing a specific
# obscure metric.
_APOLOGY_MARKERS = (
    "죄송합니다",
    "도구 호출에 실패",
    "도구 오류로 인해",
    "가져오는 데 오류가 발생",
    "데이터를 가져오지 못했",
    "데이터를 가져올 수 없",
)

# English equivalents — Gemini sometimes drops into English mid-Korean
# report when refusing tool calls (GOOGL on 2026-05-10 was the canonical
# case: "The `get_macro_context` tool is not available...").
_APOLOGY_MARKERS_EN = (
    "I cannot provide",
    "I do not have access to",
    "I am unable to access",
    "I am unable to retrieve",
    "Therefore, I cannot",
)

# Tool-failure phrases that are SO specific they're never legitimate
# analytical use. Scanned across the WHOLE body — Gemini sometimes
# buries the apology mid-section (news analyst on 2026-05-10 had it
# inside the "거시 경제 현황" subsection ~1500 chars in). Anything
# matching here is treated as a failed report.
_TOOL_FAILURE_ANYWHERE = (
    "도구를 사용할 수 없",
    "도구가 유효하지 않",
    "tools are not available",
    "tool is not available",
    "tools are unavailable",
    "tool is unavailable",
    # PLUG 2026-05-10 news analyst variant: when get_macro_context
    # returned a partial-failure message the LLM rephrased it as
    # "(macro context API 호출 실패로 인해 데이터 없음)" — a different
    # surface form from the older "도구를 사용할 수 없" that still means
    # the same thing. Caught body-wide so the buried subsection match
    # works.
    "API 호출 실패",
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
    if any(m in head for m in _APOLOGY_MARKERS_EN):
        return True
    # Specific tool-unavailability phrases anywhere in the body. These are
    # never legitimate analytical content — every occurrence is the LLM
    # refusing to call a wired-up tool and inventing an excuse. Scanned
    # body-wide so a buried "거시 경제 현황: 죄송합니다. 도구를 사용할
    # 수 없어..." subsection still triggers a retry.
    if any(m in stripped for m in _TOOL_FAILURE_ANYWHERE):
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
_INSTRUMENT_INFO_CACHE: dict[str, dict] = {}


def _instrument_info(ticker: str) -> dict:
    """Cached yfinance .info lookup. Returns a dict with the fields we
    actually use (quoteType, sector, industry, longName, Wall Street
    consensus, short interest, insider holdings, earnings timestamps),
    or empty dict when the lookup fails. Single network call per ticker
    per process; downstream callers (is_etf, _quote_type, sector/industry
    injection, market-signals injection, earnings warning) all share
    the same fetch."""
    if ticker in _INSTRUMENT_INFO_CACHE:
        return _INSTRUMENT_INFO_CACHE[ticker]
    out: dict = {}
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).info or {}
        # Strings — keep only when non-empty
        for key in ("quoteType", "typeDisp", "sector", "industry", "longName",
                    "recommendationKey"):
            v = raw.get(key)
            if isinstance(v, str) and v.strip():
                out[key] = v.strip()
        # Numerics — keep when present (zero is a meaningful value)
        for key in ("targetMeanPrice", "targetHighPrice", "targetLowPrice",
                    "recommendationMean", "numberOfAnalystOpinions",
                    "currentPrice", "regularMarketPrice",
                    "sharesShort", "shortRatio", "shortPercentOfFloat",
                    "heldPercentInsiders", "heldPercentInstitutions",
                    "earningsTimestamp", "earningsTimestampStart",
                    "earningsTimestampEnd"):
            v = raw.get(key)
            if isinstance(v, (int, float)) and not (isinstance(v, float) and v != v):
                # exclude NaN
                out[key] = v
    except Exception as exc:
        _analyst_log.warning("instrument info lookup failed for %s: %s", ticker, exc)
    _INSTRUMENT_INFO_CACHE[ticker] = out
    return out


def _quote_type(ticker: str) -> str | None:
    info = _instrument_info(ticker)
    qt = info.get("quoteType") or info.get("typeDisp")
    return qt.upper() if isinstance(qt, str) else None


def is_etf(ticker: str) -> bool:
    """True if yfinance reports this ticker as an ETF (or close cousin
    like a 3X leveraged fund). Conservative: when yfinance is unsure,
    we assume EQUITY so the standard analyst path runs."""
    qt = _quote_type(ticker) or ""
    return qt in ("ETF", "ETN", "MUTUALFUND")


_NEWS_AVAILABILITY_CACHE: dict[str, bool] = {}


def has_recent_news(ticker: str) -> bool:
    """Return True iff yfinance currently surfaces ANY news article for
    the ticker. Used as a pre-flight check so the bot can skip the news
    analyst entirely on coverage-poor names (newly-IPO'd, OTC, foreign
    secondary) instead of paying for an analyst that will produce a
    placeholder and then trip the fail-fast guard.

    Conservative on error: a yfinance hiccup returns True so we don't
    spuriously drop the news analyst on a transient network blip — the
    in-graph retry will catch a real failure downstream anyway.
    """
    if ticker in _NEWS_AVAILABILITY_CACHE:
        return _NEWS_AVAILABILITY_CACHE[ticker]
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
        has = len(items) > 0
    except Exception as exc:
        _analyst_log.warning("news availability check failed for %s: %s", ticker, exc)
        has = True  # fail open
    _NEWS_AVAILABILITY_CACHE[ticker] = has
    return has


# Macro snapshot cache. yfinance fetches for ^TNX/^VIX/DXY/etc are slow
# and analyst LLMs were observed (2026-05 across PLUG/AMAT/GOOGL/JPM/TSM)
# to skip the get_macro_context tool call entirely despite the prompt
# marking it as MANDATORY. The fix: have the analyst NODES (Python, not
# LLM) call this helper at entry, inject the result into the prompt as
# already-fetched fact, and drop get_macro_context from the analyst's
# tool list so the LLM can't decide to skip it. Two analysts (market,
# news) share one fetch per (curr_date) per process.
_MACRO_CACHE: dict[str, str] = {}


def get_macro_for(curr_date: str) -> str:
    """Return cached macro snapshot for `curr_date`, fetching once if
    needed. Empty string on failure — caller decides how to label the
    section so the analyst prompt can include a graceful fallback line
    without the LLM apologising."""
    if curr_date in _MACRO_CACHE:
        return _MACRO_CACHE[curr_date]
    try:
        # Late import: macro_context_tools imports yfinance which is
        # heavy enough to keep out of the agent_utils module load path
        # for callers that never need the snapshot.
        from tradingagents.agents.utils.macro_context_tools import get_macro_context
        snapshot = get_macro_context.invoke({"curr_date": curr_date}) or ""
    except Exception as exc:
        _analyst_log.warning(
            "macro pre-fetch for %s failed: %s — analyst will run without snapshot",
            curr_date, exc,
        )
        snapshot = ""
    _MACRO_CACHE[curr_date] = snapshot
    return snapshot


# Same pattern as the macro cache: the market analyst was observed to
# skip get_risk_metrics and get_sector_relative_strength even though
# both were marked MANDATORY in the prompt. Pre-fetch in Python at node
# entry and remove the tools from the analyst's tool list — that's the
# only reliable way to guarantee the data ends up in the report.
_RISK_METRICS_CACHE: dict[tuple[str, str], str] = {}
_SECTOR_STRENGTH_CACHE: dict[tuple[str, str], str] = {}


def get_risk_metrics_for(symbol: str, curr_date: str) -> str:
    """Cached risk-metrics snapshot for (symbol, curr_date). Empty string
    on failure; caller writes a single fallback sentence so the analyst
    doesn't slip into apology mode."""
    key = (symbol, curr_date)
    if key in _RISK_METRICS_CACHE:
        return _RISK_METRICS_CACHE[key]
    try:
        from tradingagents.agents.utils.risk_metrics_tools import get_risk_metrics
        snapshot = get_risk_metrics.invoke({"symbol": symbol, "curr_date": curr_date}) or ""
    except Exception as exc:
        _analyst_log.warning(
            "risk metrics pre-fetch for %s/%s failed: %s", symbol, curr_date, exc,
        )
        snapshot = ""
    _RISK_METRICS_CACHE[key] = snapshot
    return snapshot


def get_sector_strength_for(symbol: str, curr_date: str) -> str:
    """Cached sector-relative-strength snapshot for (symbol, curr_date)."""
    key = (symbol, curr_date)
    if key in _SECTOR_STRENGTH_CACHE:
        return _SECTOR_STRENGTH_CACHE[key]
    try:
        from tradingagents.agents.utils.sector_strength_tools import get_sector_relative_strength
        snapshot = get_sector_relative_strength.invoke(
            {"symbol": symbol, "curr_date": curr_date}
        ) or ""
    except Exception as exc:
        _analyst_log.warning(
            "sector strength pre-fetch for %s/%s failed: %s", symbol, curr_date, exc,
        )
        snapshot = ""
    _SECTOR_STRENGTH_CACHE[key] = snapshot
    return snapshot


_RECOMMENDATION_KR = {
    "strong_buy": "강매수",
    "buy": "매수",
    "hold": "보유",
    "sell": "매도",
    "strong_sell": "강매도",
    "underperform": "비중축소",
    "outperform": "비중확대",
    "none": "없음",
}


def get_market_signals_for(ticker: str) -> str:
    """Format Wall Street consensus + short interest + insider holdings
    into a snippet suitable for injection into analyst / decision
    prompts. Empty string when no relevant signals are available.

    These are deterministic numbers from yfinance — never make the LLM
    fetch them via a tool call. The pattern matches macro/risk/sector
    pre-fetching: Python pulls the data, prompt receives it as fact."""
    info = _instrument_info(ticker)
    if not info:
        return ""

    lines: list[str] = []

    # Wall Street analyst consensus.
    target = info.get("targetMeanPrice")
    current = info.get("currentPrice") or info.get("regularMarketPrice")
    rec_key = (info.get("recommendationKey") or "").lower()
    n_analysts = info.get("numberOfAnalystOpinions")
    rec_mean = info.get("recommendationMean")
    if target and current:
        upside = (target - current) / current * 100
        rec_kr = _RECOMMENDATION_KR.get(rec_key, rec_key or "")
        line = f"- Wall Street 컨센서스: 목표가 ${target:,.2f} (현재가 ${current:,.2f} 대비 {upside:+.1f}%)"
        extras = []
        if rec_kr:
            extras.append(f"등급 {rec_kr}")
        if rec_mean:
            extras.append(f"평균 {rec_mean:.2f}/5 (1=강매수)")
        if n_analysts:
            extras.append(f"{int(n_analysts)}명")
        if extras:
            line += " · " + " · ".join(extras)
        lines.append(line)

    # Forward EPS sanity check. yfinance occasionally serves a stale or
    # malformed forward EPS (often >3x TTM or even negative when the
    # TTM is positive). When the ratio is suspicious, note it explicitly
    # so the analyst doesn't anchor a bullish thesis on a number that
    # may be garbage. SNDK 2026-05-11 had TTM EPS $29.22 vs Forward EPS
    # $169.26 (5.8x) — plausibly real given the Western Digital spin-off
    # but worth flagging for the LLM to verify against the actual
    # earnings statements.
    try:
        from tradingagents.dataflows.config import get_config  # noqa: F401
        import yfinance as yf
        raw = yf.Ticker(ticker).info or {}
        fwd_eps = raw.get("forwardEps")
        ttm_eps = raw.get("trailingEps")
        if (
            isinstance(fwd_eps, (int, float)) and fwd_eps == fwd_eps
            and isinstance(ttm_eps, (int, float)) and ttm_eps == ttm_eps
            and ttm_eps != 0
        ):
            ratio = fwd_eps / ttm_eps
            if abs(ratio) >= 3 or (ttm_eps > 0 and fwd_eps < 0):
                lines.append(
                    f"- ⚠️ Forward EPS ${fwd_eps:.2f} vs TTM EPS ${ttm_eps:.2f}"
                    f" (비율 {ratio:.1f}x) — 통상 범위 밖. spin-off/일회성/"
                    f"yfinance 데이터 오류 가능. 본문에서 이 숫자에 큰 비중을"
                    f" 두기 전에 EPS 추세를 확인하라"
                )
    except Exception:
        pass

    # Short interest.
    short_pct = info.get("shortPercentOfFloat")
    short_ratio = info.get("shortRatio")
    if short_pct or short_ratio:
        parts = []
        if short_pct is not None:
            parts.append(f"공매도 {short_pct * 100:.1f}% of float")
        if short_ratio is not None:
            parts.append(f"days-to-cover {short_ratio:.1f}일")
        if parts:
            risk_hint = ""
            if short_pct and short_pct > 0.10:
                risk_hint = " (10% 초과 — squeeze 위험 ↑)"
            lines.append("- 공매도 비중: " + " · ".join(parts) + risk_hint)

    # Insider holdings.
    insider_pct = info.get("heldPercentInsiders")
    inst_pct = info.get("heldPercentInstitutions")
    if insider_pct or inst_pct:
        parts = []
        if insider_pct is not None:
            parts.append(f"내부자 {insider_pct * 100:.1f}%")
        if inst_pct is not None:
            parts.append(f"기관 {inst_pct * 100:.1f}%")
        if parts:
            lines.append("- 보유 구성: " + " · ".join(parts))

    return "\n".join(lines)


def days_until_earnings(ticker: str, curr_date: str) -> int | None:
    """Return signed integer days until/since the next earnings event,
    or None if yfinance doesn't have a usable timestamp. Positive = days
    in the future, negative = days since release."""
    info = _instrument_info(ticker)
    ts = (
        info.get("earningsTimestampStart")
        or info.get("earningsTimestamp")
    )
    if not ts:
        return None
    try:
        import datetime as _dt
        earnings = _dt.date.fromtimestamp(ts)
        current = _dt.date.fromisoformat(curr_date)
        return (earnings - current).days
    except Exception:
        return None


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified
    tickers and adjust their data expectations for non-equity products."""
    base = (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )
    info = _instrument_info(ticker)
    qt = (info.get("quoteType") or info.get("typeDisp") or "").upper()
    # Inject the GICS-style sector/industry that yfinance actually has on
    # file. Without this the market analyst's SECTOR PRIMER picks one of
    # the prompt's example labels at random and routinely mis-classifies
    # (GOOGL on 2026-05-10 was labelled "AI 인프라 반도체" when its
    # actual classification is Communication Services / Internet Content).
    sector = info.get("sector")
    industry = info.get("industry")
    long_name = info.get("longName")
    facts: list[str] = []
    if long_name:
        facts.append(f"company name: {long_name}")
    if sector:
        facts.append(f"sector: {sector}")
    if industry:
        facts.append(f"industry: {industry}")
    if facts:
        base += (
            "\n\nKnown classification (yfinance, authoritative — use these"
            " exact labels in any sector / industry primer instead of"
            " guessing): " + "; ".join(facts) + "."
        )
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
    else:
        # Equity-only signals: Wall Street consensus, short interest,
        # insider holdings. Pre-fetched from yfinance .info; the analyst
        # quotes them verbatim instead of trying to call a tool for the
        # same data (or worse, hallucinating "데이터 없음"). The decision
        # nodes also see these via the same prompt path, so the bot's
        # verdict can be compared against the Wall Street consensus
        # directly (a 4-0 매수 → 매도 mismatch reads very differently
        # when analysts also point at $517 average target).
        signals = get_market_signals_for(ticker)
        if signals:
            base += (
                "\n\n=== Pre-fetched market signals (yfinance, verbatim —"
                " do NOT call any tool for these numbers; use them in the"
                " fundamentals summary table and let the debate / decision"
                " nodes see them as ground truth) ===\n"
                + signals
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


        
