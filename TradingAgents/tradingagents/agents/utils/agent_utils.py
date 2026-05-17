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
    """Return True iff some news source surfaces ANY article for the
    ticker. Pre-flight gate so the bot can skip the news analyst on
    coverage-poor names (newly-IPO'd, OTC, foreign secondary) instead
    of paying for an analyst that will produce a placeholder and then
    trip the fail-fast guard.

    Source priority:
    1. yfinance .news (US-heavy, the primary path for non-KR tickers).
    2. Naver search via the KR corp name from DART (KR-only fallback).
       Without this, KR tickers like 호텔신라 / 현대모비스 — which
       yfinance returns 0 articles for despite plenty of Korean
       coverage — got prematurely skipped from news/sentiment.

    Conservative on error: any exception returns True so we don't
    spuriously drop the news analyst on a transient network blip —
    the in-graph retry will catch a real failure downstream.
    """
    if ticker in _NEWS_AVAILABILITY_CACHE:
        return _NEWS_AVAILABILITY_CACHE[ticker]
    has = False
    try:
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
        if items:
            has = True
    except Exception as exc:
        _analyst_log.warning(
            "news availability (yfinance) check failed for %s: %s", ticker, exc,
        )
        has = True  # fail open

    # KR fallback: yfinance often shows 0 KR news even when Naver has
    # dozens. Resolve ticker → corp_name via DART and probe Naver.
    if not has:
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "KR":
                from bot.dart_client import get_dart
                code = (ticker or "").upper().split(".")[0]
                kr_name = get_dart().stock_code_to_name(code)
                if kr_name:
                    from bot.naver_news_client import has_recent_korean_news
                    if has_recent_korean_news(kr_name):
                        has = True
        except Exception as exc:
            _analyst_log.warning(
                "news availability (naver) check failed for %s: %s", ticker, exc,
            )
            # don't flip `has` here — yfinance answer stands

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
# Cache key is (market, curr_date) so US and KR get separate snapshots —
# the two series differ (KR pulls USD/KRW + KOSPI + CNY + JPY instead of
# DXY + 5Y/13W treasuries + BTC + gold), and caching them together would
# clobber each other across an analysis session that mixes markets.
_MACRO_CACHE: dict[tuple[str, str], str] = {}


def get_macro_for(symbol: str, curr_date: str) -> str:
    """Return cached macro snapshot, fetching once per (market, curr_date)
    if needed. `symbol` is used only to detect the market — the snapshot
    itself is market-keyed, so two KR tickers on the same date share one
    cached snapshot.

    Empty string on failure — caller decides how to label the section so
    the analyst prompt can include a graceful fallback line without the
    LLM apologising."""
    try:
        from bot.market import detect_market
        market = detect_market(symbol)
    except Exception:
        market = "US"
    key = (market, curr_date)
    if key in _MACRO_CACHE:
        return _MACRO_CACHE[key]
    try:
        # Late import: macro_context_tools imports yfinance which is
        # heavy enough to keep out of the agent_utils module load path
        # for callers that never need the snapshot.
        from tradingagents.agents.utils.macro_context_tools import get_macro_context
        snapshot = get_macro_context.invoke(
            {"curr_date": curr_date, "market": market}
        ) or ""
    except Exception as exc:
        _analyst_log.warning(
            "macro pre-fetch for %s/%s failed: %s — analyst will run without snapshot",
            symbol, curr_date, exc,
        )
        snapshot = ""
    _MACRO_CACHE[key] = snapshot
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
    pre-fetching: Python pulls the data, prompt receives it as fact.

    For KR tickers: yfinance is tried first (KOSPI Top 100 reliable),
    and if the consensus block comes back empty, FnGuide CompanyGuide
    is scraped as a fallback. Small-cap KOSDAQ may have no coverage
    anywhere — in that case the block is omitted silently rather than
    fabricated."""
    info = _instrument_info(ticker)
    if not info:
        return ""

    # Market-dependent labels / formatting. KRW prices are integers (no
    # decimal in the trading screen); USD prices carry 2 decimals.
    try:
        from bot.market import detect_market, get_market_config
        market = detect_market(ticker)
        cfg = get_market_config(ticker)
    except Exception:
        market = "US"
        cfg = {"currency": "USD", "currency_symbol": "$"}
    sym = cfg["currency_symbol"]
    price_fmt = "{:,.0f}" if cfg["currency"] == "KRW" else "{:,.2f}"
    consensus_label = (
        "Wall Street 컨센서스" if market == "US" else "애널리스트 컨센서스"
    )

    lines: list[str] = []

    # Wall Street / KR analyst consensus — yfinance first.
    target = info.get("targetMeanPrice")
    current = info.get("currentPrice") or info.get("regularMarketPrice")
    rec_key = (info.get("recommendationKey") or "").lower()
    n_analysts = info.get("numberOfAnalystOpinions")
    rec_mean = info.get("recommendationMean")

    # FnGuide CompanyGuide scrape for KR tickers. Two uses out of one
    # fetch: (1) fallback when yfinance returns no target (mid / small
    # caps), (2) last_report_date for concrete consensus-staleness
    # detection. Fetched unconditionally for KR so the date check
    # works even when yfinance has the consensus block populated.
    fn_data: dict | None = None
    if market == "KR":
        try:
            from bot.fnguide_consensus import fetch_consensus
            fn_data = fetch_consensus(ticker)
        except Exception as exc:
            _analyst_log.warning(
                "fnguide fetch failed for %s: %s", ticker, exc,
            )

    if market == "KR" and not target and fn_data:
        target = target or fn_data.get("target_mean")
        n_analysts = n_analysts or fn_data.get("n_analysts")
        fn_rating = fn_data.get("rating")
        if fn_rating and not rec_key:
            rec_key = {
                "매수": "buy", "보유": "hold", "매도": "sell",
            }.get(fn_rating, "")

    if target and current:
        upside = (target - current) / current * 100
        rec_kr = _RECOMMENDATION_KR.get(rec_key, rec_key or "")
        line = (
            f"- {consensus_label}: 목표가 {sym}{price_fmt.format(target)}"
            f" (현재가 {sym}{price_fmt.format(current)} 대비 {upside:+.1f}%)"
        )
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

        # Staleness warning: when the consensus mean target sits >=20% BELOW
        # the current price, the average is almost always stale post-rally.
        # Individual analysts re-rate one at a time, so the mean lags by
        # weeks after a sharp move (DELL 2026-05-12: UBS raised target to
        # $243 and Mizuho to $260, but the yfinance mean was still $189.61
        # because most of the 23 analysts hadn't updated yet). Without
        # this flag the decision LLM tends to read the gap as "ground
        # truth — institutions think it's overpriced" and over-weights
        # the bear case. Threshold mirrors the magnitude where staleness
        # dominates real disagreement.
        if upside <= -20:
            lines.append(
                f"  ⚠️ 현재가가 컨센서스 목표가보다 {-upside:.0f}% 높음 — 최근 랠리로"
                f" 다수 애널리스트가 목표가를 아직 업데이트하지 않았을 가능성이 큼."
                f" 평균값을 'ground truth'로 사용하기 전에 본문의 개별"
                f" 상향/하향 조정 뉴스와 대조하라"
            )
        elif upside >= 30:
            # Reverse direction: current price << consensus target by a
            # wide margin. Two equally common causes: (a) real
            # undervaluation, (b) analysts haven't pulled their targets
            # down after a sustained selloff — same staleness pattern as
            # the rally case above, just the other direction. 한국전력공사
            # 2026-05-17 had +53.8% upside and the analyst happily quoted
            # it as a buy signal; could just as easily be lagging targets.
            lines.append(
                f"  ⚠️ 컨센서스 목표가가 현재가보다 {upside:.0f}% 높음 — 최근 매도세"
                f" 이후 일부 애널리스트가 목표가를 아직 하향하지 않았을 가능성도 큼."
                f" 평균값을 'ground truth'로 사용하기 전에 본문의 개별 등급 강등 /"
                f" 목표가 하향 뉴스와 대조하라."
            )

        # Concrete staleness signal (KR only) — actual date of the last
        # analyst report from FnGuide. The gap-based heuristics above
        # are indirect; this is the literal date. > 30 days = mean
        # target is genuinely lagging individual revisions.
        if fn_data and fn_data.get("last_report_date"):
            from datetime import datetime as _dt, date as _date
            try:
                last_dt = _dt.strptime(fn_data["last_report_date"], "%Y-%m-%d").date()
                days_old = (_date.today() - last_dt).days
                if days_old > 30:
                    lines.append(
                        f"  ⚠️ FnGuide 최근 리포트 갱신일 {last_dt.isoformat()}"
                        f" ({days_old}일 전) — 컨센서스 평균이 stale일 가능성이"
                        f" 매우 큼. 본문 사용 시 'consensus snapshot이 {days_old}"
                        f"일 전 시점' caveat 명시."
                    )
            except Exception:
                pass

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
                # EPS is per-share earnings — use the same currency
                # symbol as the price line so the report doesn't mix
                # $ and ₩ in the same paragraph for KR tickers.
                lines.append(
                    f"- ⚠️ Forward EPS {sym}{fwd_eps:.2f} vs TTM EPS {sym}{ttm_eps:.2f}"
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


def _format_dart_kr_block(
    disclosures: list[dict],
    insiders: list[dict],
    earnings_window,
) -> str:
    """Render DART (KR-only) data as a single prompt-injection block.
    Returns '' when all three slots are empty so the caller can decide
    to skip the section header entirely."""
    from datetime import date

    lines: list[str] = []

    # Recent disclosures — most useful for catching guidance updates,
    # M&A, lawsuits in the last month. Cap at 8 to keep the prompt
    # compact; the LLM only needs the gist, not the full filing list.
    if disclosures:
        lines.append("- 최근 30일 공시 (상위 {}건):".format(min(len(disclosures), 8)))
        for d in disclosures[:8]:
            date_str = d.get("date") or ""
            # DART returns dates as 'YYYYMMDD'; render as 'YYYY-MM-DD'.
            if len(date_str) == 8 and date_str.isdigit():
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            title = (d.get("title") or "").strip()
            if title:
                lines.append(f"  • {date_str}: {title}")

    # Insider / major shareholder holdings. DART returns rolling
    # history rows per person; dedupe by name keeping the highest pct
    # snapshot (most-recent ≈ highest in stable holdings; for active
    # traders this rule undercounts which is fine for a snapshot).
    if insiders:
        by_name: dict[str, dict] = {}
        for r in insiders:
            name = r.get("name") or ""
            if not name:
                continue
            prev = by_name.get(name)
            if prev is None or (r.get("pct") or 0) > (prev.get("pct") or 0):
                by_name[name] = r
        top = sorted(by_name.values(), key=lambda r: r.get("pct") or 0, reverse=True)[:5]
        # State-owned enterprises (한국전력공사, 한국가스공사, 등) have
        # officers who are civil servants — they hold ~zero stock. The
        # raw '상위 5 모두 0.00%' list looks broken to a reader. Detect
        # the all-near-zero pattern and replace with a single explanatory
        # line so the user doesn't think DART data is broken.
        if top:
            nonzero = [r for r in top if (r.get("pct") or 0) >= 0.01]
            if not nonzero:
                lines.append(
                    "- 임원·주요주주 지분: 공기업 / 공공 entity — 임원진은"
                    " 공직자 신분이라 의미 있는 보유 없음 (정상). 지배"
                    " 구조는 주요 주주 (산업은행 / 정부 등) 기준으로"
                    " 분석할 것."
                )
            else:
                lines.append(f"- 임원·주요주주 지분 (상위 {len(top)}):")
                for r in top:
                    name = r.get("name") or "?"
                    role = (r.get("role") or "").strip()
                    pct = r.get("pct") or 0
                    role_part = f" ({role})" if role else ""
                    lines.append(f"  • {name}{role_part}: {pct:.2f}%")

    # Next earnings filing window — inferred from KR statutory deadlines.
    if earnings_window:
        start, end = earnings_window
        days = (start - date.today()).days
        if days >= 0:
            lines.append(
                f"- 다음 정기보고서 윈도: {start.isoformat()} ~ {end.isoformat()}"
                f" (약 {days}일 후)"
            )
        else:
            # Window has started but not ended — earnings imminent / overdue.
            lines.append(
                f"- 정기보고서 윈도 진행 중: {start.isoformat()} ~ {end.isoformat()}"
                f" (마감 {(end - date.today()).days}일 남음)"
            )

    return "\n".join(lines)


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

    # Market detection + DART name lookup. We do this once at the top
    # because three downstream branches need the result: (1) the
    # quoteType override, (2) the company-name display, (3) the
    # currency directive.
    try:
        from bot.market import detect_market, get_market_config
        market = detect_market(ticker)
        _cfg = get_market_config(ticker)
    except Exception:
        market = "US"
        _cfg = {"currency": "USD", "currency_symbol": "$"}

    kr_name: str | None = None
    if market == "KR":
        try:
            from bot.dart_client import get_dart
            code = (ticker or "").upper().split(".")[0]
            kr_name = get_dart().stock_code_to_name(code)
        except Exception:
            kr_name = None

        # yfinance occasionally tags KOSDAQ-listed real companies as
        # MUTUALFUND / ETF / ETN — 039030.KS 이오테크닉스 on
        # 2026-05-17 was MUTUALFUND, which cascaded the whole analysis
        # into fund mode (all four analysts wrote '펀드 상품입니다'
        # in their bodies, sentiment failed, DART block was skipped).
        # DART corp_code only lists real corporations, so if DART
        # knows this stock_code, override yfinance and treat it as
        # equity. Real KR ETFs (KODEX 200, TIGER 200, etc.) aren't
        # in DART, so they fall through to the fund branch correctly.
        if qt in ("ETF", "ETN", "MUTUALFUND") and kr_name:
            qt = "EQUITY"

    # Inject the GICS-style sector/industry that yfinance actually has on
    # file. Without this the market analyst's SECTOR PRIMER picks one of
    # the prompt's example labels at random and routinely mis-classifies
    # (GOOGL on 2026-05-10 was labelled "AI 인프라 반도체" when its
    # actual classification is Communication Services / Internet Content).
    sector = info.get("sector")
    industry = info.get("industry")
    long_name = info.get("longName")
    # For KR tickers, prefer DART's Korean corp name over yfinance's
    # English longName — readers can't recognize '이오테크닉스' as
    # 'Eo Technics Co Ltd' at a glance.
    if market == "KR" and kr_name:
        long_name = kr_name
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

    # COMPS INDUSTRY MATCH DIRECTIVE — every peer in the Comps table
    # MUST match this exact industry string. Without this, the LLM
    # cargo-cults whatever example peer set is closest to the prompt,
    # even across industries. 한국전력공사 (015760.KS, Utilities -
    # Regulated Electric) 2026-05-17 listed KMI / WMB / ENB (oil-gas
    # midstream) + 000660.KS SK하이닉스 (semiconductor) — none of
    # which match the subject's industry. Inject the subject industry
    # as a hard constraint here so the analyst can't drift.
    if industry:
        base += (
            f"\n\n=== COMPS INDUSTRY CONSTRAINT (MANDATORY) ===\n"
            f"Subject's yfinance industry: '{industry}'.\n"
            f"EVERY ticker in your Comps table MUST have yfinance"
            f" industry == '{industry}' (exact string match) OR be a"
            f" globally-recognized direct competitor in the same product"
            f" market. FORBIDDEN: borrowing example peer sets from a"
            f" different industry just because the example tickers look"
            f" Korean or look 'utility-adjacent'.\n"
            f"  ❌ WRONG (한국전력공사 2026-05-17): KMI / WMB / ENB are"
            f" 'Oil & Gas Midstream', NOT 'Utilities - Regulated"
            f" Electric'. 000660.KS is 'Semiconductors', completely"
            f" unrelated. Mixing these in a Utilities Comps table is"
            f" cargo-cult, not peer analysis.\n"
            f"  ❌ WRONG (호텔신라 2026-05-17): listed 삼성전자 /"
            f" SK하이닉스 in a Specialty Retail Comps table; even"
            f" after self-noting the mismatch the table was kept.\n"
            f"  ✅ RIGHT for '{industry}': pick tickers whose yfinance"
            f" industry string LITERALLY matches '{industry}'. If you"
            f" cannot name 3 such tickers from memory, write ONE honest"
            f" sentence ('국내 직접 경쟁사 부재 — 글로벌 비교 보류') and"
            f" skip the Comps section. Fabricated peers worse than no"
            f" peers."
        )

        # MANDATORY PEER SET — when we have a curated peer list for
        # this industry (bot/market._KR_INDUSTRY_PEERS), inject it
        # directly so the analyst has no choice. Text-only rules
        # don't stop the LLM from cargo-culting; a literal "use these
        # five tickers" line does.
        try:
            from bot.market import resolve_peer_set
            peers = resolve_peer_set(ticker, industry)
            if peers:
                peer_lines = "\n".join(f"  • {t}" for t in peers)
                base += (
                    f"\n\n=== MANDATORY COMPS PEER SET ===\n"
                    f"For industry '{industry}', use EXACTLY these"
                    f" peer tickers in the Comps table — do NOT add,"
                    f" remove, or substitute any of them:\n"
                    f"{peer_lines}\n"
                    f"This list is curated to match the yfinance"
                    f" 'industry' field. Fabricating different peers"
                    f" (or borrowing from a different industry's"
                    f" example list) is FORBIDDEN."
                )
        except Exception:
            pass

    # KR body-text directive — readers can't parse '039030.KS' alone.
    # Force every analyst to surface the Korean corp name in narrative
    # sentences, not just bury it in the classification fact above.
    if market == "KR" and kr_name:
        base += (
            f"\n\n=== KR NAMING DIRECTIVE (MANDATORY) ===\n"
            f"When referring to the company in ANY narrative sentence,"
            f" use one of these forms — NEVER the bare numeric ticker:\n"
            f" • '{kr_name} ({ticker})' on first mention per section\n"
            f" • '{kr_name}' (Korean name only) on subsequent mentions\n"
            f"❌ WRONG: '{ticker}는 최근 급락하며...'\n"
            f"✅ RIGHT: '{kr_name} ({ticker})는 최근 급락하며...'"
            f" or '{kr_name}는 최근 급락하며...'"
        )

    # Single source of truth for the current price. Without this, every
    # analyst can pick a different number — SNPS 2026-05-13 had the
    # sentiment analyst quoting $497.5 while fundamentals quoted $513.21,
    # because one grabbed an older trailing close and the other read
    # currentPrice from yfinance .info. The trader and PM then can't tell
    # which "현재가" anchors the verdict. Inject the canonical number
    # here so every prompt sees the same value and stops re-fetching.
    # Currency rendering follows the ticker's market — KRW is whole-won
    # integer, USD/JPY/CNY get two decimals.
    _sym = _cfg.get("currency_symbol", "$")
    _fmt = "{:,.0f}" if _cfg.get("currency") == "KRW" else "{:,.2f}"
    px = info.get("currentPrice") or info.get("regularMarketPrice")
    if isinstance(px, (int, float)) and px == px and px > 0:
        base += (
            f"\n\nCanonical current price (yfinance, point-in-time — use"
            f" this single value verbatim for any '현재가 / current price'"
            f" reference in your report; do NOT quote a different price"
            f" derived from a trailing close or a tool call): {_sym}{_fmt.format(px)}"
            f"\n\nCANONICAL PRICE PRECISION RULES:\n"
            f" • Summary tables / 펀더멘털 표 / DCF inputs / 진입가 / 손절가"
            f" cells: use the EXACT canonical value ({_sym}{_fmt.format(px)})."
            f" Rounding in these cells loses the precision the downstream"
            f" decision LLM needs.\n"
            f" • Narrative prose: light rounding for readability is OK"
            f" but stay close ('약 {_sym}{_fmt.format(round(px, -3) if px > 1000 else px)}'"
            f" or '약 {_sym}{_fmt.format(px)}'). '₩약 1.0백만' style"
            f" (삼성전기 2026-05-17) loses 1% of precision AND uses the"
            f" awkward '백만' unit — both forbidden."
        )

        # Price-gap sanity check. yfinance's 50-day / 200-day averages
        # are computed from historical closes that should be
        # split-adjusted, so a current price that differs from either
        # SMA by more than 30% almost always indicates one of:
        # (a) recent stock split not yet propagated to either current
        # or historical series, (b) yfinance KR data quality issue,
        # or (c) a genuinely catastrophic / explosive move. In all
        # three cases the technical indicators (10 EMA, MACD, RSI,
        # Bollinger bands) computed from the historical series need
        # to be cross-checked before the analyst anchors a directional
        # thesis on them.
        #
        # Threshold lowered from 40% to 30% on 2026-05-17 after 삼성전기
        # (009150.KS, current ₩1,010,000 vs 50d SMA ₩614,960 =
        # +64% gap) failed to trigger — the analyst happily built a
        # full bullish-momentum analysis on '50% 한 달 상승' which
        # may or may not be real. 30% is the threshold where
        # split-staleness / data-quality cases dominate organic moves.
        # 039030.KS (이오테크닉스) -55% gap and 319660.KS (피에스케이)
        # -62% gap both stay caught. The 200-day check covers cases
        # where the 50d SMA happens to track current closely but the
        # longer window reveals an outlier.
        for window_label, key in [("50일 SMA", "fiftyDayAverage"),
                                  ("200일 SMA", "twoHundredDayAverage")]:
            sma = info.get(key)
            if not (isinstance(sma, (int, float)) and sma > 0):
                continue
            gap = abs(px - sma) / sma
            if gap <= 0.30:
                continue
            direction = "below" if px < sma else "above"
            base += (
                f"\n\n⚠️ PRICE GAP SANITY — current price"
                f" {_sym}{_fmt.format(px)} is {gap*100:.0f}%"
                f" {direction} the {window_label}"
                f" {_sym}{_fmt.format(sma)}. This usually"
                f" indicates a stock-split adjustment issue in"
                f" the historical price series (yfinance KR / JP"
                f" sometimes lags), not a real ~{gap*100:.0f}%"
                f" intra-window move. DO NOT build a directional"
                f" thesis on 10-EMA / 50-SMA / 200-SMA / MACD /"
                f" Bollinger comparisons that hinge on this gap;"
                f" quote the canonical current price + recent"
                f" realized volatility instead, and explicitly"
                f" note the data quality caveat in the report."
            )
            break  # one warning per analysis is enough — don't spam both windows

    # Currency directive for KR tickers. yfinance returns KRX-listed
    # companies' financial fields (marketCap, totalRevenue, netIncome,
    # epsActualX) in **KRW**, not USD. Without this directive the
    # fundamentals analyst defaults to its US-trained habit of labeling
    # the table in "백만 달러" / "조 달러" — SNG 2026-05-17 produced
    # '시가총액: 약 약 1776.26조 달러' (correct number, wrong unit).
    try:
        if _cfg.get("currency") == "KRW":
            base += (
                "\n\n=== CURRENCY DIRECTIVE (KR ticker, MANDATORY) ===\n"
                "This is a KRX-listed company. EVERY monetary value from"
                " yfinance .info / financial statements is in **KRW**,"
                " never USD. When you render the fundamentals summary"
                " table and body:\n"
                " • Use Korean scale units '조 원' (≥1조), '억 원'"
                " (1억 ~ 1조 미만), '만 원' (1만 ~ 1억 미만). NEVER"
                " '백만' / '백만 원' — that's a million-style English"
                " unit awkward in Korean. '₩약 1.0백만' (삼성전기"
                " 2026-05-17) → use '₩1,010,000' or '약 101만 원' instead.\n"
                " • Never '달러' / 'USD' / '백만 달러' for KR tickers.\n"
                " • Headline-scale numbers (시가총액, 매출, 순이익,"
                " FCF) ROUND to two significant figures at the unit"
                " scale: '약 8,840억 원' or '약 0.88조 원'.\n"
                " • EPS / 주당 배당금 stays as integer KRW: '₩6,603'.\n"
                " • Per-share DCF outputs (Bear/Base/Bull): '₩XXX,XXX'.\n"
                "FORBIDDEN — place-by-place spelling. KR readers cannot"
                " skim a 12-character literal Korean place readout. The"
                " raw yfinance integer (e.g. 883968714100) MUST be"
                " converted to an abbreviated form before it appears in"
                " the report; do NOT just type out every place value.\n"
                " ❌ WRONG: '시가총액: 약 1776조 달러' (불가능한 단위)\n"
                " ❌ WRONG: '총 매출: FY25 8839억 6871만 4100원' (자리수별"
                " 풀스펠 — 한솔케미칼 2026-05-17 이 정확히 이 실수)\n"
                " ❌ WRONG: '₩약 1.0백만' or '약 1.0백만 원' ('백만'"
                " 단어 자체 금지 — 삼성전기 2026-05-17 이 실수)\n"
                " ✅ RIGHT: '시가총액: 약 1,776조 원'\n"
                " ✅ RIGHT: '총 매출 (억 원): FY25 8,840 | FY24 7,764'"
                " (table column header carries the unit, cells stay"
                " plain numbers)\n"
                " ✅ RIGHT: '약 0.88조 원' for headline prose mention.\n"
                " ✅ RIGHT: '₩1,010,000' (정확) or '약 101만 원' (반올림)"
                " for 100만 원~1억 원 range values."
            )
    except Exception:
        pass
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

        # DART (KR-only) — 공시 / 임원지분 / 실적 윈도. yfinance returns
        # nothing useful for these on KRX-listed names; DART is the
        # authoritative source. Graceful degradation: if DART_API_KEY is
        # missing or the API is down, the block is just empty and the
        # analyst continues without it.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "KR":
                from bot.dart_client import get_dart
                dart = get_dart()
                disclosures = dart.get_recent_disclosures(ticker, days_back=30, limit=8)
                insiders = dart.get_insider_holdings(ticker)
                window = dart.next_earnings_window(ticker)
                dart_block = _format_dart_kr_block(disclosures, insiders, window)
                if dart_block:
                    base += (
                        "\n\n=== Pre-fetched KR market data (DART, verbatim —"
                        " do NOT call any tool for these numbers; use them in"
                        " the news / fundamentals / risk sections as ground"
                        " truth) ===\n"
                        + dart_block
                        + "\n\nRENDERING RULES for the DART block:\n"
                        " • 최근 공시: render as a bullet list, one filing"
                        " per line, with the date prefix preserved.\n"
                        " • 임원·주요주주 지분: render as a bullet list or"
                        " table, EVERY name with its exact % (e.g. '국민연금"
                        " 10.74%, 장덕현 대표이사 0.05%, ...'). 삼성전기"
                        " 2026-05-17 buried this in a narrative sentence"
                        " ('대표이사 장덕현을 비롯한 임원진도 소량의 지분을"
                        " 보유') with no individual % — FORBIDDEN. The"
                        " reader needs the actual % per person.\n"
                        " • 다음 정기보고서 윈도: one prose sentence is fine."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "dart context injection failed for %s: %s", ticker, exc,
            )

        # KRX investor-type trading flow (KR-only) — foreign /
        # institutional / individual net purchases over 5 trading
        # days. KR markets are dominated by foreign + institutional
        # flow on short horizons (foreigners selling = KOSPI down,
        # regardless of single-stock fundamentals). yfinance has
        # nothing on this; pykrx wraps the KRX endpoint. Inject so
        # the market analyst can size the directional bias correctly
        # — current bot was missing the most important KR short-term
        # signal entirely.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "KR":
                from bot.pykrx_client import (
                    get_kr_trading_flow,
                    format_flow_for_prompt,
                    get_kr_foreign_ownership_trend,
                    get_kr_short_balance_trend,
                    format_trend_for_prompt,
                )
                flow = get_kr_trading_flow(ticker, days_back=5)
                if flow:
                    base += (
                        "\n\n=== Pre-fetched KR investor flow (KRX,"
                        " verbatim — quote in 시장 분석 본문) ===\n"
                        + format_flow_for_prompt(flow)
                        + "\n\nINTERPRETATION GUIDE (mandatory):"
                        " foreign + institutional + individual must"
                        " sum to ~zero by construction. The signal"
                        " in 5거래일 horizon comes from the"
                        " direction + magnitude of the foreign +"
                        " institutional rows. '외국인 순매수 +수백억"
                        " 원' is a bullish short-term signal even"
                        " if technicals look mixed; '외국인 순매도"
                        " -수백억 원' is a bearish signal even with"
                        " strong fundamentals. Cite this flow"
                        " explicitly in the 시장 분석 결론 — it is"
                        " the single most predictive KR-market"
                        " short-term variable and the bot was"
                        " missing it before this commit."
                    )

                # 30-day positioning trends: foreign ownership pct +
                # short-balance pct. Daily flow (above) shows what
                # happened in the last 5 days; these show longer
                # positioning trajectory (foreigners accumulating
                # vs distributing, shorts building vs squeezing).
                foreign_trend = get_kr_foreign_ownership_trend(ticker, days_back=30)
                short_trend = get_kr_short_balance_trend(ticker, days_back=30)
                trend_block = format_trend_for_prompt(foreign_trend, short_trend)
                if trend_block:
                    base += (
                        "\n\n=== Pre-fetched KR positioning trends"
                        " (KRX, 30일 추이) ===\n"
                        + trend_block
                        + "\n\n외국인 지분율 추세 (꾸준한 증가 vs"
                        " 꾸준한 감소)는 5일 flow보다 안정적인"
                        " 신호이다. 공매도 잔고 감소 + 가격 상승"
                        " 동반은 short squeeze 청산 진행. 본문"
                        " 결론에 한 줄로 인용하라."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "pykrx flow / trend injection failed for %s: %s", ticker, exc,
            )

        # Korean news via Naver search (KR-only). Alpha Vantage and
        # yfinance don't index 한국어 publishers (한경 / 매경 / 연합
        # / etc.) reliably, which is why 호텔신라 / 현대모비스
        # 2026-05-17 had the news/sentiment analysts fall back to
        # "data not found" or to citing one unrelated US battery
        # company headline as their sole datapoint. Naver fills the
        # gap with 25K free calls/day. Query by the KR corp name we
        # already resolved above (kr_name from DART) — the ticker
        # code '012330.KS' doesn't surface KR news. Inject the top
        # 10 most-recent articles so the news/sentiment analysts
        # have a real KR source to build on.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "KR" and kr_name:
                from bot.naver_news_client import fetch_news, format_news_for_prompt
                news_items = fetch_news(kr_name, days_back=28, max_items=10)
                if news_items:
                    base += (
                        "\n\n=== Pre-fetched KR news (Naver 검색, verbatim) ===\n"
                        + format_news_for_prompt(news_items)
                        + "\n\n위 한국어 뉴스가 한국 종목에 대한"
                        " primary news source이다. 영문 뉴스 (Alpha"
                        " Vantage / yfinance) 가 비어있더라도 위"
                        " 리스트를 활용해 news / sentiment 분석을"
                        " 진행하라. 미국 무관 뉴스 (예: 미국 배터리"
                        " 회사 헤드라인) 끌어다가 간접 추론으로"
                        " 메우지 말 것 — 호텔신라 2026-05-17 케이스가"
                        " 그 실수. 위 리스트가 비어있는 경우만"
                        " 한국어 뉴스 부재로 인정."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "naver news injection failed for %s: %s", ticker, exc,
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


        
