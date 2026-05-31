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


def safe_invoke_text(llm, prompt, label: str) -> str:
    """Invoke an LLM for a free-text argument, returning normalized text or
    a short Korean placeholder on failure (M4, 2026-05-29 audit).

    Used by the ADVISORY debate / risk nodes (bull, bear, aggressive,
    neutral, conservative) which previously called `llm.invoke(prompt)`
    bare + read raw `.content`. A single transient Gemini 503 / timeout in
    any one of those turns propagated out of `graph.invoke`, killed the
    analysis subprocess, and discarded the already-computed 4 analyst
    reports (checkpoint_enabled=False → no resume). These voices are
    advisory — the Portfolio Manager can still synthesize from the analyst
    reports + research plan if one debate voice degrades — so failing one
    turn to a placeholder is strictly better than crashing the run. Also
    normalizes multi-part list `.content` (the `_content_to_str` fix)."""
    try:
        return _content_to_str(llm.invoke(prompt))
    except Exception as exc:
        _analyst_log.warning(
            "%s llm.invoke failed (%s) — degrading to placeholder", label, exc,
        )
        return (
            f"({label} 생성 실패 — 일시적 LLM 오류로 이 턴은 건너뜀."
            " 나머지 분석가 근거 기반으로 합성 진행.)"
        )


def _call_with_timeout(fn, timeout_sec: float, label: str, default=None):
    """Run ``fn()`` with a hard wall-clock timeout via a throwaway thread.
    Returns fn()'s result, or ``default`` on timeout / exception (F5,
    2026-05-29 audit).

    AKShare wraps ``requests`` with no socket timeout + 2s/4s retry backoff,
    so a hung Eastmoney / Sina endpoint could block build_instrument_context
    — and therefore the whole NOAH /ticker analysis — indefinitely (the same
    'stuck >15min' class the screener already band-aids at the orchestrator
    level, bot/screener.py:1560+). Bounding the inline call here protects the
    /ticker path too. A timed-out thread finishes in the background and its
    result is discarded."""
    import concurrent.futures as _cf
    ex = _cf.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=timeout_sec)
    except Exception as exc:
        _analyst_log.debug(
            "%s timed out/failed (%ss cap): %s", label, timeout_sec, exc,
        )
        return default
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


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

    Anti-mixing clause added 2026-05-19 after 茅台 600519.SS first-CN-run
    surfaced research_manager outputting full English ("Okay, let's break
    this down. The bull argument carried the day here...") + Korean-English
    mixing patterns ("be 신중함 with sizing", "Let's move to Overweight").
    Root cause: research_manager prompt body includes English Bull/Bear
    debate history; Gemini's language matching defaults to the surrounding
    context unless the directive is loud + repeated. Even with the language
    instruction at the end of the prompt, the LLM pattern-matches the long
    English debate body when generating its synthesis. Strengthen with
    explicit anti-mixing examples + structural separation.
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
        f"\n\nANTI-MIXING RULE (strict): even when the prompt body / debate"
        f" history / tool outputs are in English, your OUTPUT must be"
        f" 100% {lang}. Do not mix one or two English words into a {lang}"
        f" sentence (e.g. 'Let's move to Overweight position' / 'be 신중함"
        f" with sizing' / 'we are betting the new fundamental inputs').  If"
        f" you find yourself starting an English phrase, REWRITE it in"
        f" {lang} before emitting. Allowed exceptions (English OK):"
        f" rating labels (Buy/Hold/Sell/Overweight/Underweight),"
        f" ticker symbols, indicator names, currency codes, named entities"
        f" (Wall Street, FOMC, S&P 500). Everything else — the actual"
        f" prose, bullet content, conclusions, rationales — MUST be in"
        f" {lang}.\n"
        f"❌ WRONG ({lang} + English mix): 'Okay, let's break this down.'"
        f" / 'The bull argument carried the day here.' / 'we have concrete,"
        f" positive catalysts'\n"
        f"✅ RIGHT (clean {lang}): '강세 측 주장이 우세했다.' /"
        f" '구체적인 긍정 catalyst 가 확인된다.' / '본 5거래일 시점에서는'"
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
        "\n\n5거래일 HORIZON ANCHORING (mandatory — applies to 모든 4 분석가):"
        " 본 분석의 평가 horizon 은 **5거래일 raw return** (vs SPY / 섹터 ETF 알파)."
        " 분석가 결론 + 본문 전체가 이 horizon 에 닻을 내려야 한다:\n"
        " (1) Valuation 단독 thesis ('PER 192배 비쌈' / 'PEG > 2 부담' / 'DCF Bear case'"
        " 등) 는 6-12개월 thesis 라 5거래일 verdict 의 핵심 근거가 되면 안 된다."
        " Valuation 은 background context 로만 인용, 결론의 dominant driver 로"
        " 쓰지 말 것. 마오타이 / SMIC 2026-05-19 검증에서 펀더멘털 결론이 '높은"
        " 밸류에이션 부담' 만 인용하고 5거래일 동인 (가격 인상 catalyst / 港股통"
        " flow / RSI extreme) 누락 패턴 발견 — 차단.\n"
        " (2) 결론에 5거래일 가격 방향성을 지배하는 변수 최소 1개 명시 의무:"
        " (a) RULE 10/11/12/13/14 의 dominant 변수 (산업별 정책 / 매크로),"
        " (b) imminent catalyst (어닝 D-5 / FOMC / 가격 인상 / 신제품 출시 / M&A /"
        " FDA / entity list / 판호 등 — PM trigger 패턴과 동일 셋),"
        " (c) 단기 수급 신호 (외국인 flow / 港股통 flow / 신용잔고 / 대차잔고 추이),"
        " (d) 기술적 extreme (RSI ≥75 또는 ≤25, MACD divergence,"
        " corp action HARD GUARD 등).\n"
        " (3) 결론 마지막 verdict 라인은 '5거래일 horizon' 관점 명확히 안고 작성."
        " 펀더멘털 결론에 '장기 보유 의견' / '중장기 관점' 톤 사용 금지 — 그건"
        " 12개월 thesis 이고 본 봇의 evaluation grid 와 무관. 결론에 12개월"
        " thesis 톤이 섞이면 reader 가 5거래일 vs 12개월 mixed signal 로 오인.\n"
        " (4) Generic narrative 변수 사용 금지 (Fix K 강화, 2026-05-19 노바렉스"
        " 194700.KS surfaced): '수요 변동' / '경쟁 심화' / '시장 트렌드 변화' /"
        " '거시 환경 변화' / '경기 사이클 영향' 같은 abstract / generic 변수만"
        " 적은 결론은 위 (2) 의 4 카테고리 (산업 dominant / imminent catalyst /"
        " 단기 수급 / 기술 extreme) 중 어느 것에도 해당 안 됨 → **RULE 위반**."
        " 노바렉스 케이스: 펀더멘털 결론에 '건강기능식품 시장의 단기적인 수요"
        " 변동 및 경쟁 심화가 단기 가격 방향을 결정' 만 적혀 specific catalyst"
        " 부재 — 5거래일 horizon 분석으로 의미 없음.\n"
        " ✅ 정답 예시 (4 카테고리 중 specific 변수 1개):\n"
        "  • '6월 정기보고서 발표 D-X 의 어닝 surprise 가능성'\n"
        "  • '외국인 5거래일 누적 +50억 / -30억 매수 방향'\n"
        "  • 'RSI 78 과매수 zone — 단기 조정 가능성'\n"
        "  • 'KRX 시장경보 단기과열 분류 — 5일 정상 가격 분석 보류'\n"
        "  • '韓 정부 K-바이오 보조금 발표 D-3' (산업 dominant)\n"
        " ❌ 금지 예시 (generic / 결정 불가):\n"
        "  • '수요 변동' / '경쟁 심화' / '시장 트렌드'\n"
        "  • '회사의 견고한 펀더멘털' / '안정적인 성장'\n"
        "  • '거시 환경 불확실성' (구체적 지표 없으면 RULE 위반)\n"
        " ❌ 12개월 thesis 톤 forbidden phrases (B1 강화, 2026-05-21):\n"
        " 다음 phrase 들은 5거래일 horizon 결론에 등장 시 자동 RULE 위반"
        " — 6-12개월 thesis 의 vocabulary 라 본 평가 grid 와 mismatch:\n"
        "  • '장기적 관점' / '장기적으로' / '중장기'\n"
        "  • '장기 보유 의견' / '장기 보유 추천' / '중장기 보유'\n"
        "  • '중장기 성장 잠재력' / '장기 성장 동력' / '구조적 성장'\n"
        "  • '안정적인 배당주' / '배당 매력' / '가치주 매력' (단독 사용)\n"
        "  • '안전 자산 성격' / '방어주' (단독 사용 시)\n"
        "  • '바닥 매수' / '저점 매수' (timing 5거래일 명시 없으면)\n"
        "  • 'Buy & Hold' / '장기 투자 권고'\n"
        " 본문에서 6-12개월 context 로 인용은 OK 지만 verdict 라인 + 직전"
        " 1줄에는 반드시 금지. 위반 시 PM override discipline 의 mismatch"
        " warning 발화.\n"
        " Specific 변수 1개도 명시 못 하면 'HOLD + 5거래일 horizon 의 dominant"
        " driver 없음, 추가 모니터링 권고' 한 줄로 결론 마무리 — generic"
        " narrative 로 fallback 금지.\n"
        "\nMACRO SUSPECT HARD GUARD propagation (Fix N, 2026-05-19 LG생활건강"
        " 051900.KS surfaced — 거시 블록에 'KOSPI 검증 미완료' 보류 명시"
        " 있었는데 시장 분석가의 sector primer / 인트로에 'KOSPI 30D +15.94%"
        " 상승' 그대로 cite 한 케이스):\n"
        " instrument_context 의 거시 지표 블록에 ⛔ HARD GUARD (Fix E) 가"
        " 발화한 시리즈 (KOSPI / WTI / DXY / 환율 등 abs/30D 범위 outside)"
        " 는 본 분석의 거시 블록 외 모든 위치 — sector primer / 거시 배경"
        " 인트로 / 산업 분석 / 결론 / Comps 표 어디에도 — directional cite"
        " ('상승세' / '하락세' / '급등' / '강세' / '약세' / '+X%' / '-X%')"
        " 절대 금지. ⛔ HARD GUARD 가 발화한 시리즈는 본 분석의 거시 frame"
        " 에서 통째로 omit — 정상 시리즈 (⚠️ 미발화) 만 거시 frame 으로"
        " 사용. 시장 분석가가 ⛔ 시리즈 한 줄이라도 cite 하면 RULE 위반."
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
    the same fetch.

    Fix ① (2026-05-23, 외부 검증자 제언): yfinance .info의 currentPrice
    는 내부 캐시 lag 이 있을 수 있음. fast_info.last_price 는 별도의
    경량 API 엔드포인트를 통해 거의 실시간 가격을 제공 — currentPrice
    를 override 해 가격 데이터 freshness 보장. Rule applies to all
    analyses going forward (US/KR/JP/TW/CN/HK).
    """
    if ticker in _INSTRUMENT_INFO_CACHE:
        return _INSTRUMENT_INFO_CACHE[ticker]
    out: dict = {}
    try:
        import yfinance as yf
        yf_ticker = yf.Ticker(ticker)
        raw = yf_ticker.info or {}
        # Strings — keep only when non-empty
        for key in ("quoteType", "typeDisp", "sector", "industry", "longName",
                    "recommendationKey", "currency", "financialCurrency"):
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
                    "earningsTimestampEnd",
                    "trailingPE", "forwardPE", "priceToBook",
                    "priceToSalesTrailing12Months", "enterpriseToEbitda",
                    "trailingEps", "bookValue", "sharesOutstanding",
                    "marketCap", "beta",
                    "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                    "fiftyDayAverage", "twoHundredDayAverage"):
            v = raw.get(key)
            if isinstance(v, (int, float)) and not (isinstance(v, float) and v != v):
                out[key] = v
        # Fix ① — fast_info fallback for price + structural fields.
        # fast_info hits a lightweight quote endpoint updated more frequently
        # than .info (which has cache lag). Also fills in marketCap /
        # fiftyTwoWeekHigh / fiftyTwoWeekLow when .info returns None for
        # mid-cap US tickers like FORM (FormFactor 2026-05-23 N/A case).
        # Rule applies to all analyses going forward (US/KR/JP/TW/CN/HK).
        try:
            fi = yf_ticker.fast_info
            # currentPrice — always prefer fast_info (fresher)
            last_px = fi.last_price
            if isinstance(last_px, (int, float)) and last_px == last_px and last_px > 0:
                out["currentPrice"] = last_px
                out["regularMarketPrice"] = last_px
                _analyst_log.debug(
                    "fast_info.last_price override for %s: %.4f", ticker, last_px,
                )
            # marketCap fallback — fast_info.market_cap when .info misses it
            if "marketCap" not in out:
                fi_mc = getattr(fi, "market_cap", None)
                if isinstance(fi_mc, (int, float)) and fi_mc == fi_mc and fi_mc > 0:
                    out["marketCap"] = fi_mc
                    _analyst_log.debug("fast_info.market_cap fallback for %s: %g", ticker, fi_mc)
            # 52-week high/low fallback
            if "fiftyTwoWeekHigh" not in out:
                fi_yh = getattr(fi, "year_high", None)
                if isinstance(fi_yh, (int, float)) and fi_yh == fi_yh and fi_yh > 0:
                    out["fiftyTwoWeekHigh"] = fi_yh
            if "fiftyTwoWeekLow" not in out:
                fi_yl = getattr(fi, "year_low", None)
                if isinstance(fi_yl, (int, float)) and fi_yl == fi_yl and fi_yl > 0:
                    out["fiftyTwoWeekLow"] = fi_yl
            # sharesOutstanding fallback — needed for market cap cross-check
            if "sharesOutstanding" not in out:
                fi_sh = getattr(fi, "shares", None)
                if isinstance(fi_sh, (int, float)) and fi_sh == fi_sh and fi_sh > 0:
                    out["sharesOutstanding"] = fi_sh
        except Exception:
            pass  # fast_info unavailable — fall through to .info values
    except Exception as exc:
        _analyst_log.warning("instrument info lookup failed for %s: %s", ticker, exc)

    # GBp (London pence) → GBP normalization. yfinance 는 LSE 종목의
    # currentPrice / fiftyDayAverage / fiftyTwoWeek* / bookValue /
    # marketCap 을 펜스 단위로, trailingEps 등 일부 필드를 파운드
    # 단위로 혼재 반환 → 하위 cross-anchor check (PER×EPS≈px Fix A,
    # PBR×BPS≈px Fix B, shares×px≈mc Fix C, forwardEps/trailingEps
    # Fix H) 가 100x 스케일 mismatch 로 false 'corp action 의심' 발화.
    # 2026-05-29 Metals & Mining 도메인 review (WEIR.L) surfaced. 단일
    # 지점 normalization 으로 다운스트림 _build_factual_anchor / 멀티플
    # block / tier 분류 / SMA divergence 모두 자동 혜택. Universal —
    # 모든 LSE 종목 + 향후 GBp 단위 거래소 (남아공 ZAc 등 동일 패턴)
    # 확장 가능. yfinance 가 currency='GBp' 외에 'GBX' 도 간헐 사용.
    # Rule applies to all analyses going forward.
    # Detection: yfinance 가 currency='GBp' / 'GBX' 로 명시 반환하는 케이스
    # (primary) + 라벨이 'GBP' 인데 prices 가 pence 단위인 inconsistent
    # 케이스 (heuristic fallback). 후자는 2026-05-29 Space Launch review
    # surfaced — BA.L (LSE) cross-anchor fire 가 4254da0 commit 후에도
    # 발생. 가설: yfinance 가 currency='GBP' (대문자) 로 반환하면서 prices
    # 는 pence — 라벨 기반 detection 만으로는 못 잡음.
    raw_currency = (out.get("currency") or "").strip()
    needs_norm = raw_currency in ("GBp", "GBX")
    is_heuristic = False
    if not needs_norm:
        # Heuristic — .L (LSE) suffix + currentPrice > 1000 단위 패턴 =
        # pence 단위 강한 의심. £1,000+ 본주는 GBP 종목 중 극히 드물고
        # (Berkshire-style price 거의 없음), 일반 LSE blue-chip 은 £5-£60
        # 범위. 1000+ 면 pence (£10+ 환산) 가 압도적으로 자연스러움.
        if (ticker or "").upper().endswith(".L"):
            px = out.get("currentPrice") or out.get("regularMarketPrice")
            if isinstance(px, (int, float)) and px > 1000:
                needs_norm = True
                is_heuristic = True
    if needs_norm:
        for _k in ("currentPrice", "regularMarketPrice",
                   "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
                   "fiftyDayAverage", "twoHundredDayAverage",
                   "bookValue", "targetMeanPrice",
                   "targetHighPrice", "targetLowPrice",
                   "trailingEps", "marketCap"):
            v = out.get(_k)
            if isinstance(v, (int, float)) and v == v:
                out[_k] = v / 100.0
        out["currency"] = "GBP"
        if is_heuristic:
            out["_gbp_normalized_heuristic"] = True
            _analyst_log.warning(
                "GBp heuristic normalization applied for %s (currency label was %r, "
                "px > 1000) — yfinance label-price inconsistency suspected",
                ticker, raw_currency or "(empty)",
            )
        else:
            out["_gbp_normalized"] = True
            _analyst_log.info("GBp→GBP normalization applied for %s", ticker)

    # 2026-05-29 EV screener review fix: yfinance 가 CN A-share 의
    # trailingPE / priceToBook / priceToSalesTrailing12Months 를 자주
    # None 으로 던짐 (300037.SZ / 002812.SZ / 688275.SS 등 EV 도메인
    # 11종 중 3종이 모두 None). 마지막 단계에서 AKShare
    # 'stock_a_indicator_lg' (PE/PB/PS daily 시계열) 의 latest row 로
    # 빈 슬롯 overlay. KR 의 Naver/KIS 폴백과 동일 의도, 단 _instrument_
    # info 한 곳에서 처리하므로 _build_factual_anchor / canonical price
    # block / _assemble_multiples_block 모두 자동 혜택. Universal —
    # 시장 == CN_A 일 때만 작동, 다른 시장은 no-op.
    try:
        from bot.market import detect_market
        if detect_market(ticker) == "CN_A" and not all(
            isinstance(out.get(k), (int, float)) and out.get(k) == out.get(k)
            for k in ("trailingPE", "priceToBook")
        ):
            try:
                from bot.akshare_client import get_akshare
                # F5 (2026-05-29 audit): bound this inline AKShare call —
                # no internal socket timeout, runs outside the prefetch
                # executor's 15/30s guard.
                cn_val = _call_with_timeout(
                    lambda: get_akshare().get_valuation(ticker),
                    15, f"akshare get_valuation {ticker}",
                )
                if cn_val:
                    if cn_val.get("per") and "trailingPE" not in out:
                        out["trailingPE"] = float(cn_val["per"])
                    if cn_val.get("pbr") and "priceToBook" not in out:
                        out["priceToBook"] = float(cn_val["pbr"])
                    if cn_val.get("psr") and "priceToSalesTrailing12Months" not in out:
                        out["priceToSalesTrailing12Months"] = float(cn_val["psr"])
                    _analyst_log.info(
                        "akshare CN_A multiples overlay for %s: PER=%s PBR=%s PSR=%s",
                        ticker,
                        cn_val.get("per"), cn_val.get("pbr"), cn_val.get("psr"),
                    )
            except Exception as exc:
                _analyst_log.debug(
                    "akshare CN_A overlay failed for %s: %s", ticker, exc,
                )
    except Exception:
        pass

    _INSTRUMENT_INFO_CACHE[ticker] = out
    return out


def _quote_type(ticker: str) -> str | None:
    info = _instrument_info(ticker)
    qt = info.get("quoteType") or info.get("typeDisp")
    return qt.upper() if isinstance(qt, str) else None


_PEER_MULTIPLES_CACHE: dict[str, str] = {}

# F1 (2026-05-29 audit): build_instrument_context is called up to 8× per
# analysis (cache-seed + 4 analysts + RM/Trader/PM) and re-entered on every
# analyst tool round, each call re-running _prefetch_market_io's ~20-task
# thread-pool fan-out. The output is deterministic for a given
# (ticker, analyst_id) within one analysis, so memoize. Key includes the
# KST date as a backstop for callers that don't clear between runs (the
# screener's Phase-3 parallel fetch — desirable there, matching its 24h
# cache). The NOAH /ticker path calls clear_instrument_caches() at run
# start (trading_graph._run_graph) so each analysis sees fresh intraday
# data (also closes the F7 intraday-staleness gap on the two caches below).
_INSTRUMENT_CONTEXT_CACHE: dict[tuple, str] = {}


def clear_instrument_caches() -> None:
    """Drop the per-run instrument caches so the next analysis re-fetches
    fresh data. Called at analysis start. Clears the context memo plus the
    two long-lived .info / peer-multiple caches (previously bare-ticker
    keyed and never cleared → an AAPL analysis at 16:00 reused the 09:00
    price; F7). Screener does NOT call this — its Phase-3 fetch benefits
    from intra-run reuse and it has a separate 24h result cache."""
    _INSTRUMENT_CONTEXT_CACHE.clear()
    _INSTRUMENT_INFO_CACHE.clear()
    _PEER_MULTIPLES_CACHE.clear()


def _fetch_peer_multiples(ticker: str) -> str:
    """Pull headline valuation multiples + canonical company name from
    yfinance .info for a peer ticker. Returns 'Company Name | PER 18.4 /
    PSR 1.8 / ...' or 'Company Name | (multiples 미수집)' when only the
    name is available, or '' when both are missing.

    Why include the company name: TW peer review 2026-05-18 surfaced
    that analysts independently fabricated peer company names when the
    PEER SET block only injected bare tickers. e.g. 2379.TW (actually
    Realtek Semiconductor) was labeled 'ASE Tech' / 'Advanced
    Semiconductor Engineering' by sentiment / news analysts. 3034.TW
    (actually Novatek Microelectronics) labeled 'GlobalWafers' / 'WIN
    Semiconductors'. 8299.TWO (actually Phison Electronics) labeled
    'GUC' / 'CHIPBOND'. Injecting the yfinance longName eliminates the
    hallucination class — analysts copy the name verbatim.

    A dedicated cache + targeted fetch keeps this independent of the
    main _instrument_info cache so other callers' output isn't affected.
    """
    if ticker in _PEER_MULTIPLES_CACHE:
        return _PEER_MULTIPLES_CACHE[ticker]
    out_parts: list[str] = []
    company_name = ""
    # KR peer values — Fix E (2026-05-23, 207940.KS 외부 검증 surface):
    # yfinance 가 셀트리온 068270.KS / SK바이오팜 326030.KS 등 KR mid-cap
    # peer 의 trailingPE / priceToBook / EV-EBITDA 를 N/A 로 던지는 패턴.
    # Naver Finance + KIS 로 보강해 Comps 표 빈 칸 메움. KR ticker 만
    # 적용 — US/JP/TW/CN 은 yfinance peer multiples 가 일반적으로 양호.
    kr_per = kr_pbr = kr_eps = kr_bps = None
    # CN A-share fallback values (set when market == CN_A and yfinance
    # multiples miss). Mirror of the KR fallback shape — same per/pbr/psr
    # naming so the resolution chain below is symmetric.
    cn_per = cn_pbr = cn_psr = None
    try:
        from bot.market import detect_market
        _mkt = detect_market(ticker)
        if _mkt == "KR":
            try:
                from bot.naver_finance_client import get_naver_valuation
                nav_peer = get_naver_valuation(ticker)
                if nav_peer:
                    kr_per = nav_peer.get("per")
                    kr_pbr = nav_peer.get("pbr")
                    kr_eps = nav_peer.get("eps")
                    kr_bps = nav_peer.get("bps")
            except Exception:
                pass
            # KIS PER/PBR as secondary KR peer source (Naver 가 비어있는
            # 일부 mid-cap 케이스)
            if not (kr_per or kr_pbr):
                try:
                    from bot.kis_client import get_kis
                    kis_peer = get_kis().get_price(ticker)
                    if kis_peer:
                        if not kr_per and kis_peer.get("per") and kis_peer["per"] > 0:
                            kr_per = kis_peer["per"]
                        if not kr_pbr and kis_peer.get("pbr") and kis_peer["pbr"] > 0:
                            kr_pbr = kis_peer["pbr"]
                except Exception:
                    pass
        elif _mkt == "CN_A":
            # 2026-05-29 EV screener review fix: yfinance 가 CN A-share
            # PER/PBR/PSR 를 모두 None 으로 던지는 케이스 (300037.SZ /
            # 002812.SZ / 688275.SS) 가 잦음. AKShare 'stock_a_indicator_
            # _lg' (daily PE/PB/PS 시계열) 의 가장 최근 행으로 폴백 →
            # KR 의 Naver/KIS 폴백과 동일 패턴.
            try:
                from bot.akshare_client import get_akshare
                # F5 (2026-05-29 audit): bound this inline AKShare call —
                # no internal socket timeout, runs outside the prefetch
                # executor's 15/30s guard.
                cn_val = _call_with_timeout(
                    lambda: get_akshare().get_valuation(ticker),
                    15, f"akshare get_valuation {ticker}",
                )
                if cn_val:
                    cn_per = cn_val.get("per")
                    cn_pbr = cn_val.get("pbr")
                    cn_psr = cn_val.get("psr")
            except Exception:
                pass
    except Exception:
        pass

    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).info or {}
        # Canonical company name first — used as the row label so
        # analysts can't substitute their own guess.
        nm = raw.get("longName") or raw.get("shortName") or ""
        if isinstance(nm, str) and nm.strip():
            company_name = nm.strip()

        # Fix Q (2026-05-24, 현대모비스 012330.KS 외부 검증 surface):
        # peer multiple sanity range 강화 — DENSO Corporation 케이스에서
        # PSR 0.00 / EV/EBITDA -0.1 같은 명백한 데이터 오류가 노출.
        # 범위: PER 0~500, PBR -50~100, PSR 0.05~50, EV/EBITDA -500~500.
        # PSR < 0.05 는 정수 truncation 후 0.00 으로 표시되는 stale 데이터
        # 의심. PBR negative 는 자본잠식 (legitimate) 으로 허용. Universal.
        def _valid(v, lo: float, hi: float) -> bool:
            return (isinstance(v, (int, float))
                    and not (isinstance(v, float) and v != v)
                    and lo < v < hi)

        # PER (trailing) — KR fallback first if yfinance miss, then CN
        # AKShare fallback. 적자 (eps<0) 케이스는 별도 "N/M(적자)" 로 명시.
        per = raw.get("trailingPE")
        eps_for_per = raw.get("trailingEps")
        if not _valid(per, 0, 500):
            if kr_per and 0 < kr_per < 500:
                per = kr_per
            elif cn_per and 0 < cn_per < 500:
                per = cn_per
            else:
                per = None
        if _valid(per, 0, 500):
            out_parts.append(f"PER {per:.1f}")
        elif isinstance(eps_for_per, (int, float)) and eps_for_per == eps_for_per and eps_for_per < 0:
            # Fix R (2026-05-24): 적자 기업의 PER N/A → "N/M(적자)" substitute.
            # LLM 이 "데이터 없음" 으로 오해하지 않고 "적자라 PER 산출 불가"
            # 로 정성 분석 가능하게 함. Universal — 모든 시장 공통.
            out_parts.append("PER N/M(적자)")
        # Forward PER
        fper = raw.get("forwardPE")
        if _valid(fper, 0, 500):
            out_parts.append(f"Fwd PER {fper:.1f}")
        # PSR — 0.05 미만은 stale/truncated 의심 (실제 0 인 회사 없음).
        # CN AKShare PSR 폴백 — yfinance priceToSalesTrailing12Months 가
        # CN A-share 에서 자주 None.
        psr = raw.get("priceToSalesTrailing12Months")
        if not _valid(psr, 0.05, 50):
            if cn_psr and 0.05 < cn_psr < 50:
                psr = cn_psr
            else:
                psr = None
        if _valid(psr, 0.05, 50):
            out_parts.append(f"PSR {psr:.2f}")
        # PBR — KR fallback first, then CN AKShare. -50~100 (자본잠식 시
        # negative 허용)
        pbr = raw.get("priceToBook")
        if not _valid(pbr, -50, 100):
            if kr_pbr and -50 < kr_pbr < 100:
                pbr = kr_pbr
            elif cn_pbr and -50 < cn_pbr < 100:
                pbr = cn_pbr
            else:
                pbr = None
        if _valid(pbr, -50, 100):
            out_parts.append(f"PBR {pbr:.2f}")
        # EV/EBITDA — -500~500 (적자 EBITDA legitimate negative 허용)
        evebitda = raw.get("enterpriseToEbitda")
        if _valid(evebitda, -500, 500):
            out_parts.append(f"EV/EBITDA {evebitda:.1f}")
    except Exception as exc:
        _analyst_log.warning("peer multiples fetch failed for %s: %s", ticker, exc)
    multiples_str = " / ".join(out_parts) if out_parts else "(multiples 미수집)"
    if company_name:
        out = f"{company_name} | {multiples_str}"
    else:
        out = multiples_str if out_parts else ""
    _PEER_MULTIPLES_CACHE[ticker] = out
    return out


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

    # JP fallback: yfinance .news is English-centric and frequently 0
    # for `.T` tickers. Kabutan aggregates JP-language news per ticker.
    # Mirrors the KR / Naver path above.
    if not has:
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "JP":
                from bot.kabutan_news import has_recent_japanese_news
                if has_recent_japanese_news(ticker):
                    has = True
        except Exception as exc:
            _analyst_log.warning(
                "news availability (kabutan) check failed for %s: %s", ticker, exc,
            )

    # TW fallback: 鉅亨網 (Anue) scrape provides 繁體中文 news for .TW /
    # .TWO tickers. Same pattern as KR Naver / JP Kabutan. Module ships
    # in Phase 4-TW-B (next commit) — until then the import will fail
    # silently and TW news fallback returns False, falling through to
    # the analyzer's news-skip path which now correctly says 'yfinance
    # + 鉅亨網 양쪽 모두 0건'.
    if not has:
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "TW":
                from bot.cnyes_client import has_recent_taiwanese_news
                if has_recent_taiwanese_news(ticker):
                    has = True
        except Exception as exc:
            _analyst_log.warning(
                "news availability (cnyes) check failed for %s: %s", ticker, exc,
            )

    # CN_A / HK fallback to AKShare Eastmoney news. Same shape as the
    # TW cnyes / JP Kabutan paths above. AKShare may not be installed
    # on the bot host (~200MB dep, lazy install); import failure
    # silently degrades to False and the analyzer's news-skip path
    # handles it correctly.
    if not has:
        try:
            from bot.market import detect_market
            if detect_market(ticker) in ("CN_A", "HK"):
                from bot.akshare_client import has_recent_chinese_news
                if has_recent_chinese_news(ticker):
                    has = True
        except Exception as exc:
            _analyst_log.warning(
                "news availability (akshare) check failed for %s: %s", ticker, exc,
            )

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

    # A3 (Step 2A ⑥, 2026-05-19): 한경 컨센서스 fallback for mid-cap
    # KOSDAQ that FnGuide 도 누락하는 영역. 3 단계 fallback chain:
    # yfinance → FnGuide → 한경. yfinance + FnGuide 둘 다 target 또는
    # n_analysts 가 비어있을 때만 한경 scrape.
    if market == "KR" and not (target and n_analysts):
        try:
            from bot.hk_consensus_client import fetch_consensus as fetch_hk_consensus
            hk_data = fetch_hk_consensus(ticker)
            if hk_data:
                target = target or hk_data.get("target_price")
                n_analysts = n_analysts or hk_data.get("analyst_count")
                hk_rating = hk_data.get("rating")
                if hk_rating and not rec_key:
                    rec_key = hk_rating  # 이미 buy/sell/hold direction
        except Exception as exc:
            _analyst_log.warning(
                "hk_consensus fetch failed for %s: %s", ticker, exc,
            )

    # Kabutan consensus scrape — JP equivalent of the FnGuide block.
    # Same two-purpose use: fallback for mid/small-caps yfinance missed,
    # and last_report_date for staleness detection. Fetched
    # unconditionally for JP so the date check works even when yfinance
    # already has the consensus block.
    kabu_data: dict | None = None
    if market == "JP":
        try:
            from bot.kabutan_consensus import fetch_consensus as fetch_jp_consensus
            kabu_data = fetch_jp_consensus(ticker)
        except Exception as exc:
            _analyst_log.warning(
                "kabutan consensus fetch failed for %s: %s", ticker, exc,
            )

    if market == "JP" and not target and kabu_data:
        target = target or kabu_data.get("target_mean")
        n_analysts = n_analysts or kabu_data.get("n_analysts")
        kabu_rating = kabu_data.get("rating")
        if kabu_rating and not rec_key:
            rec_key = {
                "매수": "buy", "보유": "hold", "매도": "sell",
            }.get(kabu_rating, "")

    # 鉅亨網 consensus scrape — TW equivalent of the FnGuide (KR) and
    # Kabutan (JP) blocks. Same two-purpose use: fallback for mid/small-
    # cap TW names yfinance misses, and last_report_date for staleness.
    cnyes_data: dict | None = None
    if market == "TW":
        try:
            from bot.cnyes_consensus import fetch_consensus as fetch_tw_consensus
            cnyes_data = fetch_tw_consensus(ticker)
        except Exception as exc:
            _analyst_log.warning(
                "cnyes_consensus fetch failed for %s: %s", ticker, exc,
            )

    if market == "TW" and not target and cnyes_data:
        target = target or cnyes_data.get("target_mean")
        n_analysts = n_analysts or cnyes_data.get("n_analysts")
        cnyes_rating = cnyes_data.get("rating")
        if cnyes_rating and not rec_key:
            rec_key = {
                "매수": "buy", "보유": "hold", "매도": "sell",
            }.get(cnyes_rating, "")

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
        # Fix P (2026-05-24, 현대모비스 012330.KS 외부 검증 surface):
        # 더 강력한 staleness 신호 — target < current (any upside < 0)
        # AND 등급이 buy/strong_buy (rec_mean ≤ 2.0 또는 rec_key in
        # buy/strong_buy). 통상 목표가가 현재가보다 낮으면 등급도 hold/
        # sell 이어야 정합 — 강매수 등급은 가격 급등을 컨센서스가
        # 따라잡지 못한 stale 신호. 현대모비스 -11.6% (>-20% 기존
        # threshold 미달) 인데 강매수 등급 잡음. Universal — US/KR/JP/
        # TW/CN/HK 공통.
        _is_buy_rating = (
            (isinstance(rec_mean, (int, float)) and rec_mean == rec_mean
                and 0 < rec_mean <= 2.0)
            or rec_key in ("strong_buy", "buy")
        )
        if upside < 0 and _is_buy_rating:
            lines.append(
                f"  ⛔ STALE 컨센서스 (HARD): 목표가가 현재가보다"
                f" {-upside:.1f}% 낮은데 등급은 강매수/매수. 가격 급등을"
                f" 애널리스트들이 아직 따라잡지 못한 stale 신호. 5거래일"
                f" horizon dominant variable 로 컨센서스 사용 금지 — 단순"
                f" reference 로만 cite. 본문의 개별 상향 뉴스로 catch-up"
                f" 여부 확인 후 판단."
            )
        elif upside <= -20:
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

        # Concrete staleness signal — actual date of the last analyst
        # report from FnGuide (KR) / Kabutan (JP). The gap-based
        # heuristics above are indirect; this is the literal date.
        # > 30 days = mean target is genuinely lagging individual
        # revisions.
        _staleness_data = fn_data if market == "KR" else (kabu_data if market == "JP" else None)
        _staleness_source = "FnGuide" if market == "KR" else "Kabutan"
        if _staleness_data and _staleness_data.get("last_report_date"):
            from datetime import datetime as _dt, date as _date
            try:
                last_dt = _dt.strptime(_staleness_data["last_report_date"], "%Y-%m-%d").date()
                days_old = (_date.today() - last_dt).days
                if days_old > 30:
                    lines.append(
                        f"  ⚠️ {_staleness_source} 최근 리포트 갱신일 {last_dt.isoformat()}"
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


# Keywords that flag an in-flight corporate action — i.e. a price-affecting
# event whose ex-date is imminent or just passed, where yfinance's
# historical price / SMA / MACD / RSI series are likely to be in a
# mixed adjusted / unadjusted state. Detection in build_instrument_context
# turns into a HARD GUARD telling every analyst NOT to interpret
# MA-based technicals (Rule A).
_KR_CORP_ACTION_KEYWORDS = (
    "무상증자", "주식분할", "액면분할",   # share count up → price down
    "주식병합", "감자",                   # share count down → price up
    "무상감자", "유상감자",
    "자기주식 소각", "주식 소각",
)
_JP_CORP_ACTION_KEYWORDS = (
    "株式分割", "株式無償割当",            # share count up
    "株式併合",                            # share count down
    "自己株式の消却",
)
_TW_CORP_ACTION_KEYWORDS = (
    "減資",                                # capital reduction (price up, common in TW)
    "增資", "增加资本",                    # capital increase (rare price-down)
    "無償配股",                            # bonus shares / stock dividend (price down)
    "股票分割", "股票分拆",                # stock split (price down, rare in TW)
    "股票合併", "股票反向分割",            # reverse split (price up, rare)
    "減資彌補虧損",                        # loss-offset capital reduction
    "庫藏股", "庫藏股註銷",                # treasury buyback / cancellation (price impact varies)
)


def _detect_kr_corp_action(disclosures: list[dict]) -> dict | None:
    """Scan DART recent disclosures (last 30 days from upstream call) for
    a price-affecting corporate action. Returns {date, event} or None.

    The earlier the event date, the smaller the staleness window — but
    the historical series can stay corrupted for up to two weeks after
    ex-date, so we surface ANY hit from the last 30 days. The caller
    uses this to inject the HARD GUARD directive."""
    if not disclosures:
        return None
    for d in disclosures:
        title = (d.get("title") or "")
        for kw in _KR_CORP_ACTION_KEYWORDS:
            if kw in title:
                raw_date = (d.get("date") or "")
                if len(raw_date) == 8 and raw_date.isdigit():
                    raw_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                return {"date": raw_date, "event": title.strip()}
    return None


def _detect_yf_corp_action(ticker: str, lookback_days: int = 14) -> dict | None:
    """Universal corporate-action detector via yfinance .splits series.

    Catches the EX-DATE event for any market (US/KR/JP/CN), complementing
    the DART/EDINET announcement scans which only fire for KR/JP. For US
    tickers this is the ONLY corp-action signal we have; for KR/JP it
    acts as a safety net if the regulatory-filing scan missed something
    (DART/EDINET key absent, filing keyword variant, etc.).

    Returns {date, event} when a split happened in the last `lookback_days`
    calendar days, else None. Network-dependent — caught at call site.
    """
    try:
        import yfinance as yf
        import pandas as pd
        splits = yf.Ticker(ticker).splits
        if splits is None or len(splits) == 0:
            return None
        # splits index is tz-aware; use the same tz for the cutoff to
        # avoid 'cannot compare tz-naive and tz-aware' on some pandas
        # versions. UTC is fine — the lookback window is in days.
        tz = splits.index.tz
        cutoff = pd.Timestamp.now(tz=tz) - pd.Timedelta(days=lookback_days)
        recent = splits[splits.index >= cutoff]
        if recent.empty:
            return None
        last_date = recent.index[-1]
        factor = float(recent.iloc[-1])
        if factor == 1.0:
            return None
        # Render direction so the analyst can tell forward (TSLA 3:1) from
        # reverse (low-priced stocks); factor > 1 = forward, < 1 = reverse.
        if factor > 1:
            label = f"{factor:g}:1 forward split"
        else:
            label = f"1:{(1/factor):g} reverse split"
        return {
            "date": last_date.strftime("%Y-%m-%d"),
            "event": f"{label} (yfinance ex-date)",
        }
    except Exception:
        return None


# FSC 권리일정(getRighExerReasSche) rcdNm 중 가격영향 corp action 사유.
# 정기 "기준일 / 명부폐쇄 / 배당 / 총회 / 기타" 는 제외 (기술지표 무효화 X).
_FSC_RIGHTS_CORP_ACTION_KW = (
    "무상증자", "유상증자", "감자", "무상감자", "유상감자",
    "액면분할", "주식분할", "액면병합", "주식병합", "주식교환", "주식이전",
)


def _detect_fsc_corp_action(ticker: str, lookback_days: int = 14) -> dict | None:
    """금융위 FSC 권리일정(KSD) 기반 KR corp action 탐지 — DART scan 의
    백업/보강. rcdNm 이 증자/감자/분할/병합/교환 사유인 행만 필터하고 정기
    기준일·배당은 제외. crno 는 fsc item_info 로 매핑. {date, event, source}
    or None. 네트워크/키 실패 시 None (호출측 안전)."""
    try:
        from bot.fsc_client import rights_for
        rows = rights_for(ticker, lookback_days=lookback_days)
    except Exception:
        return None
    for r in rows or []:
        name = f"{r.get('rcdNm', '')} {r.get('rgtNm', '')}"
        if not any(kw in name for kw in _FSC_RIGHTS_CORP_ACTION_KW):
            continue
        raw = (r.get("rgtSttgDt") or r.get("basDt") or "").strip()
        date = (f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
                if len(raw) == 8 and raw.isdigit() else raw)
        ev = (r.get("rcdNm") or "").strip()
        if r.get("rgtNm"):
            ev = f"{ev} ({r['rgtNm'].strip()})".strip()
        return {"date": date, "event": ev or name.strip(),
                "source": "FSC 권리일정(KSD)"}
    return None


def _detect_jp_corp_action(disclosures: list[dict]) -> dict | None:
    """JP analogue. EDINET 臨時報告書 carry 株式分割 / 無償割当 / 株式併合
    headlines in their docDescription field."""
    if not disclosures:
        return None
    for d in disclosures:
        title = (d.get("description") or "") + " " + (d.get("doc_type_label") or "")
        for kw in _JP_CORP_ACTION_KEYWORDS:
            if kw in title:
                return {
                    "date": (d.get("date") or "").strip(),
                    "event": (d.get("description") or "").strip(),
                }
    return None


def _detect_tw_corp_action(disclosures: list[dict]) -> dict | None:
    """TW analogue. MOPS 重大訊息 rows carry 減資 / 無償配股 / 股票分割
    / 庫藏股 keywords in their 'subject' (主旨) free-text field. Same
    semantic as KR / JP scans but MOPS uses a different field name —
    the bot.mops_client returns 'subject' (not 'title') from 重大訊息.
    Universal handling: scan both for safety."""
    if not disclosures:
        return None
    for d in disclosures:
        title = (
            (d.get("subject") or "")
            + " "
            + (d.get("title") or "")
            + " "
            + (d.get("doc_type_label") or "")
        )
        for kw in _TW_CORP_ACTION_KEYWORDS:
            if kw in title:
                return {
                    "date": (d.get("date") or "").strip(),
                    "event": (d.get("subject") or d.get("title") or "").strip(),
                }
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
        # When all reported holdings round to ~0% we can't tell whether
        # this is a state-owned enterprise (civil-servant officers,
        # legitimately zero) OR a private company DART happened to
        # return holdings rows for that exclude the actual major
        # shareholders. The previous heuristic assumed the SOE case
        # and emitted "공기업 / 공공 entity — 정부 보유" prose, which
        # the fundamentals LLM then parroted into reports of
        # ordinary private firms (코미코 2026-05-17: "산업은행 / 정부
        # 등 주요 주주 기준으로 분석"). That's worse than admitting
        # the data is missing, because it fabricates a false
        # ownership narrative. Replace with neutral phrasing — the
        # build_instrument_context layer adds an explicit ANTI-
        # HALLUCINATION directive when the top list is all-zero so
        # the analyst doesn't backfill a story.
        if top:
            nonzero = [r for r in top if (r.get("pct") or 0) >= 0.01]
            if not nonzero:
                lines.append(
                    "- 임원·주요주주 지분: DART가 의미 있는 보유 (≥0.01%)를"
                    " 반환하지 않음 — 공기업 (한국전력공사 등) 의 공직자"
                    " 임원, 또는 일반 상장사인데 DART 임원지분 row가 비어"
                    " 있는 경우가 모두 가능. 회사 성격을 추측하지 말 것"
                    " (지배구조 narrative 금지)."
                )
            else:
                lines.append(f"- 임원·주요주주 지분 (상위 {len(top)}):")
                for r in top:
                    name = r.get("name") or "?"
                    role = (r.get("role") or "").strip()
                    pct = r.get("pct") or 0
                    role_part = f" ({role})" if role else ""
                    lines.append(f"  • {name}{role_part}: {pct:.2f}%")
    else:
        # Truly empty list (DART returned no rows at all, or the API
        # call failed silently). Same anti-hallucination rule applies.
        lines.append(
            "- 임원·주요주주 지분: DART 데이터 미수집 (API 빈 응답"
            " 또는 fetch 실패). 회사 성격을 추측하지 말 것 (지배구조"
            " narrative 금지)."
        )

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


# Per-analyst exclusion map for build_instrument_context (Option 1 cost
# reduction, 2026-05-18). Each analyst gets only the sections it actually
# uses; non-relevant heavy blocks (foreign-language news for market /
# fundamentals, KR flow data for sentiment, etc.) are dropped from the
# prompt to cut input tokens by ~25-30% per analyst. Default `None`
# analyst_id (used by PM / trader / research_manager) sees all sections
# unchanged — backward-compatible.
#
# Rule applies to all analyses going forward, covers US + KR + JP + TW
# (+ future CN). Universal-by-default: each gated section is the same
# block across markets, the gate just hides it from analysts that don't
# need it. No per-market branching introduced.
_ANALYST_CONTEXT_EXCLUDE: dict[str, set[str]] = {
    # 시장 (technical): doesn't analyze native-language news. Keeps KRX
    # flow (it IS market-flow data), keeps all macro blocks (rate
    # environment frames the chart). For CN/HK, HSGT 港股通 flow IS
    # market-flow data so it stays in the market analyst's set.
    "market": {
        "naver_news", "kabutan_news", "cnyes_news", "eastmoney_news",
        "edgar_form4",
        "rule1_skeleton", "cashflow_block", "balance_block", "ratios_block",
    },
    # 감정 (sentiment): doesn't quantify rates or KRX/HSGT flow. Keeps news
    # blocks (sentiment fuel) and peer set (Comps consistency).
    "social": {
        "krx_flow", "hsgt_flow", "kis_supply",
        "bok_macro", "fred_jp_macro", "fred_tw_macro", "akshare_macro",
        "edgar_8k", "edgar_form4",
        "options_signals",
        "rule1_skeleton", "cashflow_block", "balance_block", "ratios_block",
    },
    # 뉴스 (news): keeps everything except flow data (numbers without
    # narrative don't add to news synthesis).
    "news": {"krx_flow", "hsgt_flow", "kis_supply",
             "options_signals",
             "rule1_skeleton", "cashflow_block", "balance_block", "ratios_block"},
    # 펀더멘털 (fundamentals): doesn't read native-language news, doesn't
    # need short-horizon flow. Keeps macro (rate-sensitive valuation).
    # rule1_skeleton is NOT excluded — fundamentals analyst gets the table.
    "fundamentals": {
        "naver_news", "kabutan_news", "cnyes_news", "eastmoney_news",
        "krx_flow", "hsgt_flow", "kis_supply",
    },
}


def _prefetch_market_io(ticker: str, market: str) -> dict:
    """F3-light parallel I/O prefetch (2026-05-19).

    Fan out the heavy network fetches for `market` in parallel via
    ThreadPoolExecutor. Returns dict keyed by source tag — each downstream
    block in build_instrument_context pulls its data via dict lookup
    instead of inline fetch, eliminating sequential I/O wait.

    Latency win per market (sequential vs parallel max_workers=N):
     • KR: ~4-5s → ~2s (DART + pykrx flow + Naver + BoK macro)
     • JP: ~2-4s → ~1-2s (EDINET + Kabutan + FRED macro)
     • TW: ~2-3s → ~1s (MOPS + cnyes + FRED macro)
     • CN_A/HK: ~6-8s → ~3-4s (AKShare 4 calls + HSGT + Eastmoney + macro)

    Each fetch wrapped in try/except so a single failure (network /
    AttributeError / etc.) doesn't poison the dict — the tag just maps
    to None and the downstream block treats it like an empty result.

    For market == "US", prefetches EDGAR 8-K events + Form 4 insider
    trades in parallel (no API key required).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tasks: dict[str, callable] = {}

    if market == "KR":
        try:
            from bot.dart_client import get_dart
            dart = get_dart()
            tasks["dart_disclosures"] = lambda: dart.get_recent_disclosures(ticker, days_back=30, limit=8)
            tasks["dart_insiders"] = lambda: dart.get_insider_holdings(ticker)
            tasks["dart_window"] = lambda: dart.next_earnings_window(ticker)
        except Exception:
            pass
        try:
            from bot.pykrx_client import (
                get_kr_trading_flow,
                get_kr_foreign_ownership_trend,
                get_kr_short_balance_trend,
                get_kr_market_cap,
                get_kr_ohlcv_stats,
            )
            tasks["pykrx_flow"] = lambda: get_kr_trading_flow(ticker, days_back=5)
            tasks["pykrx_foreign_trend"] = lambda: get_kr_foreign_ownership_trend(ticker, days_back=30)
            tasks["pykrx_short_trend"] = lambda: get_kr_short_balance_trend(ticker, days_back=30)
            # D1: pykrx 시가총액 + 최신 close — yfinance .info marketCap
            # 및 currentPrice cross-check 용. canonical 시총 directive
            # 에서 사용.
            tasks["pykrx_market_cap"] = lambda: get_kr_market_cap(ticker)
            # D1 Phase 3 (307950.KS 2026-05-23 surfaced): 52주 최고/최저
            # + 50/200일 SMA pykrx fallback. yfinance .info 가 빈 자리를
            # 남기면 PM 이 fabrication 시도 (현대오토에버 PER 94.8 case).
            tasks["pykrx_ohlcv_stats"] = lambda: get_kr_ohlcv_stats(ticker)
        except Exception:
            pass
        try:
            from bot.bok_ecos_client import fetch_kr_macro
            tasks["bok_macro"] = lambda: fetch_kr_macro()
        except Exception:
            pass
        # D2 (Step 2A item ⑤): USD/KRW 30D % change — KR 수출주 / 수입주
        # FX sensitivity 자동 추정용. macro_context_tools._fetch_one 재활용
        # (yfinance internal cache).
        try:
            from tradingagents.agents.utils.macro_context_tools import _fetch_one
            from datetime import date as _date
            def _fetch_krw_30d():
                try:
                    _, pct = _fetch_one("KRW=X", _date.today().isoformat())
                    return pct
                except Exception:
                    return None
            tasks["krw_30d_pct"] = _fetch_krw_30d
        except Exception:
            pass
        # US overnight futures — S&P 500 (ES=F) + NASDAQ 100 (NQ=F).
        # KR 개장 전 미국 선물 방향성을 KR 분석 컨텍스트에 주입.
        # fast_info 재활용 (별도 HTTP 최소화).
        try:
            import yfinance as _yf_fut
            def _fetch_us_futures():
                result = {}
                for sym, label in [("ES=F", "S&P500 선물"), ("NQ=F", "NASDAQ100 선물")]:
                    try:
                        fi = _yf_fut.Ticker(sym).fast_info
                        cur = getattr(fi, "last_price", None)
                        prev = getattr(fi, "previous_close", None)
                        if cur and prev and prev != 0:
                            result[sym] = {
                                "label": label,
                                "price": round(cur, 2),
                                "pct": round((cur - prev) / prev * 100, 2),
                            }
                    except Exception:
                        pass
                return result or None
            tasks["us_futures"] = _fetch_us_futures
        except Exception:
            pass
        # A2: KRX 시장경보 status — 거래정지 / 관리종목 / 단기과열 /
        # 투자주의/경고/위험 4 카테고리 통합 lookup. fetch_all 가
        # process-wide cached (12h) 이라 first call 만 network — 이후
        # ticker check 는 in-memory set membership.
        try:
            from bot.krx_alert_client import get_krx_alert
            tasks["krx_alert"] = lambda: get_krx_alert().get_status(ticker)
        except Exception:
            pass
        # Step 2B A1: KIS 7종 수급 데이터 (현재가 / 외인+기관+개인 flow /
        # 기관 주체별 / 외인 한도소진율 / 신용+대차 / 프로그램 / 공매도).
        # KIS_APP_KEY / KIS_APP_SECRET 미설정 시 graceful skip.
        try:
            from bot.kis_client import get_kis
            _kis = get_kis()
            tasks["kis_price"]         = lambda: _kis.get_current_price(ticker)
            tasks["kis_investor_flow"] = lambda: _kis.get_investor_flow(ticker)
            tasks["kis_foreign_limit"] = lambda: _kis.get_foreign_limit(ticker)
            tasks["kis_credit_short"]  = lambda: _kis.get_credit_short_balance(ticker)
            tasks["kis_program_trade"] = lambda: _kis.get_program_trade(ticker)
            tasks["kis_short_sale"]    = lambda: _kis.get_short_sale(ticker)
        except Exception:
            pass
        # Naver news fetch needs KR corp name (kr_name from DART), which
        # is resolved inside build_instrument_context AFTER this prefetch.
        # Keep Naver as inline sequential fetch — only ~1s anyway.

    elif market == "JP":
        try:
            from bot.edinet_client import get_edinet
            edinet = get_edinet()
            tasks["edinet_disclosures"] = lambda: edinet.get_recent_disclosures(ticker, days_back=30, limit=8)
            tasks["edinet_holders"] = lambda: edinet.get_major_holders(ticker, days_back=180)
            tasks["edinet_window"] = lambda: edinet.next_earnings_window(ticker)
        except Exception:
            pass
        try:
            from bot.kabutan_news import fetch_news as fetch_jp_news
            tasks["kabutan_news"] = lambda: fetch_jp_news(ticker, days_back=28, max_items=10)
        except Exception:
            pass
        try:
            from bot.fred_client import fetch_macro
            tasks["fred_jp_macro"] = lambda: fetch_macro("JP")
        except Exception:
            pass

    elif market == "TW":
        try:
            from bot.mops_client import get_mops
            mops = get_mops()
            tasks["mops_disclosures"] = lambda: mops.get_recent_disclosures(ticker, days_back=30, limit=8)
            tasks["mops_insiders"] = lambda: mops.get_insider_holdings(ticker)
            tasks["mops_window"] = lambda: mops.next_earnings_window(ticker)
        except Exception:
            pass
        try:
            from bot.cnyes_client import fetch_news as fetch_tw_news
            tasks["cnyes_news"] = lambda: fetch_tw_news(ticker, days_back=28, max_items=10)
        except Exception:
            pass
        try:
            from bot.fred_client import fetch_macro
            tasks["fred_tw_macro"] = lambda: fetch_macro("TW")
        except Exception:
            pass

    elif market in ("CN_A", "HK"):
        try:
            from bot.akshare_client import get_akshare
            ak_client = get_akshare()
            tasks["akshare_disclosures"] = lambda: ak_client.get_recent_disclosures(ticker, days_back=30, limit=8)
            tasks["akshare_holders"] = lambda: ak_client.get_major_holders(ticker)
            tasks["akshare_window"] = lambda: ak_client.next_earnings_window(ticker)
            tasks["akshare_is_st"] = lambda: ak_client.is_st(ticker)
            tasks["akshare_is_suspended"] = lambda: ak_client.is_suspended(ticker)
            tasks["akshare_hsgt_flow"] = lambda: ak_client.get_hsgt_flow_summary(days_back=5)
            tasks["akshare_news"] = lambda: ak_client.fetch_news(ticker, days_back=28, max_items=10)
            tasks["akshare_macro"] = lambda: ak_client.fetch_cn_macro()
        except Exception:
            pass

    elif market == "US":
        try:
            from bot.edgar_client import get_recent_8k, get_recent_form4
            tasks["edgar_8k"] = lambda: get_recent_8k(ticker, days=30)
            tasks["edgar_form4"] = lambda: get_recent_form4(ticker, days=30)
        except Exception:
            pass
        try:
            from bot.options_client import get_options_signals
            tasks["options_signals"] = lambda: get_options_signals(ticker)
        except Exception:
            pass

    if not tasks:
        return {}

    results: dict = {}
    with ThreadPoolExecutor(
        max_workers=min(len(tasks), 10), thread_name_prefix="prefetch",
    ) as ex:
        future_to_tag = {ex.submit(fn): tag for tag, fn in tasks.items()}
        try:
            for future in as_completed(future_to_tag, timeout=30):
                tag = future_to_tag[future]
                try:
                    results[tag] = future.result(timeout=15)
                except Exception as exc:
                    _analyst_log.warning(
                        "prefetch %s failed for %s: %s", tag, ticker, exc,
                    )
                    results[tag] = None
        except Exception as exc:
            # Total timeout — return whatever was fetched. Downstream
            # blocks gracefully handle missing tags.
            _analyst_log.warning(
                "prefetch global timeout for %s (%s) — partial results",
                ticker, exc,
            )
    return results


# ── RULE 1 skeleton helpers ──────────────────────────────────────────────────

_RULE1_FIELDS: dict[str, str] = {
    "Total Revenue":    "매출",
    "Gross Profit":     "매출총이익",
    "Operating Income": "영업이익",
    "Net Income":       "순이익",
    "EBITDA":           "EBITDA",
}


def _fmt_native_val(v: float, currency: str, sym: str) -> str:
    """Format a raw financial value (absolute, in local currency) to the
    market's idiomatic short form (조/억 for KRW/JPY, $B/$M for USD, etc.)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if currency in ("KRW", "JPY"):
        unit_str = "조" if currency == "KRW" else "조"
        unit_small = "억"
        if abs(v) >= 1e12:
            return f"{sym}{v / 1e12:,.2f}{unit_str}"
        elif abs(v) >= 1e8:
            return f"{sym}{v / 1e8:,.0f}{unit_small}"
        else:
            return f"{sym}{v:,.0f}"
    elif currency == "TWD":
        if abs(v) >= 1e12:
            return f"NT${v / 1e12:,.2f}兆"
        elif abs(v) >= 1e8:
            return f"NT${v / 1e8:,.0f}億"
        else:
            return f"NT${v:,.2f}"
    elif currency in ("CNY",):
        if abs(v) >= 1e12:
            return f"¥{v / 1e12:,.2f}兆"
        elif abs(v) >= 1e8:
            return f"¥{v / 1e8:,.0f}亿"
        else:
            return f"¥{v:,.2f}"
    elif currency == "HKD":
        if abs(v) >= 1e9:
            return f"HK${v / 1e9:,.2f}B"
        elif abs(v) >= 1e6:
            return f"HK${v / 1e6:,.1f}M"
        else:
            return f"HK${v:,.2f}"
    else:  # USD / fallback
        if abs(v) >= 1e9:
            return f"${v / 1e9:,.2f}B"
        elif abs(v) >= 1e6:
            return f"${v / 1e6:,.0f}M"
        else:
            return f"${v:,.2f}"


def _fy_label(col_date) -> str:
    """Convert a yfinance income_stmt column (Timestamp/date) to a FY label.
    e.g. 2024-12-31 → 'FY24', 2025-03-31 → 'FY25'."""
    try:
        year = getattr(col_date, "year", None)
        if year is None:
            import datetime
            d = datetime.datetime.fromisoformat(str(col_date))
            year = d.year
        return f"FY{year % 100:02d}"
    except Exception:
        return str(col_date)[:7]


def _build_rule1_skeleton(
    ticker: str, info: dict, _cfg: dict, market: str
) -> str:
    """Fetch annual + quarterly income_stmt from yfinance and return a
    pre-formatted RULE 1 financial table skeleton for the fundamentals
    analyst. The analyst copies the skeleton verbatim and only adds
    growth rates / margins — never recomputes the base numbers.

    Returns '' on any failure (silent degradation).
    """
    try:
        import yfinance as yf
        obj = yf.Ticker(ticker.upper())
        annual = obj.income_stmt
        qtrly  = obj.quarterly_income_stmt
    except Exception:
        return ""

    try:
        if annual is None or (hasattr(annual, "empty") and annual.empty):
            return ""

        currency = _cfg.get("currency", "USD")
        sym      = _cfg.get("currency_symbol", "$")

        # ── TTM: sum of most-recent quarters ────────────────────────
        # RULE 8.1 guard: (1) label "Q{n}합 (불완전)" when <4 quarters
        # available; (2) >50x divergence vs annual → unit drop → OMIT.
        ttm: dict[str, float | None] = {}
        ttm_label = "TTM"
        if qtrly is not None and not (hasattr(qtrly, "empty") and qtrly.empty):
            n_qtrs = min(4, qtrly.shape[1])
            last4 = qtrly.iloc[:, :n_qtrs]
            if n_qtrs < 4:
                ttm_label = f"Q{n_qtrs}합 (불완전)"
            for field in _RULE1_FIELDS:
                if field not in last4.index:
                    continue
                vals = [float(v) for v in last4.loc[field]
                        if v is not None and v == v]
                raw_sum = sum(vals) if vals else None
                # RULE 8.1: cross-check vs most-recent annual value
                if (raw_sum is not None
                        and field in annual.index
                        and not annual.empty):
                    try:
                        ann_col = annual.columns[0]
                        ann_v = annual.loc[field, ann_col]
                        if ann_v is not None and ann_v == ann_v:
                            ann_f = float(ann_v)
                            if ann_f != 0:
                                ratio = abs(raw_sum) / abs(ann_f)
                                if ratio > 50 or ratio < 0.02:
                                    raw_sum = None  # unit mismatch — OMIT
                    except Exception:
                        pass
                ttm[field] = raw_sum

        # ── Annual: up to 4 most-recent fiscal years ────────────────
        fy_cols = list(annual.columns[:4])

        # Verify we actually have data to show
        has_data = any(
            field in annual.index and any(
                (v is not None and v == v)
                for v in (annual.loc[field, col] for col in fy_cols
                          if col in annual.columns)
            )
            for field in _RULE1_FIELDS
        )
        if not has_data:
            return ""

        lines = [
            "=== PRE-COMPUTED RULE 1 TABLE (시스템 직접 산출 — 수치 절대 변경 금지) ===",
            "분석가 지시: 아래 수치를 RULE 1 요약표에 그대로 복사하고,"
            " YoY 성장률(%)·이익률만 추가. 수치 재계산·재호출 절대 금지.",
            "",
        ]
        for field, kr_label in _RULE1_FIELDS.items():
            parts: list[str] = []
            ttm_v = ttm.get(field)
            if ttm_v is not None:
                parts.append(f"{ttm_label} {_fmt_native_val(ttm_v, currency, sym)}")
            for col in fy_cols:
                if field in annual.index:
                    v = annual.loc[field, col]
                    if v is not None and v == v:
                        parts.append(f"{_fy_label(col)} {_fmt_native_val(float(v), currency, sym)}")
            if parts:
                lines.append(f"  {kr_label}: {' | '.join(parts)}")

        if len(lines) <= 3:
            return ""

        lines += [
            "",
            "위 값이 get_income_statement 도구 결과와 다른 경우:"
            " 위 시스템 산출값 우선. 도구 결과로 재계산 금지.",
        ]
        return "\n".join(lines)
    except Exception as exc:
        _analyst_log.warning("rule1_skeleton failed for %s: %s", ticker, exc)
        return ""


def _build_cashflow_block(ticker: str, info: dict, _cfg: dict, market: str) -> str:
    """Pre-computed cash flow table. FCF = OpCF + CapEx (CapEx is negative in yfinance).
    Returns '' on failure."""
    try:
        import yfinance as yf
        obj = yf.Ticker(ticker.upper())
        annual = obj.cashflow
    except Exception:
        return ""
    try:
        if annual is None or (hasattr(annual, "empty") and annual.empty):
            return ""
        currency = _cfg.get("currency", "USD")
        sym = _cfg.get("currency_symbol", "$")
        fy_cols = list(annual.columns[:4])

        def _get(field):
            if field in annual.index:
                return {col: annual.loc[field, col] for col in fy_cols
                        if col in annual.columns}
            return {}

        opcf = _get("Operating Cash Flow")
        capex = _get("Capital Expenditure")
        ivcf = _get("Investing Cash Flow")
        fncf = _get("Financing Cash Flow")

        if not any(opcf.values()):
            return ""

        lines = [
            "=== PRE-COMPUTED 현금흐름표 (시스템 직접 산출 — 수치 절대 변경 금지) ===",
            "분석가 지시: 아래 수치를 RULE 2 현금흐름 분석에 그대로 복사."
            " get_cashflow 재호출·수치 재계산 절대 금지.",
            "",
        ]

        def _row(kr_label, data_dict):
            parts = []
            for col in fy_cols:
                v = data_dict.get(col)
                if v is not None and v == v:
                    parts.append(f"{_fy_label(col)} {_fmt_native_val(float(v), currency, sym)}")
            if parts:
                lines.append(f"  {kr_label}: {' | '.join(parts)}")

        _row("영업현금흐름", opcf)
        _row("설비투자(CapEx)", capex)

        # FCF = OpCF + CapEx (CapEx stored as negative)
        fcf_data: dict = {}
        for col in fy_cols:
            ov = opcf.get(col)
            cv = capex.get(col)
            if (ov is not None and ov == ov and cv is not None and cv == cv):
                fcf_data[col] = float(ov) + float(cv)
        _row("잉여현금흐름(FCF)", fcf_data)
        _row("투자현금흐름", ivcf)
        _row("재무현금흐름", fncf)

        if len(lines) <= 3:
            return ""

        lines += ["", "위 값 우선. 도구 결과로 재계산 금지."]
        return "\n".join(lines)
    except Exception as exc:
        _analyst_log.warning("cashflow_block failed for %s: %s", ticker, exc)
        return ""


def _build_balance_block(ticker: str, info: dict, _cfg: dict, market: str) -> str:
    """Pre-computed balance sheet table. Returns '' on failure."""
    try:
        import yfinance as yf
        obj = yf.Ticker(ticker.upper())
        annual = obj.balance_sheet
    except Exception:
        return ""
    try:
        if annual is None or (hasattr(annual, "empty") and annual.empty):
            return ""
        currency = _cfg.get("currency", "USD")
        sym = _cfg.get("currency_symbol", "$")
        fy_cols = list(annual.columns[:3])

        def _get_first(candidates):
            for field in candidates:
                if field in annual.index:
                    return {col: annual.loc[field, col] for col in fy_cols
                            if col in annual.columns}
            return {}

        rows = [
            ("총자산",   _get_first(["Total Assets"])),
            ("총부채",   _get_first(["Total Liabilities Net Minority Interest",
                                      "Total Liabilities"])),
            ("자기자본", _get_first(["Common Stock Equity", "Stockholders Equity",
                                      "Total Equity Gross Minority Interest"])),
            ("유동자산", _get_first(["Current Assets"])),
            ("유동부채", _get_first(["Current Liabilities"])),
            ("총차입금", _get_first(["Total Debt"])),
        ]

        if not any(rows[0][1].values()):
            return ""

        lines = [
            "=== PRE-COMPUTED 재무상태표 (시스템 직접 산출 — 수치 절대 변경 금지) ===",
            "분석가 지시: 아래 수치를 RULE 3/4/6 부채·자본 분석에 그대로 복사."
            " get_balance_sheet 재호출·수치 재계산 절대 금지.",
            "",
        ]
        for kr_label, data_dict in rows:
            parts = []
            for col in fy_cols:
                v = data_dict.get(col)
                if v is not None and v == v:
                    parts.append(f"{_fy_label(col)} {_fmt_native_val(float(v), currency, sym)}")
            if parts:
                lines.append(f"  {kr_label}: {' | '.join(parts)}")

        if len(lines) <= 3:
            return ""

        lines += ["", "위 값 우선. 도구 결과로 재계산 금지."]
        return "\n".join(lines)
    except Exception as exc:
        _analyst_log.warning("balance_block failed for %s: %s", ticker, exc)
        return ""


def _build_ratios_block(ticker: str, info: dict, _cfg: dict, market: str) -> str:
    """Pre-compute profitability + leverage ratios Python-side so the LLM
    never has to do unit conversions or cross-table arithmetic.

    Ratios computed:
      영업이익률 = Operating Income / Total Revenue × 100
      순이익률   = Net Income / Total Revenue × 100
      ROE        = Net Income / Common Stock Equity × 100
      ROA        = Net Income / Total Assets × 100
      차입금비율 = Total Debt / Common Stock Equity × 100   (US-style)
      총부채비율 = Total Liabilities / Common Stock Equity × 100  (KR standard)
      유동비율   = Current Assets / Current Liabilities × 100
    """
    try:
        import yfinance as yf
        obj = yf.Ticker(ticker.upper())
        inc = obj.income_stmt
        bal = obj.balance_sheet
        qi  = obj.quarterly_income_stmt
    except Exception:
        return ""
    try:
        if inc is None or (hasattr(inc, "empty") and inc.empty):
            return ""

        fy_cols = list(inc.columns[:4])

        def _inc(field):
            if field in inc.index:
                return {col: inc.loc[field, col] for col in fy_cols
                        if col in inc.columns and
                        inc.loc[field, col] is not None and
                        inc.loc[field, col] == inc.loc[field, col]}
            return {}

        def _bal(candidates):
            if bal is None or (hasattr(bal, "empty") and bal.empty):
                return {}
            for field in candidates:
                if field in bal.index:
                    return {col: bal.loc[field, col] for col in bal.columns[:3]
                            if bal.loc[field, col] is not None and
                            bal.loc[field, col] == bal.loc[field, col]}
            return {}

        rev = _inc("Total Revenue")
        oi  = _inc("Operating Income")
        ni  = _inc("Net Income")
        equity = _bal(["Common Stock Equity", "Stockholders Equity",
                        "Total Equity Gross Minority Interest"])
        assets = _bal(["Total Assets"])
        curr_a = _bal(["Current Assets"])
        curr_l    = _bal(["Current Liabilities"])
        debt      = _bal(["Total Debt"])
        tot_liab  = _bal(["Total Liabilities Net Minority Interest",
                          "Total Liabilities"])

        # TTM income from last 4 quarters
        ttm_rev = ttm_oi = ttm_ni = None
        if qi is not None and not (hasattr(qi, "empty") and qi.empty):
            last4 = qi.iloc[:, :4]
            def _ttm(field):
                if field in last4.index:
                    vals = [float(v) for v in last4.loc[field]
                            if v is not None and v == v]
                    return sum(vals) if vals else None
                return None
            ttm_rev = _ttm("Total Revenue")
            ttm_oi  = _ttm("Operating Income")
            ttm_ni  = _ttm("Net Income")

        def _pct(num, den):
            if (isinstance(num, (int, float)) and isinstance(den, (int, float))
                    and den and den == den and num == num):
                r = num / den * 100
                return f"{r:.1f}%" if abs(r) < 10000 else None
            return None

        opm_parts: list[str] = []
        npm_parts: list[str] = []
        roe_parts: list[str] = []
        roa_parts: list[str] = []

        # TTM row first
        if ttm_rev and ttm_rev > 0:
            if ttm_oi is not None:
                r = _pct(ttm_oi, ttm_rev)
                if r:
                    opm_parts.append(f"TTM {r}")
            if ttm_ni is not None:
                r = _pct(ttm_ni, ttm_rev)
                if r:
                    npm_parts.append(f"TTM {r}")

        # Annual rows — match income col to nearest balance col
        bal_cols = list(bal.columns[:3]) if bal is not None and not (
            hasattr(bal, "empty") and bal.empty) else []

        def _nearest_bal(col, bal_dict):
            if col in bal_dict:
                return bal_dict[col]
            if bal_dict:
                return list(bal_dict.values())[0]
            return None

        for col in fy_cols:
            rv = rev.get(col)
            ov = oi.get(col)
            nv = ni.get(col)
            lbl = _fy_label(col)
            if rv and rv > 0:
                if ov is not None:
                    r = _pct(ov, rv)
                    if r:
                        opm_parts.append(f"{lbl} {r}")
                if nv is not None:
                    r = _pct(nv, rv)
                    if r:
                        npm_parts.append(f"{lbl} {r}")
            if nv is not None:
                eq_v = _nearest_bal(col, equity)
                if eq_v:
                    r = _pct(nv, eq_v)
                    if r:
                        roe_parts.append(f"{lbl} {r}")
                as_v = _nearest_bal(col, assets)
                if as_v:
                    r = _pct(nv, as_v)
                    if r:
                        roa_parts.append(f"{lbl} {r}")

        de_parts: list[str] = []   # 차입금비율 = Total Debt / Equity
        tl_parts: list[str] = []   # 총부채비율 = Total Liabilities / Equity (KR std)
        cr_parts: list[str] = []
        for col in bal_cols:
            dv  = debt.get(col)
            tlv = tot_liab.get(col)
            ev  = equity.get(col)
            av  = curr_a.get(col)
            lv  = curr_l.get(col)
            lbl = _fy_label(col)
            if dv is not None and ev and ev > 0:
                r = _pct(dv, ev)
                if r:
                    de_parts.append(f"{lbl} {r}")
            if tlv is not None and ev and ev > 0:
                r = _pct(tlv, ev)
                if r:
                    tl_parts.append(f"{lbl} {r}")
            if av is not None and lv and lv > 0:
                cr = float(av) / float(lv) * 100
                cr_parts.append(f"{lbl} {cr:.0f}%")

        profitability = [p for p in [
            f"  영업이익률: {' | '.join(opm_parts[:4])}" if opm_parts else "",
            f"  순이익률:   {' | '.join(npm_parts[:4])}" if npm_parts else "",
            f"  ROE:       {' | '.join(roe_parts[:4])}" if roe_parts else "",
            f"  ROA:       {' | '.join(roa_parts[:4])}" if roa_parts else "",
        ] if p]
        leverage = [p for p in [
            f"  차입금비율: {' | '.join(de_parts[:3])}" if de_parts else "",
            f"  총부채비율: {' | '.join(tl_parts[:3])}" if tl_parts else "",
            f"  유동비율:   {' | '.join(cr_parts[:3])}" if cr_parts else "",
        ] if p]

        if not profitability and not leverage:
            return ""

        lines = [
            "=== PRE-COMPUTED 수익성·안정성 비율 (시스템 직접 산출 — 수치 절대 변경 금지) ===",
            "분析가 지시: 아래 비율을 RULE 5/6 수익성·안정성 분析에 그대로 복사."
            " 비율 재계산 절대 금지. 차입금비율 = Total Debt/Equity (US 차입금),"
            " 총부채비율 = Total Liabilities/Equity (KR 표준 부채비율).",
            "",
        ]
        lines.extend(profitability)
        if leverage:
            lines.append("")
            lines.extend(leverage)
        lines += ["", "위 값 우선. 재계산 금지."]
        return "\n".join(lines)
    except Exception as exc:
        _analyst_log.warning("ratios_block failed for %s: %s", ticker, exc)
        return ""


def _build_factual_anchor(ticker: str, info: dict, _cfg: dict) -> str:
    """Compact FACTUAL ANCHOR injected at the very top of every analyst
    prompt. Shows 현재가 / 시총 / 52w high-low / PER / PBR / EPS in a
    single glanceable block so the LLM sees canonical numbers first.

    Corrupt values (52w ≤ 0, current < 52w-low, etc.) are flagged
    inline so the LLM cannot silently copy them.
    Returns '' when no price data is available.
    """
    try:
        currency = _cfg.get("currency", "USD")
        sym      = _cfg.get("currency_symbol", "$")
        _fmt_p   = "{:,.0f}" if currency == "KRW" else "{:,.2f}"

        px         = info.get("currentPrice") or info.get("regularMarketPrice")
        market_cap = info.get("marketCap")
        wk_high    = info.get("fiftyTwoWeekHigh")
        wk_low     = info.get("fiftyTwoWeekLow")
        per        = info.get("trailingPE")
        pbr        = info.get("priceToBook")
        eps        = info.get("trailingEps")

        if not (isinstance(px, (int, float)) and px == px and px > 0):
            return ""

        px_str = f"{sym}{_fmt_p.format(px)}"

        # 시가총액
        mc_str = "N/A"
        if isinstance(market_cap, (int, float)) and market_cap > 0:
            if currency in ("KRW", "JPY"):
                unit_word = "원" if currency == "KRW" else "엔"
                if market_cap >= 1e12:
                    mc_str = f"약 {market_cap / 1e12:,.2f}조 {unit_word}"
                else:
                    mc_str = f"약 {market_cap / 1e8:,.0f}억 {unit_word}"
            elif currency == "TWD":
                if market_cap >= 1e12:
                    mc_str = f"약 NT${market_cap / 1e12:,.2f}兆"
                else:
                    mc_str = f"약 NT${market_cap / 1e8:,.0f}億"
            elif currency in ("CNY", "HKD"):
                pfx = "¥" if currency == "CNY" else "HK$"
                mc_str = (f"약 {pfx}{market_cap / 1e12:,.2f}兆"
                          if market_cap >= 1e12
                          else f"약 {pfx}{market_cap / 1e9:,.2f}B")
            else:
                mc_str = (f"${market_cap / 1e9:,.2f}B"
                          if market_cap >= 1e9
                          else f"${market_cap / 1e6:,.0f}M")

        # 52주 고저 — corrupt 값 자동 감지
        def _52w(v, is_low: bool) -> str:
            label = "52주 최저" if is_low else "52주 최고"
            if not isinstance(v, (int, float)) or v != v:
                return f"{label}: N/A"
            if v <= 0 or (px > 0 and v < px * 0.01):
                return f"{label}: ⚠️데이터오류(0금지)"
            if is_low and px > 0 and v > px * 1.005:
                return f"{label}: ⚠️데이터오류(최저>현재가)"
            if not is_low and px > 0 and v < px * 0.99:
                return f"{label}: ⚠️데이터오류(최고<현재가)"
            return f"{label}: {sym}{_fmt_p.format(v)}"

        wk_high_str = _52w(wk_high, is_low=False)
        wk_low_str  = _52w(wk_low,  is_low=True)

        # Fix R (2026-05-24): 적자 기업의 PER → "N/M(적자)" substitute
        # 대신 "N/A" 표기 시 LLM 이 "데이터 없음" 으로 오인식. EPS 가
        # 음수이면 PER 산출 불가능 — 자명한 상태이므로 명시. Universal.
        if isinstance(per, (int, float)) and per == per and 0 < per < 10000:
            per_str = f"{per:.1f}"
        elif isinstance(eps, (int, float)) and eps == eps and eps < 0:
            per_str = "N/M(적자)"
        else:
            per_str = "N/A"
        pbr_str = (f"{pbr:.2f}" if isinstance(pbr, (int, float))
                   and pbr == pbr and -100 < pbr < 1000 else "N/A")
        eps_str = (f"{sym}{_fmt_p.format(eps)}" if isinstance(eps, (int, float))
                   and eps == eps else "N/A")

        # Cross-anchor consistency check (319660.KS 2026-05-23 surfaced).
        # 외부 검증 framework (047810.KS) 의 수식을 코드화: PER × EPS ≈ price
        # (within ±15%). 분할 (split) 등 corp action 으로 일부 source 가
        # adjusted, 다른 source 가 unadjusted 일 때 ratio 가 깨짐. Universal
        # — US/KR/JP/TW/CN/HK 모두 동일. 위반 시 HARD GUARD 자동 주입.
        inconsistency_lines: list[str] = []
        # Fix A (2026-05-23, 207940.KS 외부 검증 surface): KRX shares override
        # 가 발생했으면 (yfinance shares != KRX shares > 5%), 사용자에게
        # 명시적으로 알린다. PER × EPS check 는 yfinance 내부 정합성이라
        # 자기참조 통과 가능 — 외부 anchor 위반은 별도 surface 필요.
        sh_override = info.get("_shares_override_warning")
        if sh_override:
            inconsistency_lines.append(sh_override)
        if (isinstance(per, (int, float)) and per == per and 0 < per < 10000
                and isinstance(eps, (int, float)) and eps == eps and eps != 0
                and isinstance(px, (int, float)) and px > 0):
            implied = per * eps
            if implied > 0 and abs(implied - px) / px > 0.15:
                ratio = implied / px
                inconsistency_lines.append(
                    f"⚠️ PER × EPS 불일치: {per:.1f} × {sym}{_fmt_p.format(eps)}"
                    f" = {sym}{_fmt_p.format(implied)}, 현재가 {px_str}"
                    f" (ratio {ratio:.2f}x) — 분할 / corp action 가능"
                )
        # PBR consistency: PBR × BPS ≈ currentPrice (within ±15%)
        bps = info.get("bookValue")
        if (isinstance(pbr, (int, float)) and pbr == pbr and 0 < pbr < 1000
                and isinstance(bps, (int, float)) and bps == bps and bps > 0
                and isinstance(px, (int, float)) and px > 0):
            implied_p = pbr * bps
            if implied_p > 0 and abs(implied_p - px) / px > 0.15:
                ratio = implied_p / px
                inconsistency_lines.append(
                    f"⚠️ PBR × BPS 불일치: {pbr:.2f} × {sym}{_fmt_p.format(bps)}"
                    f" = {sym}{_fmt_p.format(implied_p)}, 현재가 {px_str}"
                    f" (ratio {ratio:.2f}x) — 분할 / BPS 재계산 시점 차이"
                )

        # Fix H (2026-05-23, FORM US 7-axis + 외부 검증자 제언): forwardEps /
        # trailingEps > 2.5x ratio is a red flag — spin-off, large one-time
        # item, or yfinance serving a stale/cached forward estimate. Threshold
        # 2.5x vs existing 3x in _get_market_signals_for gives earlier warning.
        # Universal — applies to US + KR + JP + TW + CN/HK.
        # Rule applies to all analyses going forward.
        fwd_eps = info.get("forwardEps")
        if (isinstance(fwd_eps, (int, float)) and fwd_eps == fwd_eps
                and isinstance(eps, (int, float)) and eps == eps and eps != 0):
            eps_ratio = fwd_eps / eps
            # Same-sign extreme ratio = real anomaly (spin-off / one-time /
            # stale forward estimate). Sign-flip deterioration (eps>0 →
            # fwd_eps<0) = profit collapse, also anomaly worth flagging.
            # Sign-flip TURNAROUND (eps<0 → fwd_eps>0 = loss to profit)
            # is LEGITIMATE business recovery, NOT a data error or corp
            # action. Meituan 3690.HK 2026-05-28: TTM EPS HK$-4.52 + Fwd
            # EPS HK$~+positive triggered abs(ratio)≥2.5 → injected
            # "데이터 transitional (corp action 의심)" guard text into a
            # report about a legitimate turnaround story. Exempt this case.
            same_sign_extreme = (
                eps * fwd_eps > 0 and abs(eps_ratio) >= 2.5
            )
            deterioration = (eps > 0 and fwd_eps < 0)
            if same_sign_extreme or deterioration:
                inconsistency_lines.append(
                    f"⚠️ Forward EPS {sym}{_fmt_p.format(fwd_eps)} vs TTM EPS"
                    f" {sym}{_fmt_p.format(eps)} (비율 {eps_ratio:.1f}x) —"
                    f" 통상 범위 초과. spin-off/일회성 항목/데이터 오류 가능."
                    f" Forward PER 사용 전 EPS 추세 검증 필수."
                )

        # Fix ② (2026-05-23, 외부 검증자 제언): dual-class shares
        # directive. GOOG/GOOGL, BRK.A/BRK.B (US) + EPI-A.ST, VOLV-B.ST,
        # CARL-B.CO, VOW3.DE 등 (EU) 은 yfinance 가 특정 share class
        # 기준으로 sharesOutstanding 을 반환 — 전체 시총 대비 EPS/PBR
        # 계산이 왜곡될 수 있음. EU 확장 2026-05-29 (Metals & Mining
        # 도메인 review surfaced EPI-A.ST). US 셋은 ticker bare (점 앞)
        # 매칭, EU 셋은 suffix 포함 full-ticker 매칭 — Wallenberg 패밀리의
        # A/B class 분리 등.
        try:
            from bot.market import (
                _US_DUAL_CLASS_TICKERS, _EU_DUAL_CLASS_TICKERS,
            )
            ticker_upper = (ticker or "").upper()
            ticker_bare = ticker_upper.split(".")[0]
            is_dual_class = (
                ticker_bare in _US_DUAL_CLASS_TICKERS
                or ticker_upper in _EU_DUAL_CLASS_TICKERS
            )
            if is_dual_class:
                inconsistency_lines.append(
                    f"⚠️ DUAL-CLASS SHARES: {ticker_upper} 는 차등의결권(다중"
                    f" class) 구조. yfinance sharesOutstanding 이 특정 class"
                    f" 기준일 수 있음 (예: BRK.A vs BRK.B × 1500, GOOG Class"
                    f" C vs GOOGL Class A, EPI-A vs EPI-B). 시총은 yfinance"
                    f" marketCap(전체 가중 mc) 을 canonical 로 사용. EPS"
                    f" = NI / total_shares 기준이므로 이 class 의 PER"
                    f" 계산이 의도한 값인지 확인 권고."
                )
        except Exception:
            pass

        # Fix C (2026-05-23, 207940.KS surface): non-tautology external anchor
        # — shares × price 로 mc 재계산해서 info["marketCap"] 과 비교. yfinance
        # 내부 정합성(EPS=NI/sh, BPS=Eq/sh, mc=px×sh 모두 같은 sh로 도출)
        # 만으로는 shares 자체 오류를 잡을 수 없음. info["sharesOutstanding"]
        # 가 Fix A 로 KRX shares 로 정정된 후, mc = canonical_shares × price
        # vs info["marketCap"] 비교. 차이 > 5% 면 mc 도 의심.
        canonical_sh = info.get("sharesOutstanding")
        # Dual-class skip — yfinance 가 한 class shares 만 반환하지만
        # marketCap 은 양 class 합산이라 구조적 mismatch (Epiroc A: 485M
        # shares vs 합산 1217M → ratio 0.4) 가 항상 발생. 위 dual-class
        # banner 가 이미 reader 에게 고지하므로 추가 'corp action 의심'
        # 발화 차단. 2026-05-29 Metals & Mining review surfaced EPI-A.ST.
        skip_mc_check = False
        try:
            from bot.market import (
                _US_DUAL_CLASS_TICKERS, _EU_DUAL_CLASS_TICKERS,
            )
            _tu = (ticker or "").upper()
            if (_tu.split(".")[0] in _US_DUAL_CLASS_TICKERS
                    or _tu in _EU_DUAL_CLASS_TICKERS):
                skip_mc_check = True
        except Exception:
            pass
        if (not skip_mc_check
                and isinstance(canonical_sh, (int, float)) and canonical_sh > 0
                and isinstance(market_cap, (int, float)) and market_cap > 0
                and isinstance(px, (int, float)) and px > 0):
            implied_mc = canonical_sh * px
            if implied_mc > 0 and abs(implied_mc - market_cap) / market_cap > 0.05:
                ratio_mc = implied_mc / market_cap
                inconsistency_lines.append(
                    f"⚠️ MC 외부 anchor 불일치: shares × 현재가"
                    f" = {sym}{_fmt_p.format(implied_mc)} vs 보고 시총"
                    f" {mc_str} (ratio {ratio_mc:.2f}x) — yfinance shares"
                    f" 또는 marketCap 한쪽이 stale."
                )

        sep = "━" * 56
        lines = [
            sep,
            "⚡ FACTUAL ANCHOR — 시스템 직접 산출 (절대 재계산 금지)",
            sep,
            f"현재가:   {px_str:<20}  시가총액: {mc_str}",
            f"{wk_high_str:<30}  {wk_low_str}",
            f"PER: {per_str:<10}  PBR: {pbr_str:<10}  EPS: {eps_str}",
        ]
        if inconsistency_lines:
            lines.append(sep)
            lines.extend(inconsistency_lines)
            lines.append(
                "⛔ CROSS-ANCHOR HARD GUARD: 위 inconsistency 가 detected."
                " PER / PBR / SMA / 기술지표 cite 시 반드시 '데이터 transitional"
                " (corp action 의심)' 명시. 5거래일 horizon dominant variable"
                " 로 valuation/기술지표 사용 금지 — corp action 정정 전 보류."
            )
        lines.extend([
            sep,
            "❌ HARD RULE: 위 값은 yfinance .info 원본. 재계산·재호출·근사화 절대 금지.",
            "   모든 섹션에서 위 값 그대로 복사·인용. ⚠️데이터오류 항목은 인용 자체 금지.",
            sep,
        ])
        return "\n".join(lines)
    except Exception:
        return ""


def _compute_technical_snapshot(ticker: str) -> str:
    """Single Source of Truth for RSI(14), MACD(12,26,9), Bollinger(20,2σ).

    Injected into every analyst prompt so ALL analysts share the same
    pre-computed technical values. Prevents cross-analyst RSI/MACD
    divergence (FORM 2026-05-23: 시장 analyst RSI 49.37 vs 감정 analyst
    RSI 78 fabrication). Analysts MUST cite these values; fabricating
    different values is FORBIDDEN.
    Rule applies to all analyses going forward — US + KR + JP + TW + CN/HK.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="3mo", auto_adjust=True)
        if hist is None or len(hist) < 20:
            return ""
        close = hist["Close"].dropna()
        if len(close) < 20:
            return ""

        # RSI(14) — Wilder exponential smoothing
        rsi_str = "N/A"
        rsi_val_num: float | None = None
        if len(close) >= 15:
            delta = close.diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
            last_loss = float(avg_loss.iloc[-1])
            if last_loss == 0:
                rsi_val_num = 100.0
            else:
                rs = float(avg_gain.iloc[-1]) / last_loss
                rsi_val_num = 100 - 100 / (1 + rs)
            rsi_str = f"{rsi_val_num:.1f}"

        # MACD(12, 26, 9)
        macd_str = "N/A"
        if len(close) >= 27:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            macd_sig = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist_s = macd_line - macd_sig
            macd_str = (
                f"MACD {macd_line.iloc[-1]:.3f}"
                f" / Signal {macd_sig.iloc[-1]:.3f}"
                f" / Hist {macd_hist_s.iloc[-1]:.3f}"
            )

        # Bollinger Bands(20, 2σ)
        bb_str = "N/A"
        if len(close) >= 20:
            try:
                from bot.market import get_market_config as _gcfg
                _c = _gcfg(ticker)
                _sym_bb = _c.get("currency_symbol", "$")
                _fmt_bb: str = "{:,.0f}" if _c.get("currency") in ("KRW", "JPY") else "{:,.2f}"
            except Exception:
                _sym_bb, _fmt_bb = "$", "{:,.2f}"
            bb_mid = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            bb_u = bb_mid + 2 * bb_std
            bb_l = bb_mid - 2 * bb_std
            bb_str = (
                f"상단 {_sym_bb}{_fmt_bb.format(float(bb_u.iloc[-1]))}"
                f" / 중단 {_sym_bb}{_fmt_bb.format(float(bb_mid.iloc[-1]))}"
                f" / 하단 {_sym_bb}{_fmt_bb.format(float(bb_l.iloc[-1]))}"
            )

        # RSI zone label for PM override trigger context
        rsi_zone = ""
        if rsi_val_num is not None:
            if rsi_val_num >= 75:
                rsi_zone = " ⚠️ [과매수 ≥75 — PM override 허용 trigger]"
            elif rsi_val_num <= 25:
                rsi_zone = " ⚠️ [과매도 ≤25 — PM override 허용 trigger]"
            elif rsi_val_num >= 70:
                rsi_zone = " [과매수 접근 구간 ≥70]"
            elif rsi_val_num <= 30:
                rsi_zone = " [과매도 접근 구간 ≤30]"

        sep = "━" * 56
        return "\n".join([
            sep,
            "📐 TECHNICAL SNAPSHOT — 백엔드 단 1회 계산값 (재계산·재인용 금지)",
            sep,
            f"RSI(14): {rsi_str}{rsi_zone}",
            f"{macd_str}",
            f"볼린저(20,2σ): {bb_str}",
            sep,
            "⛔ SINGLE SOURCE OF TRUTH 강제 적용 (FORM 2026-05-23 RSI hallucination 방지):",
            "   • 위 RSI(14) / MACD / 볼린저 수치가 이 분석의 유일한 canonical 값.",
            "   • 위와 다른 RSI / MACD / 볼린저 값을 독자 계산·추정·인용하는 것 FORBIDDEN.",
            "   • **글자 단위 copy 의무** (2026-05-29 4063.T 신에쓰화학 review"
            " surfaced): 본문 / 요약 / 결론에서 specific 수치 (예: MACD Hist"
            " 7.476, RSI 62.8, 볼린저 상단 ¥7,768) 를 인용할 때 위 snapshot"
            " 의 값을 글자 단위로 정확히 copy. 반올림 / 자릿수 축약 / 0.5"
            " 단위 paraphrase 금지. 시장 'Hist 7.476' vs 뉴스 'Hist 7.986'"
            " 같은 0.5 mismatch = Pro 가 snapshot 무시하고 paraphrase 한"
            " hallucination 사례 — 모든 분석가가 같은 문자열을 copy 해야"
            " cross-section 일관성 보장.",
            "   • 시장 분석가 전용: get_indicators / get_stock_data 툴은 추세 해석"
            " (크로스오버·다이버전스 방향·지지/저항) 용도로만 사용하고, 요약표·본문에"
            " 인용하는 RSI / MACD / 볼린저 '수치'는 반드시 위 canonical 값을 사용."
            " 툴 재계산값이 위와 달라도 위 snapshot 이 우선 (2382.TW 2026-05-28:"
            " 시장 MACD -1.61 vs 감정/뉴스 -1.792 불일치 — 시장 분석가가"
            " get_indicators 재계산값을 인용한 cross-section 모순 케이스).",
            f"   • '과매수' 언급 조건: 위 RSI(14) [{rsi_str}] ≥ 75 인 경우에만 허용.",
            f"   • '과매도' 언급 조건: 위 RSI(14) [{rsi_str}] ≤ 25 인 경우에만 허용.",
            sep,
        ])
    except Exception:
        return ""


def _section_allowed(analyst_id: str | None, section: str) -> bool:
    """Return True when this section should appear in the analyst's
    prompt. `analyst_id is None` (non-analyst callers — PM, trader,
    research manager) always sees every section."""
    if analyst_id is None:
        return True
    return section not in _ANALYST_CONTEXT_EXCLUDE.get(analyst_id, set())


def build_instrument_context(ticker: str, analyst_id: str | None = None) -> str:
    """Memoizing wrapper around `_build_instrument_context_impl` (F1, 2026-
    05-29 audit). Keyed by (ticker, analyst_id, KST-date) so the up-to-8
    builds per analysis collapse to one per distinct shape. Bounded:
    cleared wholesale when it grows past 256 entries (screener can touch
    many tickers/day). The NOAH /ticker path additionally calls
    clear_instrument_caches() at run start for intraday freshness."""
    import datetime as _ctx_dt
    _today_kst = _ctx_dt.datetime.now(
        _ctx_dt.timezone(_ctx_dt.timedelta(hours=9))
    ).date().isoformat()
    _key = (ticker, analyst_id, _today_kst)
    _hit = _INSTRUMENT_CONTEXT_CACHE.get(_key)
    if _hit is not None:
        return _hit
    _result = _build_instrument_context_impl(ticker, analyst_id)
    if len(_INSTRUMENT_CONTEXT_CACHE) > 256:
        _INSTRUMENT_CONTEXT_CACHE.clear()
    _INSTRUMENT_CONTEXT_CACHE[_key] = _result
    return _result


def _build_instrument_context_impl(ticker: str, analyst_id: str | None = None) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified
    tickers and adjust their data expectations for non-equity products.

    `analyst_id` (optional): one of "market" / "social" / "news" /
    "fundamentals". When set, heavy sections not relevant to that
    analyst are excluded (see `_ANALYST_CONTEXT_EXCLUDE`). Non-analyst
    callers (portfolio manager, trader, research manager) pass None /
    omit the argument and see the full context — backward-compatible."""
    # Fetch instrument data first — needed by FACTUAL ANCHOR and all
    # downstream sections. Previously `base` was built before `info` was
    # fetched; moving `info` + market detection above `base` construction
    # lets us prepend the anchor without a second pass.
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

    # D1 Phase 3 (307950.KS 2026-05-23 surfaced): KR 종목 yfinance .info
    # 결측 (시총 / 52w / 50d-200d SMA / PER / EPS) 시 pykrx OHLCV + 시총
    # + financial statements 로 Python 자체 산출 fallback. 빈 자리를 LLM
    # 이 fabricate 시도하는 패턴 (PM "KIS PER 94.8") 차단. 인라인 호출
    # 이지만 동일 cache 키 사용하므로 downstream prefetch 가 hit 한다.
    if market == "KR":
        try:
            from bot.pykrx_client import get_kr_market_cap, get_kr_ohlcv_stats
            mc_data = get_kr_market_cap(ticker)
            if mc_data and mc_data.get("market_cap"):
                # Fix A (2026-05-23, 207940.KS 외부 검증 surface): KR shares
                # always-override (not on-miss). yfinance 한국 종목 발행
                # 주식수가 non-zero but wrong 으로 던져지는 패턴 — 삼성바이오
                # 207940 yfinance shares 46.29M vs 실제 KRX 71.17M (35% 누락)
                # → 시총 65.5조 (실제 100.7조) + PBR 8.78 (실제 13.5) 왜곡.
                # 047810.KS shares=0 cascade 와 같은 root cause 의 다른 양상.
                # KRX 공식 상장주식수가 canonical — drift > 5% 시 override
                # 후 mc/BPS/PBR 모두 일관 재계산. Rule applies to all KR
                # analyses going forward.
                canonical_shares = mc_data.get("shares") or 0
                yf_shares = info.get("sharesOutstanding") or 0
                px_for_recalc = (
                    info.get("currentPrice")
                    or info.get("regularMarketPrice")
                    or mc_data.get("close")
                )
                shares_override_warning = ""
                if canonical_shares > 0:
                    sh_drift = (
                        abs(canonical_shares - yf_shares) / canonical_shares
                        if (isinstance(yf_shares, (int, float)) and yf_shares > 0)
                        else 1.0  # yfinance missing — canonical replaces
                    )
                    if sh_drift > 0.05:
                        old_shares = yf_shares if yf_shares else 0
                        info["sharesOutstanding"] = int(canonical_shares)
                        # marketCap: KRX 공식 시총 또는 price × canonical shares
                        krx_mc = int(mc_data["market_cap"])
                        if isinstance(px_for_recalc, (int, float)) and px_for_recalc > 0:
                            # KRX market_cap_by_ticker 가 같은 close 기준이므로
                            # 두 값은 거의 일치해야 함. 차이 < 1% 면 KRX mc 채택.
                            recalc_mc = int(px_for_recalc * canonical_shares)
                            info["marketCap"] = max(krx_mc, recalc_mc)
                        else:
                            info["marketCap"] = krx_mc
                        # BPS / PBR 재계산: 기존 BPS 가 잘못된 shares 로 도출
                        # 됐으면 PBR 도 왜곡. 자기자본 추정값 (yf BPS × yf
                        # shares) 을 canonical shares 로 다시 나눠 보정.
                        old_bps = info.get("bookValue")
                        if (isinstance(old_bps, (int, float)) and old_bps > 0
                                and old_shares > 0):
                            equity_est = old_bps * old_shares
                            new_bps = equity_est / canonical_shares
                            info["bookValue"] = new_bps
                            if (isinstance(px_for_recalc, (int, float))
                                    and px_for_recalc > 0 and new_bps > 0):
                                info["priceToBook"] = px_for_recalc / new_bps
                        # EPS 도 같은 원리: NI/shares. EPS × old_shares = NI,
                        # ÷ canonical_shares 로 정정.
                        old_eps = info.get("trailingEps")
                        if (isinstance(old_eps, (int, float)) and old_eps != 0
                                and old_shares > 0):
                            ni_est = old_eps * old_shares
                            new_eps = ni_est / canonical_shares
                            info["trailingEps"] = new_eps
                            if (isinstance(px_for_recalc, (int, float))
                                    and px_for_recalc > 0 and new_eps > 0):
                                info["trailingPE"] = px_for_recalc / new_eps
                        shares_override_warning = (
                            f"⚠️ KRX SHARES OVERRIDE: yfinance 발행주식수"
                            f" {old_shares:,} → KRX 공식 {canonical_shares:,}"
                            f" (drift {sh_drift * 100:.1f}%). 시총·PBR·EPS·PER"
                            f" 모두 KRX shares 기준으로 정정."
                        )
                        info["_shares_override_warning"] = shares_override_warning
                        _analyst_log.info(
                            "Fix A KRX shares override for %s: %s → %s (drift %.1f%%)",
                            ticker, old_shares, canonical_shares, sh_drift * 100,
                        )
                    elif yf_shares == 0 and canonical_shares > 0:
                        # yfinance missing — pure fallback (이전 0d3afcf 패턴)
                        info["sharesOutstanding"] = int(canonical_shares)
                if not (isinstance(info.get("marketCap"), (int, float))
                        and info.get("marketCap")):
                    info["marketCap"] = int(mc_data["market_cap"])
                if not (isinstance(info.get("currentPrice"), (int, float))
                        and info.get("currentPrice")):
                    info["currentPrice"] = int(mc_data["close"])
            stats = get_kr_ohlcv_stats(ticker)
            if stats:
                for key_y, key_p in [
                    ("fiftyTwoWeekHigh",     "wk_high"),
                    ("fiftyTwoWeekLow",      "wk_low"),
                    ("fiftyDayAverage",      "sma50"),
                    ("twoHundredDayAverage", "sma200"),
                ]:
                    if not (isinstance(info.get(key_y), (int, float))
                            and info.get(key_y)
                            and info[key_y] == info[key_y]):
                        if stats.get(key_p):
                            info[key_y] = stats[key_p]
            # Python-compute PER removed (2026-05-23): computing mc/ni_ttm
            # from quarterly income_stmt produces inaccurate KR values due
            # to yfinance unit inconsistencies (백만 ↔ 억 mix). Naver Finance
            # (D1 Phase 5 below) fetches the authoritative consensus figure.
        except Exception as exc:
            _analyst_log.warning(
                "D1 Phase 3 pykrx info patch failed for %s: %s", ticker, exc,
            )

        # D1 Phase 3.5 (2026-05-23 010140.KS surfaced): pykrx 60-month
        # 월간 베타 vs KOSPI 200 — yfinance .info['beta'] 가 KR 종목도
        # S&P 500 기준으로 계산 (010140.KS '베타 1.83 vs KOSPI 200'
        # 라벨 vs 실제 vs S&P 500 mismatch). pykrx 시계열로 직접 산출해
        # info['beta'] override. Rule applies to all KR analyses.
        try:
            from bot.pykrx_client import get_kr_beta_60m
            kr_beta = get_kr_beta_60m(ticker)
            if kr_beta is not None and isinstance(kr_beta, (int, float)):
                info["beta"] = kr_beta
        except Exception as exc:
            _analyst_log.warning(
                "KR beta 60M recompute failed for %s: %s", ticker, exc,
            )

        # D1 Phase 4 (2026-05-23 010140.KS surfaced): KIS price block as
        # 3rd fallback after yfinance + pykrx. KIS inquire-price returns
        # 시가총액 (hts_avls) + PER + PBR + EPS + BPS + 상장주수 — when
        # both yfinance .info and pykrx miss, KIS is the last source
        # before N/A. Without this 010140.KS 펀더 박스 showed '시가총액
        # N/A, PER N/A, PBR N/A, EPS N/A' even though KIS block had
        # PER 47.6, PBR 6.07 — the KIS data sat in the prefetched dict
        # but was scope-guarded out of canonical / fundamentals reach.
        # Rule applies to all KR analyses going forward.
        try:
            from bot.kis_client import get_kis
            kis_price = get_kis().get_price(ticker)
            if kis_price:
                if not (isinstance(info.get("marketCap"), (int, float))
                        and info.get("marketCap")):
                    if kis_price.get("market_cap"):
                        info["marketCap"] = int(kis_price["market_cap"])
                if not (isinstance(info.get("trailingPE"), (int, float))
                        and info.get("trailingPE")):
                    if kis_price.get("per") and kis_price["per"] > 0:
                        info["trailingPE"] = kis_price["per"]
                if not (isinstance(info.get("priceToBook"), (int, float))
                        and info.get("priceToBook")):
                    if kis_price.get("pbr") and kis_price["pbr"] > 0:
                        info["priceToBook"] = kis_price["pbr"]
                if not (isinstance(info.get("trailingEps"), (int, float))
                        and info.get("trailingEps")):
                    if kis_price.get("eps"):
                        info["trailingEps"] = kis_price["eps"]
                if not (isinstance(info.get("bookValue"), (int, float))
                        and info.get("bookValue")):
                    if kis_price.get("bps"):
                        info["bookValue"] = kis_price["bps"]
                if not (isinstance(info.get("sharesOutstanding"), (int, float))
                        and info.get("sharesOutstanding")):
                    if kis_price.get("shares"):
                        info["sharesOutstanding"] = kis_price["shares"]
                if not (isinstance(info.get("fiftyTwoWeekHigh"), (int, float))
                        and info.get("fiftyTwoWeekHigh")):
                    if kis_price.get("high_52w"):
                        info["fiftyTwoWeekHigh"] = kis_price["high_52w"]
                if not (isinstance(info.get("fiftyTwoWeekLow"), (int, float))
                        and info.get("fiftyTwoWeekLow")):
                    if kis_price.get("low_52w"):
                        info["fiftyTwoWeekLow"] = kis_price["low_52w"]
        except Exception as exc:
            _analyst_log.warning(
                "D1 Phase 4 KIS info patch failed for %s: %s", ticker, exc,
            )

        # D1 Phase 5 (2026-05-23): Naver Finance PER/PBR/EPS/BPS as 4th
        # fallback (yfinance → pykrx → KIS → Naver Finance). Naver Finance
        # carries QuantiWise/FnGuide consensus valuation — more accurate for
        # KR equities than Python-computed mc/ni_ttm. Only fills fields still
        # missing after Phase 4. Rule applies to all KR analyses going forward.
        try:
            from bot.naver_finance_client import get_naver_valuation
            nav = get_naver_valuation(ticker)
            if nav:
                if not (isinstance(info.get("trailingPE"), (int, float))
                        and info.get("trailingPE")):
                    if nav.get("per") and nav["per"] > 0:
                        info["trailingPE"] = nav["per"]
                if not (isinstance(info.get("priceToBook"), (int, float))
                        and info.get("priceToBook")):
                    if nav.get("pbr") and nav["pbr"] > 0:
                        info["priceToBook"] = nav["pbr"]
                if not (isinstance(info.get("trailingEps"), (int, float))
                        and info.get("trailingEps")):
                    if nav.get("eps"):
                        info["trailingEps"] = nav["eps"]
                if not (isinstance(info.get("bookValue"), (int, float))
                        and info.get("bookValue")):
                    if nav.get("bps"):
                        info["bookValue"] = nav["bps"]
                if not (isinstance(info.get("sharesOutstanding"), (int, float))
                        and info.get("sharesOutstanding")):
                    if nav.get("shares"):
                        info["sharesOutstanding"] = int(nav["shares"])
        except Exception as exc:
            _analyst_log.warning(
                "D1 Phase 5 Naver Finance patch failed for %s: %s", ticker, exc,
            )

        # D1 Phase 6 (2026-05-23): sharesOutstanding ↔ marketCap 양방향
        # 역산 fallback. 047810.KS 외부 검증 surface: shares 단일 변수만
        # 채우면 EPS/PER/PBR 1주당 지표 cascade 해결. 319660.KS 검증 추가:
        # shares 만 있고 marketCap 이 비어있는 case 도 역방향으로 채워야
        # 시총 N/A 가 0d3afcf 단독 으로도 사라짐. Universal — KR/JP/TW/CN/HK
        # 모두 적용.
        try:
            mc = info.get("marketCap")
            sh = info.get("sharesOutstanding")
            px = info.get("currentPrice") or info.get("regularMarketPrice")
            mc_ok = isinstance(mc, (int, float)) and mc and mc > 0
            sh_ok = isinstance(sh, (int, float)) and sh and sh > 0
            px_ok = isinstance(px, (int, float)) and px and px > 0

            # Forward: shares = mc / price (when mc + price 있고 shares 없음)
            if not sh_ok and mc_ok and px_ok:
                approx = int(mc / px)
                if approx > 0:
                    info["sharesOutstanding"] = approx
                    _analyst_log.info(
                        "D1 Phase 6 shares 역산 for %s: mc=%s / px=%s → %s",
                        ticker, mc, px, approx,
                    )
            # Reverse: mc = shares × price (when shares + price 있고 mc 없음)
            # 319660.KS surface: pykrx 가 shares 만 채우고 mc 가 비어있는
            # 케이스. 외부 검증 framework (047810): mc = price × shares.
            elif not mc_ok and sh_ok and px_ok:
                approx_mc = int(sh * px)
                if approx_mc > 0:
                    info["marketCap"] = approx_mc
                    _analyst_log.info(
                        "D1 Phase 6 mc 역산 for %s: shares=%s × px=%s → %s",
                        ticker, sh, px, approx_mc,
                    )
        except Exception as exc:
            _analyst_log.warning(
                "D1 Phase 6 양방향 역산 failed for %s: %s", ticker, exc,
            )

        # KSIC industry override (2026-05-23 삼성중공업 010140.KS surfaced).
        # yfinance industry tag for KR stocks is often wrong (010140 KSIC
        # C31111 강선건조업 mis-tagged as 'Aerospace & Defense' → Comps
        # peer set wrong, sector ETF wrong, RULE 10 dominant variable
        # wrong). DART /api/company.json `induty_code` is the authoritative
        # KR industry classification. Override info["industry"] BEFORE
        # downstream code (peer set / sector / Comps) reads it.
        try:
            from bot.dart_client import get_dart
            from bot.market import resolve_ksic_industry
            company_info = get_dart().get_company_info(ticker)
            if company_info:
                ksic = company_info.get("induty_code")
                mapped = resolve_ksic_industry(ksic)
                if mapped and mapped != info.get("industry"):
                    original = info.get("industry")
                    info["industry"] = mapped
                    _analyst_log.info(
                        "DART KSIC override for %s: '%s' → '%s' (induty_code=%s)",
                        ticker, original, mapped, ksic,
                    )
        except Exception as exc:
            _analyst_log.warning(
                "DART KSIC industry override failed for %s: %s", ticker, exc,
            )

    # FACTUAL ANCHOR: compact canonical-number box at the very top so the
    # LLM sees 현재가 / 시총 / 52w / PER / PBR / EPS before any other text.
    # Corrupt values (52w = 0, current < low, etc.) are pre-flagged here
    # so the LLM cannot silently copy them. Silent degradation: if info
    # has no price, anchor is '' and base is unchanged.
    _anchor = _build_factual_anchor(ticker, info, _cfg)
    base = (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
        + ("\n\n" + _anchor if _anchor else "")
    )

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
            f" peers.\n"
            f"\n"
            f"⚠️ INDUSTRY MISCLASSIFICATION CHECK (Fix I 보강, 2026-05-19"
            f" 노바렉스 194700.KS surfaced): yfinance 의 industry tag 가"
            f" 회사의 실제 business 와 명확히 불일치할 수 있다 (yfinance"
            f" 의 GICS-style 분류는 ODM/OEM 회사를 잘못 카테고리로 분류"
            f" 빈도 높음). 노바렉스 케이스: 회사는 건강기능식품 ODM/OEM"
            f" 인데 yfinance 가 'Specialty Chemicals' 로 분류 → MANDATORY"
            f" PEER SET 이 동진쎄미켐 / 솔브레인 등 반도체 소재 회사를"
            f" 강제 inject. 분석가가 그대로 인용 = misleading.\n"
            f"  분석가는 회사 longName / 산업 description (instrument_context"
            f" 의 'company name' 필드 + 본문) 과 위 yfinance industry"
            f" '{industry}' 가 자명히 다른 카테고리이면 (예: '건강기능식품'"
            f" longName 인데 industry='Specialty Chemicals'),\n"
            f"  ❌ 위 MANDATORY PEER SET (아래에 inject 된 ticker 들) 을"
            f" 그대로 인용 금지\n"
            f"  ✅ 'yfinance industry 분류 의심 — 회사 실제 business 와"
            f" 不일치 (longName 기반 판단). Comps peer set 부재로 처리'"
            f" 한 줄 명시 + Comps 표 자체 omit\n"
            f"  ✅ 또는 globally-recognized 직접 경쟁사 3개 (예: 노바렉스"
            f" → 콜마비앤에이치 / 코스맥스엔비티 / 종근당건강) 본인 지식"
            f" 으로 listing — 단 이 경우 'yfinance industry 미스매치 회피'"
            f" 명시 의무"
        )

        # ⛔ INTERNET VIE HARD GUARD (Fix C, 9988.HK / Alibaba 2026-05-27).
        # Chinese internet platforms (Tencent / Alibaba / JD / Meituan / …)
        # are held by foreign + HK investors only through a Cayman holdco
        # that contracts with the mainland operating entity — the VIE
        # structure the PRC could invalidate (2021 滴滴 precedent). RULE 13.4
        # in fundamentals_analyst already demands this be named, but the LLM
        # routinely falls back to a generic '中国 규제 위험' (9988.HK
        # 2026-05-27: the market primer AND the fundamentals conclusion both
        # omitted the VIE-specific dominant variable). A prominent context-
        # level HARD GUARD seen by EVERY analyst — not just fundamentals —
        # raises compliance the way the CORPORATE ACTION guard does.
        # Market-gated to CN_A/HK Internet names for a data-structure reason
        # (VIE is a PRC-specific construct); applies universally to every
        # such ticker going forward.
        if market in ("CN_A", "HK") and industry in (
            "Internet Content & Information", "Internet Retail",
        ):
            base += (
                f"\n\n=== ⛔ INTERNET VIE HARD GUARD (MANDATORY) ===\n"
                f"{ticker} 는 中国 인터넷 플랫폼으로 VIE 구조입니다. 외국인 /"
                f" HK 투자자는 본토 운영 entity 지분을 직접 보유하지 못하고"
                f" Cayman 지주사 ↔ 본토 VIE 계약으로 우회 보유합니다 — PRC 가"
                f" VIE 구조 자체를 무효화할 tail risk (2021 滴滴 선례).\n"
                f"  시장 sector primer 와 펀더멘털 / 뉴스 결론 중 최소 1곳"
                f" 에서 아래 dominant 변수 ≥1개를 반드시 명시 (generic '中国"
                f" 규제 위험' / '미중 갈등' 으로 대체 금지): (a) VIE 구조"
                f" 자체 무효화 risk, (b) 反독점 (SAMR) 罰款, (c) 게임 / 콘텐츠"
                f" 판호 발급 추이, (d) 美 entity list / SDN / HFCAA 상장폐지"
                f" 압력.\n"
                f"  ❌ FORBIDDEN (9988.HK 2026-05-27 패턴): '중국 정부의"
                f" 인터넷 산업 규제' / '미중 기술 갈등' 같은 막연한 서술만으로"
                f" 종결.\n"
                f"  ✅ RIGHT: '판호 발급 재개 추이' 또는 'VIE 구조 무효화 tail"
                f" risk' 등 구체 변수 1개 이상 인용 후 5거래일 가격 영향 연결."
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
                # Pre-fetch each peer's headline valuation multiples
                # from yfinance .info so the Comps table actually
                # carries numbers instead of '— N/A — N/A —' across
                # every row (코미코 2026-05-17 case: peer set
                # populated, multiples 0/4 populated, table useless).
                # Subject row included explicitly per Rule E extension
                # (TW TSMC 2026-05-18 surfaced this: News/Sentiment
                # Comps tables had TSMC row with N/A or fabricated
                # values like 'PER 29.5 / PBR 3.51' while Fundamentals
                # quoted 'PER 30.43 / PBR 9.86' from yfinance — same
                # stock, same report, 2.8x divergence on PBR).
                # Cap at 6 peers to keep the prompt budget bounded.
                peer_lines = []
                # Subject FIRST so analysts copy its row verbatim into
                # their own Comps tables. Then the curated peers.
                # Each row now includes the canonical company name from
                # yfinance .info longName — post MediaTek 2454.TW 2026-
                # 05-18 review where 3 analysts independently fabricated
                # different wrong names for 2379.TW / 3034.TW / 8299.TWO
                # because the PEER SET block only injected bare tickers.
                subject_multiples = _fetch_peer_multiples(ticker)
                if subject_multiples:
                    peer_lines.append(f"  • {ticker} (subject) — {subject_multiples}")
                else:
                    peer_lines.append(f"  • {ticker} (subject) — (data 미수집)")
                for t in peers[:6]:
                    multiples = _fetch_peer_multiples(t)
                    if multiples:
                        peer_lines.append(f"  • {t} — {multiples}")
                    else:
                        peer_lines.append(f"  • {t} — (data 미수집)")
                base += (
                    f"\n\n=== MANDATORY COMPS PEER SET + MULTIPLES ===\n"
                    f"For industry '{industry}', use EXACTLY these"
                    f" peer tickers in the Comps table — do NOT add,"
                    f" remove, or substitute any of them. Each row's"
                    f" format is `TICKER — Company Name | PER X.X /"
                    f" Fwd PER Y.Y / PSR Z.Z / PBR W.W / EV/EBITDA V.V`"
                    f" with company name + multiples pre-fetched from"
                    f" yfinance. Cite them verbatim, do not call any"
                    f" tool to refetch, do NOT substitute your own"
                    f" guess for the company name (MediaTek 2454.TW"
                    f" 2026-05-18: analysts mislabeled 2379.TW as 'ASE'"
                    f" / 'Advanced Semiconductor Engineering' instead"
                    f" of correct 'Realtek Semiconductor'; same defect"
                    f" for 3034.TW and 8299.TWO):\n"
                    f"  • 행 포맷: `티커 — 회사명 | PER X.X / Fwd PER Y.Y /"
                    f" PSR Z.Z / PBR W.W / EV/EBITDA V.V` (각 값에 라벨 부착)\n"
                    f"\n⛔ VERBATIM COPY 강제 (300750.SZ 2026-05-28 surfaced —"
                    f" 4개 분석가가 동시에 라벨을 제거하고 `/` → `—` 치환):"
                    f" 아래 peer 행들을 PER/Fwd PER/PSR/PBR/EV/EBITDA **라벨"
                    f" 포함** 그대로 출력. ❌ FORBIDDEN: (a) 라벨 제거 후"
                    f" `300750.SZ — 23.5 — 17.2 — 4.08 — 5.23 — 14.6` 같은"
                    f" 라벨 없는 dash-chain, (b) `/` 를 `—` 또는 `|` 로 임의"
                    f" 변환, (c) 행을 paraphrase 하거나 회사명 축약/번역."
                    f" 표를 '깔끔하게 정리' 하려는 본능을 차단할 것 — 위 행은"
                    f" 이미 정상 포맷이며, 그대로 복사하는 게 정답.\n"
                    + "\n".join(peer_lines) + "\n"
                    f"\n⛔ COMPS 출력 HEADER 의무 (319660.KS 2026-05-23"
                    f" surfaced, 2382.TW 2026-05-28 재발 — 시장/감정/뉴스"
                    f" 섹션에서 라벨 없는 — 체인 재출현): 이 규칙은 펀더멘털"
                    f" 뿐 아니라 **모든 분석가(시장/감정/뉴스/펀더멘털)**의"
                    f" '동종업계 비교 (Comps)' 섹션에 적용. 데이터 행 보다"
                    f" 먼저 헤더 행을 반드시 출력. 예시:\n"
                    f"  | 티커 | 회사명 | PER | Fwd PER | PSR | PBR | EV/EBITDA |\n"
                    f"  | --- | --- | --- | --- | --- | --- | --- |\n"
                    f"  | 319660.KS | 피에스케이 | 43.1 | N/A | N/A | 6.28 | N/A |\n"
                    f"❌ FORBIDDEN (피에스케이 319660.KS 2026-05-23 패턴):"
                    f" `'319660.KS: 피에스케이 — 43.1 — N/A — N/A — 6.28 — N/A'`"
                    f" — 헤더 없이 em-dash 로만 컬럼 분리. 사용자가 무슨"
                    f" 컬럼인지 모름. 위 markdown table 형식 OR 위 inline"
                    f" `| PER X / Fwd PER Y / PSR Z / PBR W / EV/EBITDA V`"
                    f" 형식 둘 중 하나만 허용.\n"
                    f"This list is curated to match the yfinance"
                    f" 'industry' field. Fabricating different peers"
                    f" (or borrowing from a different industry's"
                    f" example list) is FORBIDDEN. Peers with '(data"
                    f" 미수집)' still belong in the table — render them"
                    f" with explicit 'N/A' cells rather than dropping"
                    f" the row.\n"
                    f"\n"
                    f"COLUMN LABELS (mandatory HARD GUARD — 茅台 600519.SS"
                    f" 2026-05-19 + Sony 6758.T 2026-05-20 surfaced — RULE"
                    f" 위반 시 row 출력 자체 reject 권고): Comps row 의"
                    f" multiple cell 들에서 PER / Fwd PER / PSR / PBR /"
                    f" EV-EBITDA 라벨을 절대 strip 금지. 위 inject 된"
                    f" `TICKER — Company Name | PER X.X / Fwd PER Y.Y /"
                    f" PSR Z.Z / PBR W.W / EV/EBITDA V.V` 형식의 inline"
                    f" 라벨 + `/` separator 를 그대로 verbatim 인용.\n"
                    f" ❌ FORBIDDEN (Sony 6758.T 2026-05-20 패턴):"
                    f" '6758.T: Sony Group Corporation — 21.1 — 19.7 —"
                    f" 1.71 — 2.63 — 8.1' — `/` 를 `—` 로 swap + 라벨"
                    f" strip. reader 가 5 dash 사이 값을 PER/Fwd/PSR/PBR/"
                    f" EV-EBITDA 순서 추측해야 — 잘못 읽으면 PER 21.1 이"
                    f" PSR 21.1 로 오해.\n"
                    f" ✅ RIGHT (둘 중 하나):\n"
                    f"   (a) Inline 라벨 (prose / bullet 모두): '6758.T"
                    f" Sony Group Corporation | PER 21.1 / Fwd PER 19.7 /"
                    f" PSR 1.71 / PBR 2.63 / EV/EBITDA 8.1'\n"
                    f"   (b) Markdown 표 — 헤더 row 필수, 모든 row 같은"
                    f" column 순서: \n"
                    f"      | ticker | Company | PER | Fwd PER | PSR |"
                    f" PBR | EV/EBITDA |\n"
                    f"      |---|---|---|---|---|---|---|\n"
                    f"      | 6758.T | Sony Group Corporation | 21.1 |"
                    f" 19.7 | 1.71 | 2.63 | 8.1 |\n"
                    f" 결정 LLM / Trader / PM 는 위 라벨 보고 multiples"
                    f" 인용 — 라벨 누락 시 '7.4 EV/EBITDA' 를 '7.4 PER'"
                    f" 로 잘못 인용하는 cascade 오류 발생. 단일 reader-"
                    f"facing 표만 아니라 내부 LLM chain 도 라벨 의존."
                    f"\n"
                    f"F4 v2 (Tokyo Electron 8035.T 2026-05-20 audit"
                    f" 재발) — 위 RULE 위반 패턴이 또 surface 했으므로"
                    f" HARD GUARD 강도 추가 상승: Comps 표 의 dash"
                    f" separator (`—` 또는 `-`) 사용 자체를 cell 간"
                    f" 구분자로 FORBID. cell 구분은 (a) 마크다운 표의"
                    f" pipe `|` 또는 (b) inline 라벨 `PER X / Fwd PER Y"
                    f" / PSR Z` 의 slash `/` 만 허용. dash 가 cell 구분자"
                    f" 로 보이면 분석가 본인의 출력을 reject 하고 재"
                    f" 생성하라. 위반 시 'Comps multiples — dash"
                    f" separator forbidden, 라벨 보존 의무' 한 줄로"
                    f" 표 자체를 omit 하는 것이 dash format 보다 나음."
                    f"\n"
                    f"SUBJECT ROW POLICY (Rule E — applies to ALL analysts,"
                    f" not just fundamentals — TW TSMC 2026-05-18 +"
                    f" MediaTek 2026-05-18 surfaced this): when any"
                    f" analyst (시장 / 감정 / 뉴스 / 펀더멘털) renders a"
                    f" Comps table, the subject's '{ticker} (subject)'"
                    f" row above MUST be present AND use the multiples"
                    f" verbatim. Dropping the subject row entirely"
                    f" (MediaTek News Comps omitted 2454.TW row) is"
                    f" FORBIDDEN. Different analysts producing different"
                    f" multiples for the same stock in the same report"
                    f" (TSMC News PER 29.5 / PBR 3.51 vs Fundamentals"
                    f" PER 30.43 / PBR 9.86) is also FORBIDDEN — readers"
                    f" cannot tell which value is canonical. The single"
                    f" '(subject)' row above is THE canonical source for"
                    f" every analyst's Comps table."
                )
        except Exception:
            pass

    # Fix L (2026-05-23, FORM RSI hallucination 차단): Technical Indicators
    # Single Source of Truth. Pre-compute RSI(14)/MACD/Bollinger here so
    # ALL analysts share the same canonical values. Each analyst sees these
    # numbers in the context and MUST NOT produce different values.
    # No _section_allowed gate — every analyst must see the SSoT.
    _tech_snap = _compute_technical_snapshot(ticker)
    if _tech_snap:
        base += "\n\n" + _tech_snap

    # Fix K (2026-05-23, FORM US 7-axis): US 종목 분석에서 KR/JP/TW/CN
    # 매크로 인용 차단. FORM 감정 분석가가 "USD/KRW 환율 + BoK 금리" 를
    # US 종목 분석에 인용한 패턴 차단. Rule applies to all US analyses.
    if market == "US":
        base += (
            "\n\n=== US 종목 매크로 GATE (MANDATORY) ===\n"
            "이 분석 대상은 미국 상장 종목입니다. 아래 항목 인용 FORBIDDEN:\n"
            "  ❌ KR 매크로: USD/KRW 환율, BoK 기준금리, KOSPI/KOSDAQ,\n"
            "     KRX 수급 flow, 한국 CPI, 한국 GDP, 한국 수출입 지표.\n"
            "  ❌ JP 매크로: USD/JPY, BoJ 정책금리, Nikkei 225, TOPIX.\n"
            "  ❌ TW 매크로: USD/TWD, CBC 중취금리, TAIEX.\n"
            "  ❌ CN 매크로: USD/CNY, LPR, PMI, CSI 300 (예외: 중국 매출 비중\n"
            "     30%+ 명시 시 USD/CNY 1건만 허용).\n"
            "  ✅ ALLOWED: Fed Fund Rate, 美 10Y 국채, USD Index (DXY),\n"
            "     S&P 500, NASDAQ, VIX, WTI (에너지주), 미국 CPI/GDP/고용.\n"
        )

    # JP body-text directive — same shape as the KR one below. Readers
    # can't parse '7203.T' alone; force every analyst to surface a
    # human-readable company name in narrative sentences. For now we
    # use yfinance longName (typically English: 'Toyota Motor Corporation')
    # because EDINET's Japanese-name lookup isn't wired yet. Skip the
    # directive when longName == ticker (yfinance fallback) or missing.
    # yfinance occasionally tags J-REITs / JDR (8951.T 일본 빌딩펀드 등)
    # as MUTUALFUND despite being real listed entities; mirror the KR
    # override here so .T tickers don't cascade into fund mode unless
    # they're an actual ETF.
    if market == "JP" and qt in ("MUTUALFUND",) and long_name and ".T" in (ticker or "").upper():
        # Heuristic: real .T mutual funds don't have a sector/industry
        # in yfinance, while misclassified equities do. If sector is
        # populated, treat as equity.
        if sector and industry:
            qt = "EQUITY"

    # TW body-text directive — like JP, .TW / .TWO numeric tickers are
    # unreadable to non-specialists. yfinance longName usually contains
    # both 繁體中文 + English ('Taiwan Semiconductor Manufacturing Company
    # Limited' for TSMC, '鴻海精密工業股份有限公司' or 'Hon Hai Precision'
    # for 2317.TW). We require the analyst to surface a recognizable
    # name on first mention each section. ADR cross-listings (TSM ↔
    # 2330.TW, UMC ↔ UMC, AUO ↔ AUO) — when the analyst notices an ADR
    # exists, MENTION it once but keep all valuation in TWD on the .TW
    # listing (different time zone + currency, can't blend).
    if market == "TW" and long_name and long_name.upper() != (ticker or "").upper():
        base += (
            f"\n\n=== TW NAMING DIRECTIVE (MANDATORY) ===\n"
            f"When referring to the company in ANY narrative sentence,"
            f" use one of these forms — NEVER the bare numeric ticker:\n"
            f" • '{long_name} ({ticker})' on first mention per section\n"
            f" • '{long_name}' (name only) on subsequent mentions\n"
            f"❌ WRONG: '{ticker}는 최근 급락하며...'\n"
            f"✅ RIGHT: '{long_name} ({ticker})는 최근 급락하며...'\n"
            f"\nADR CROSS-LISTING NOTE: 다수의 TW 대형주가 NYSE ADR도"
            f" 발행 (TSMC 2330.TW ↔ TSM, UMC 2303.TW ↔ UMC, AUO 2409.TW"
            f" ↔ AUO). 분석에서 ADR 존재 자체는 한 줄 인지하되, 모든"
            f" 펀더멘털 / 시총 / 가격 비교는 .TW 본 상장 (TWD 기준)"
            f" 으로 통일. ADR과 .TW는 다른 시간대 + 다른 통화 + 다른"
            f" 유동성이라 multiples를 섞으면 reader가 혼동."
        )

    if market == "JP" and long_name and long_name.upper() != (ticker or "").upper():
        base += (
            f"\n\n=== JP NAMING DIRECTIVE (MANDATORY) ===\n"
            f"When referring to the company in ANY narrative sentence,"
            f" use one of these forms — NEVER the bare numeric ticker:\n"
            f" • '{long_name} ({ticker})' on first mention per section\n"
            f" • '{long_name}' (name only) on subsequent mentions\n"
            f"❌ WRONG: '{ticker}는 최근 급락하며...'\n"
            f"✅ RIGHT: '{long_name} ({ticker})는 최근 급락하며...'"
        )

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

        # CHAEBOL AUTO-DETECTION — RULE 9 enforcement (mandatory)
        # RULE 9 (CHAEBOL GROUP RISK) in fundamentals_analyst.py requires
        # the analyst to add one explicit line about group-level risks
        # for chaebol-affiliated names. The rule text alone gets silently
        # skipped — 두산 000150.KS 2026-05-18 had no chaebol risk line
        # despite being 두산그룹 지주회사. Detect chaebol prefix from
        # DART corp_name and inject a mandatory directive so the analyst
        # cannot route around RULE 9.
        # Prefix list matches CLAUDE.md '## Per-ticker reviews' RULE 9
        # exactly: top 15 KR chaebols by 공정거래법 자산 ranking.
        _CHAEBOL_PREFIXES = (
            "삼성", "현대", "SK", "LG", "한화", "롯데", "GS", "CJ",
            "두산", "KT", "효성", "신세계", "포스코", "한진", "영풍",
        )
        chaebol_match = next(
            (p for p in _CHAEBOL_PREFIXES if kr_name.startswith(p)), None,
        )
        if chaebol_match:
            base += (
                f"\n\n=== CHAEBOL GROUP RISK DIRECTIVE (MANDATORY — RULE 9) ===\n"
                f"{kr_name} ({ticker})는 {chaebol_match}그룹 계열사 / 지주회사로"
                f" 분류된다. fundamentals_analyst.py의 RULE 9에 따라, 결론"
                f" (Conclusion / 6번 섹션) 에 반드시 그룹-level 리스크 한 줄을"
                f" 포함해야 한다. 다음 중 적용 가능한 변수 최소 1개를 명시:\n"
                f"  • 그룹 지배구조 개편 / 승계 절차 진행 상황\n"
                f"  • 그룹 계열사 간 상호채무보증 / 순환출자 변경\n"
                f"  • 정부의 그룹 차원 압박 (공정위 / 국세청 / 금감원 조사)\n"
                f"  • 그룹 발 ESG 이슈 (계열사 사고 / 노조 / 환경)\n"
                f"  • 그룹 핵심 계열사 (예: {chaebol_match}전자 / {chaebol_match}중공업)"
                f" 의 실적 변동이 지주회사 가치에 미치는 영향\n"
                f"두산 000150.KS 2026-05-18 같이 분기 실적 + 부채비율만 다루고"
                f" 그룹 차원 변수를 0줄로 처리하는 패턴 금지 — 단일 종목"
                f" 펀더멘털과 무관하게 그룹 발 변수가 5거래일 가격을 흔들 수"
                f" 있는 게 chaebol 패턴의 정의다."
            )

    # US naming directive — soft variant. US tickers like AAPL / NVDA are
    # recognizable on their own, but some symbols (SNDK = SanDisk, AVGO =
    # Broadcom, BRK-B = Berkshire Hathaway, GOOGL = Alphabet) do not
    # surface the company name to a non-finance reader without help.
    # Inject a SOFT directive (recommendation, not mandatory) — for the
    # tickers where yfinance returns a clearly different longName, prefer
    # "{Company} ({TICKER})" form on first mention per section. KR/JP
    # versions stay MANDATORY because numeric tickers are unreadable.
    if market == "US" and long_name and long_name.upper() != (ticker or "").upper():
        base += (
            f"\n\n=== US NAMING DIRECTIVE (recommended) ===\n"
            f"yfinance longName: '{long_name}'. When this differs"
            f" meaningfully from the ticker symbol (e.g. SNDK / SanDisk,"
            f" AVGO / Broadcom, GOOGL / Alphabet), prefer"
            f" '{long_name} ({ticker})' on first mention per section so"
            f" readers can place the company without context. Subsequent"
            f" mentions can use either form."
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
            f" awkward '백만' unit — both forbidden.\n"
            f" • 기술 분석 표 (10 EMA / 50 SMA / Close Price / RSI 입력값)"
            f" 및 narrative 본문 의 '현재가' / '현재 가격' / 'Close Price'"
            f" 셀도 같은 canonical 값 ({_sym}{_fmt.format(px)}) 사용. yfinance"
            f" .history Close (전일 종가) vs .info currentPrice (latest"
            f" intraday) 가 미세 다른 케이스 (Sony 6758.T 2026-05-20:"
            f" 시장 분석가 본문에 '¥3,720.00' 와 '¥3,615.00' 두 다른 가격"
            f" 동시 인용) 가 RULE 위반 — 본 분석가 보고서 시작부터 끝까지"
            f" 단일 canonical 가격만 인용. 시점 다른 가격 (5일 전, 4주 전"
            f" 등) 명시 시에는 시점 라벨 ('5일 전 종가 ...', '4주 전 high"
            f" ...') 동반 의무, 시점 라벨 없는 가격은 canonical 외 금지."
        )

        # Canonical market cap (Rule B). Without this, each analyst
        # computed (or fabricated) its own — Toyota 7203.T 2026-05-18:
        # 펀더멘털 ¥38.40조 (yfinance ground truth) vs 감정·뉴스 ¥45조
        # (LLM 환각). Same divergence pattern showed up in earlier KR
        # runs. Inject the yfinance marketCap as canonical with proper
        # native-unit formatting per market so every analyst quotes the
        # same number. Currency follows _cfg: KRW → 조 원, JPY → 兆 円
        # / 조 엔, USD → $XB.
        market_cap = info.get("marketCap")
        if isinstance(market_cap, (int, float)) and market_cap > 0:
            _currency = _cfg.get("currency", "USD")
            if _currency == "KRW":
                # KRW values are large; render as 조 원 (≥1조) or 억 원.
                if market_cap >= 1e12:  # ≥1조
                    mc_native = f"약 {market_cap / 1e12:,.2f}조 원"
                else:
                    mc_native = f"약 {market_cap / 1e8:,.0f}억 원"
            elif _currency == "JPY":
                # JPY same scale convention but with 조/억 엔 (Korean
                # output) — analyst's report is in Korean per JP
                # currency directive.
                if market_cap >= 1e12:
                    mc_native = f"약 {market_cap / 1e12:,.2f}조 엔"
                else:
                    mc_native = f"약 {market_cap / 1e8:,.0f}억 엔"
            elif _currency == "TWD":
                # TWD scale: 兆 元 (≥1兆 = 10^12), 億 元 (1억 ~ 1兆),
                # 万 元 (1만 ~ 1억). TWD-scale TSMC ~NT$58T = 約 58兆 元.
                # User's TW analysis 2026-05-18 surfaced this — sentiment
                # said '약 25兆 元' while fundamentals said '약 58조 元'
                # for the same stock because this branch was missing and
                # TWD fell through to the USD '$XB' format which the LLM
                # ignored, computing its own divergent value.
                if market_cap >= 1e12:
                    mc_native = f"약 {market_cap / 1e12:,.2f}兆 元 (NT${market_cap / 1e12:,.1f}T)"
                else:
                    mc_native = f"약 {market_cap / 1e8:,.0f}億 元 (NT${market_cap / 1e9:,.1f}B)"
            elif _currency == "CNY":
                # CNY same scale as TWD — 亿元 / 兆元. Phase 4-CN will
                # exercise this branch when CN_A tickers ship; pre-wired
                # here so the TW + CN fixes land in one commit.
                if market_cap >= 1e12:
                    mc_native = f"약 {market_cap / 1e12:,.2f}兆元 (¥{market_cap / 1e12:,.1f}T)"
                else:
                    mc_native = f"약 {market_cap / 1e8:,.0f}亿元 (¥{market_cap / 1e9:,.1f}B)"
            elif _currency == "HKD":
                # HKD scale: HK$ + B/M (similar to USD). HK market caps
                # typically rendered in $billions. Tencent ~HK$3.8T,
                # Alibaba ~HK$1.8T at recent peaks.
                if market_cap >= 1e12:
                    mc_native = f"약 HK${market_cap / 1e12:,.2f}T"
                elif market_cap >= 1e9:
                    mc_native = f"약 HK${market_cap / 1e9:,.2f}B"
                else:
                    mc_native = f"약 HK${market_cap / 1e6:,.1f}M"
            else:
                # USD / fallback: $XB / $XM.
                if market_cap >= 1e9:
                    mc_native = f"${market_cap / 1e9:,.2f}B"
                elif market_cap >= 1e6:
                    mc_native = f"${market_cap / 1e6:,.1f}M"
                else:
                    mc_native = f"${market_cap:,.0f}"
            base += (
                f"\n\nCanonical market cap (yfinance .info marketCap,"
                f" point-in-time — use this single value verbatim for"
                f" any '시가총액 / market cap' reference; do NOT compute"
                f" your own from price × shares or quote a different"
                f" number from memory): {mc_native}\n"
                f"같은 종목 한 보고서에서 분석가들이 서로 다른 시총을 인용하는"
                f" 패턴 (Toyota 2026-05-18: 펀더멘털 ¥38.40조 vs 감정·뉴스"
                f" ¥45조 / 현대차증권 001500.KS 2026-05-19: 감정+시장 8,840억"
                f" vs 펀더멘털 6,542억) 방지가 목적이다. 위 값을 모든 섹션"
                f" (시장 / 감정 / 뉴스 / 펀더멘털 / 결정) 에서 동일하게"
                f" 사용하라.\n"
                f"❌ 위반 패턴 (RULE 위반): 본인이 다른 source 에서 fetch 한"
                f" 시총 (예: 펀더멘털 분석가가 marketCap field 를 다시 호출"
                f" 해서 받은 값 / 다른 시점의 cached 값) 을 인용. 분석가"
                f" 사이에 시총이 ±1% 이상 차이가 나면 reader 가 둘 다"
                f" 신뢰할 수 없다고 판단.\n"
                f"✅ 정답: 위 canonical 값 ({mc_native}) 만 인용. fresh"
                f" fetch / recompute / paraphrase 모두 금지."
            )

        # D1 (2026-05-19): KR 종목 pykrx 시총 cross-check. yfinance .info
        # marketCap 가 일부 KR 종목에 stale / corrupt / missing 인 경우
        # KRX 공식 데이터로 cross-check. 10% 이상 차이 시 ⚠️ alert,
        # yfinance missing 시 pykrx 값을 canonical 로 사용 (override).
        if market == "KR":
            try:
                from bot.pykrx_client import get_kr_market_cap as _d1_gmc
                pykrx_mc_data = _d1_gmc(ticker)
                if pykrx_mc_data and pykrx_mc_data.get("market_cap"):
                    pykrx_mc = pykrx_mc_data["market_cap"]
                    pykrx_close = pykrx_mc_data.get("close", 0)
                    pykrx_date = pykrx_mc_data.get("date", "")
                    # KRW 라 일관 단위 (직접 비교 OK)
                    if isinstance(market_cap, (int, float)) and market_cap > 0:
                        # cross-check
                        diff = abs(pykrx_mc - market_cap) / market_cap
                        if diff > 0.10:
                            base += (
                                f"\n\n⚠️ D1 시가총액 cross-check 불일치"
                                f" (yfinance vs KRX):\n"
                                f"  • yfinance: 약 {market_cap / 1e8:,.0f}억 원\n"
                                f"  • KRX (pykrx, {pykrx_date}):"
                                f" 약 {pykrx_mc / 1e8:,.0f}억 원\n"
                                f"  • 차이: {diff*100:.1f}% (10% 이상)\n"
                                f"KRX 값이 공식 — KRX 값을 우선 인용 권고."
                                f" yfinance 값은 분석에 인용하지 말고 KRX 값"
                                f" (약 {pykrx_mc / 1e8:,.0f}억 원) 만 사용."
                            )
                    else:
                        # yfinance missing → pykrx override
                        base += (
                            f"\n\n✅ D1 시가총액 pykrx fallback (yfinance"
                            f" missing): KRX 공식 시총 약 {pykrx_mc / 1e8:,.0f}억"
                            f" 원 ({pykrx_date} 기준). 본 값을 모든 섹션"
                            f" canonical 시총으로 사용."
                        )
                    # pykrx close vs yfinance currentPrice cross-check.
                    # Fix ③ (2026-05-23, 외부 검증자 제언): 단순 경고가
                    # 아닌 divergence 수준에 따라 severity 를 2단계로 분리.
                    # 15% 이상 → CRITICAL (수정주가 미반영 의심, PM HOLD 강제).
                    # 1% ~ 15% → 기존 minor ⚠️ 유지. Rule applies to all
                    # analyses going forward.
                    if (isinstance(px, (int, float)) and px > 0
                            and pykrx_close > 0):
                        px_diff = abs(pykrx_close - px) / px
                        if px_diff > 0.15:
                            # CRITICAL — corp action 당일~수일 내 수정주가
                            # 미반영 패턴. 319660.KS 같은 분할 직후 케이스.
                            info["_price_critical_divergence"] = {
                                "src1": "yfinance",
                                "px1": px,
                                "src2": f"KRX ({pykrx_date})",
                                "px2": pykrx_close,
                                "diff_pct": px_diff * 100,
                            }
                            base += (
                                f"\n\n🔴 PRICE CRITICAL DIVERGENCE —"
                                f" 가격 데이터 source 간 {px_diff*100:.1f}%"
                                f" 괴리 감지 (Fix ③):\n"
                                f"  • yfinance: ₩{px:,.0f}\n"
                                f"  • KRX close ({pykrx_date}):"
                                f" ₩{pykrx_close:,}\n"
                                f"수정주가(split-adjusted) 미반영 의심 —"
                                f" 모든 기술 지표(SMA/EMA/MACD/RSI/볼린저)"
                                f" INVALID. valuation(PER/PBR) cite 보류.\n"
                                f"⛔ PM 결론 강제: 데이터 정합성 확인 완료 전"
                                f" 매수/매도 금지. 최종 권고를 반드시 HOLD"
                                f" (보유/관망) 로 고정할 것."
                            )
                        elif px_diff > 0.01:
                            base += (
                                f"\n\n⚠️ D1 현재가 cross-check 불일치"
                                f" (yfinance vs KRX):\n"
                                f"  • yfinance currentPrice: ₩{px:,.0f}\n"
                                f"  • KRX close ({pykrx_date}):"
                                f" ₩{pykrx_close:,}\n"
                                f"  • 차이: {px_diff*100:.2f}%\n"
                                f"yfinance 가 intraday 가격 lag 또는 corp"
                                f" action 영향 가능. KRX 종가가 공식 close"
                                f" — 분석에서 ₩{pykrx_close:,} 사용 권고."
                            )
            except Exception as exc:
                _analyst_log.warning(
                    "D1 pykrx cross-check failed for %s: %s", ticker, exc,
                )

        # Canonical 50일 / 200일 SMA (Rule F, 2026-05-19 SMIC 688981.SS
        # surfaced). Same shape as canonical price + market cap above
        # but for the moving averages. yfinance fiftyDayAverage /
        # twoHundredDayAverage point-in-time values — analysts were
        # producing slightly different SMA values (시장 50d ¥107 vs
        # 펀더멘털 ¥107.49 미세 불일치) because each analyst's tool
        # call fetched at slightly different timestamps. Inject the
        # single canonical value so every section quotes the same.
        sma50 = info.get("fiftyDayAverage")
        sma200 = info.get("twoHundredDayAverage")
        # D1 Phase 3 (307950.KS 2026-05-23): yfinance .info 가 SMA / 52w
        # 를 비워서 반환하는 KR 종목 — pykrx OHLCV 시계열로 직접 계산해
        # fallback. 빈 자리를 LLM 이 채우려고 fabrication 시도하는 패턴
        # (PM "PER 94.8" case) 차단. Same pattern for fiftyTwoWeek* below.
        if market == "KR":
            try:
                from bot.pykrx_client import get_kr_ohlcv_stats as _d1_sma
                ohlcv_stats = _d1_sma(ticker)
            except Exception:
                ohlcv_stats = None
            if ohlcv_stats:
                if (not isinstance(sma50, (int, float)) or not sma50 or sma50 != sma50):
                    if ohlcv_stats.get("sma50"):
                        sma50 = ohlcv_stats["sma50"]
                if (not isinstance(sma200, (int, float)) or not sma200 or sma200 != sma200):
                    if ohlcv_stats.get("sma200"):
                        sma200 = ohlcv_stats["sma200"]
        if isinstance(sma50, (int, float)) and sma50 > 0 \
                and isinstance(sma200, (int, float)) and sma200 > 0:
            base += (
                f"\n\nCanonical 50일 / 200일 SMA (yfinance, point-in-time"
                f" — 모든 섹션이 이 단일 값을 인용; 시장 / 펀더멘털 /"
                f" 결정 노드 사이 SMA 값 mismatch 금지):\n"
                f"  • 50일 SMA: {_sym}{_fmt.format(sma50)}\n"
                f"  • 200일 SMA: {_sym}{_fmt.format(sma200)}\n"
                f"같은 보고서에서 분석가들이 서로 다른 SMA 값 (예:"
                f" 시장 50d ¥106.99 vs 펀더멘털 ¥107.49 — SMIC"
                f" 688981.SS 2026-05-19 / 시장 ₩10,953.90 vs 펀더멘털"
                f" ₩11,089.20 — 현대차증권 001500.KS 2026-05-19) 을"
                f" 인용하는 패턴 방지가 목적이다.\n"
                f"❌ 위반 패턴 (RULE 위반): get_indicators / get_stock_data"
                f" tool 호출 결과의 SMA 값을 위 canonical 대신 인용."
                f" Tool 결과는 다른 시점 / 다른 lookback window 일 가능성"
                f" 큼 — canonical 값 (yfinance .info 의 fiftyDayAverage /"
                f" twoHundredDayAverage 단일 fetch) 만 인용.\n"
                f"✅ 정답: tool 이 fetch 한 SMA 가 위 canonical 과 다르면"
                f" 무시하고 canonical 만 인용. fresh fetch / recompute"
                f" 모두 금지. ±1% 이상 차이는 RULE 위반.\n"
                f"F6 (Tokyo Electron 8035.T 2026-05-20 재발 — 시장 50d"
                f" ¥43,119.97 / 200d ¥34,153.87 vs 펀더 50d ¥43,733.00"
                f" / 200d ¥34,941.75, 1.4-2.3% 차이): RULE 강화 — 시장"
                f" 분석가 의 기술 분석 본문에서 SMA 인용 시 위 canonical"
                f" 만 사용. get_indicators tool 호출 결과가 위 값과"
                f" 다르면 tool 결과 무시, '시장 50일 SMA: {_sym}{_fmt.format(sma50)}"
                f" (canonical), tool 호출 결과는 다른 lookback' 형식으로"
                f" 명시. 펀더 / 결정 노드도 동일 — 같은 보고서 안에서"
                f" SMA 값 두 번 인용되면 둘이 반드시 일치."
            )

        # Fix C: 52주 최고 / 최저 sanity (2026-05-19 현대차증권 001500.KS
        # surfaced). yfinance 가 일부 종목에서 fiftyTwoWeekLow=0 또는
        # nan 반환 — 분석가가 비판 없이 '52주 최저: ₩0' 그대로 인용하는
        # silent fail 패턴. 어떤 종목도 ₩0 가 historical low 가 될 수
        # 없으므로 currentPrice 의 1% 미만이면 데이터 corrupt 로 판단.
        wk_high = info.get("fiftyTwoWeekHigh")
        wk_low = info.get("fiftyTwoWeekLow")
        # D1 Phase 3: KR 종목 yfinance fiftyTwoWeek* missing 시 pykrx
        # OHLCV 252-day window 로 fallback. 307950.KS 2026-05-23: 펀더
        # 표 '52주 최고/최저: N/A / N/A' 그대로 노출 → fabrication 유발.
        if market == "KR":
            try:
                from bot.pykrx_client import get_kr_ohlcv_stats as _d1_52w
                ohlcv_stats = _d1_52w(ticker)
            except Exception:
                ohlcv_stats = None
            if ohlcv_stats:
                if (not isinstance(wk_high, (int, float))
                        or not wk_high or wk_high != wk_high
                        or wk_high <= 0):
                    if ohlcv_stats.get("wk_high"):
                        wk_high = ohlcv_stats["wk_high"]
                if (not isinstance(wk_low, (int, float))
                        or not wk_low or wk_low != wk_low
                        or wk_low <= 0):
                    if ohlcv_stats.get("wk_low"):
                        wk_low = ohlcv_stats["wk_low"]
        wk_corrupt: list[str] = []
        if isinstance(wk_low, (int, float)) and wk_low >= 0:
            # ₩0 / nan / currentPrice 의 1% 미만 (current 의 100분의 1
            # 보다 작은 historical low 는 split 등 corp action 외 불가)
            if wk_low == 0 or (isinstance(px, (int, float)) and px > 0
                                and wk_low < px * 0.01):
                wk_corrupt.append(f"52주 최저: {_sym}{_fmt.format(wk_low)}")
            # Fix H (2026-05-19 노바렉스 194700.KS surfaced): 52주 최저가
            # 가 현재가보다 클 수 없음 (자동 갱신 되어야 함). 만약 52w
            # low > current 면 yfinance 데이터 corruption 또는 corp
            # action 진행 중 + historical series stale. 노바렉스 케이스:
            # current ₩10,140 vs 52w low ₩11,500 — 현재가가 52주 최저
            # 보다 약 12% 낮음. 자동 detect.
            if (isinstance(px, (int, float)) and px > 0
                    and wk_low > px * 1.005):  # 0.5% 버퍼 (intraday tick)
                wk_corrupt.append(
                    f"52주 최저 {_sym}{_fmt.format(wk_low)} > 현재가"
                    f" {_sym}{_fmt.format(px)} (어떤 종목도 current <"
                    f" 52w low 불가능 — 데이터 stale 또는 corp action 의심)"
                )
        if isinstance(wk_high, (int, float)) and isinstance(px, (int, float)):
            # 52주 최고가 < currentPrice 면 일반적으로 불가 (current 가
            # 새 52주 최고가 갱신 case 만 valid — 그러나 일반적으로
            # yfinance 자동 갱신).
            if wk_high > 0 and wk_high < px * 0.99:
                wk_corrupt.append(
                    f"52주 최고 {_sym}{_fmt.format(wk_high)} < 현재가"
                    f" {_sym}{_fmt.format(px)}"
                )
        if wk_corrupt:
            base += (
                f"\n\n⚠️ 52주 최고/최저 데이터 의심 (yfinance silent fail):\n"
                + "\n".join(f"  • {c}" for c in wk_corrupt)
                + f"\n❌ HARD RULE: 위 corrupt 값을 요약표·본문·DCF 입력 어디에도"
                f" 인용 금지. '52주 최고/최저: {_sym}266,500 / {_sym}0' 같은"
                f" 표기 절대 금지 (LG전자 2026-05-22 펀더멘털 사례). 요약표"
                f" 해당 행 전체를 '52주 변동폭 데이터 미수집 / 검증 보류' 한"
                f" 줄로 대체하라. Narrative 인용도 동일하게 금지."
            )

        # Revenue / market cap sanity (Rule G, 2026-05-19 SMIC surfaced).
        # yfinance .info totalRevenue 가 일부 .SS / .SZ / .HK 종목에서
        # 단위 오류 (백만 ¥ 보고 vs 억 ¥ 보고) 발생. SMIC 688981.SS
        # 케이스: yfinance totalRevenue 93.27억 ¥ — 실제 매출 ~580-700
        # 억 ¥, 1Q 단독 매출이 176억 ¥ 라는 뉴스와 충돌 (분기 > 연간
        # 불가능 → 단위 mismatch 확정). 분기-연간 sanity 가 LLM 한테
        # 강하게 directive 되어야 fundamental multiples 의존 분석
        # 차단됨.
        revenue = info.get("totalRevenue")
        if (revenue and market_cap
                and isinstance(revenue, (int, float))
                and revenue > 0):
            ratio = revenue / market_cap
            # 일반 종목 ratio 0.02-0.30 범위 (NVDA 0.02 / TSLA 0.07 /
            # AAPL 0.11 / JPM 0.20). 0.015 이하 = 매출이 시총의 1.5%
            # 미만 (yfinance 단위 보고가 ~10x 작은 케이스 또는 극단적
            # 高PSR 종목). 20 초과 = 매출이 시총의 20배 초과 (yfinance
            # 단위 100x 큰 케이스, 매우 드뭄). 양방향 모두 sanity flag.
            # SMIC 688981.SS 2026-05-19: ratio = 0.0099 → catch (낮은 쪽).
            # 현대차증권 001500.KS 2026-05-19: ratio = 154,321억 ÷ 6,542억
            # = 23.6 → catch (높은 쪽). yfinance .info totalRevenue 가
            # TTM 단위로 보고했는데 FY annual 보다 ~47x 큰 케이스.
            # 이전 threshold (> 100) 가 너무 wide 라 23.6 통과 시킴.
            if ratio < 0.015 or ratio > 20:
                direction = (
                    "매출이 시총보다 작음 (yfinance 단위 100x 작게 보고 의심"
                    " 또는 高PSR 종목 — SMIC 패턴)"
                    if ratio < 0.015
                    else "매출이 시총보다 큼 (yfinance TTM 단위 잘못 또는"
                         " 분기-연간 mix 의심 — 현대차증권 패턴)"
                )
                base += (
                    f"\n\n⚠️ yfinance 매출 단위 의심 (HARD GUARD —"
                    f" Rule G):\n"
                    f"yfinance totalRevenue / marketCap 비율이 비정상"
                    f" ({ratio:.4f}). 일반 종목 0.02-0.30 범위 outside.\n"
                    f"방향: {direction}\n"
                    f"가능한 원인:\n"
                    f"  (a) yfinance 가 매출을 다른 단위 (백만 / 억 / TTM"
                    f" vs FY annual) 로 보고 — KR / CN_A / HK 종목 빈도"
                    f" 높음\n"
                    f"  (b) 회사가 최근 corporate action (대량 자본 변동)"
                    f" 후 시총 ↔ 매출 일시 mismatch\n"
                    f"  (c) TTM 매출 vs FY annual 매출 단위 mix 보고\n"
                    f"분석에서 다음 금지:\n"
                    f"  ❌ yfinance 매출 / PSR / EV-EBITDA / Comps의"
                    f" 매출 기반 multiples 그대로 인용 (잘못된 단위"
                    f" 기반 = 모두 misleading)\n"
                    f"  ❌ '매출 X 억 ¥' 단정 표기 (단위 확정 안 됨)\n"
                    f"  ❌ TTM 매출이 FY annual 매출보다 ~5배 이상 크면"
                    f" (현대차증권 케이스 47x) TTM 값 그대로 narrative 인용"
                    f" 금지 — FY annual 값만 인용\n"
                    f"올바른 처리:\n"
                    f"  ✅ TTM vs FY annual cross-check. 'TTM 매출 > FY"
                    f" annual × 5' 모순 발생 시 단위 mismatch 확정.\n"
                    f"  ✅ 분기 매출 (뉴스 / AKShare / Bloomberg 등 외부)"
                    f" 과 cross-check. '1Q 매출 > 연간 매출' 모순도 마찬가지.\n"
                    f"  ✅ 결론에 '매출 단위 데이터 mismatch — 펀더멘털"
                    f" multiples 평가 보류, 다음 보고 주기 데이터 대기'"
                    f" 한 줄 명시. SMIC 688981.SS 2026-05-19 (ratio 0.0099"
                    f" / SMIC 패턴), 현대차증권 001500.KS 2026-05-19"
                    f" (ratio 23.6 / 현대차 패턴) 정확히 이 패턴."
                )

                # D1 Phase 2 (2026-05-19): Rule G 발화 시 DART 정규화
                # 재무 데이터 자동 fetch + override path. StandardView
                # (StanLee5767, 라이선스 동의) 의 DART 계정과목 정규화
                # 패턴 차용. yfinance 의 corrupt 값 대신 DART 의 KRW
                # 단위 정확한 데이터 inject — 분석가가 DART 값 사용 가능.
                if market == "KR":
                    try:
                        from bot.dart_client import get_dart as _get_dart
                        dart_fin = _get_dart().get_normalized_financials(ticker)
                        if dart_fin:
                            f = dart_fin.get("financials") or {}
                            r = dart_fin.get("ratios") or {}
                            yr = dart_fin.get("year")
                            override_lines = [
                                f"\n\n✅ DART 정규화 재무 데이터 (D1 Phase 2"
                                f" — yfinance 단위 mismatch 대체용, FY{yr}"
                                f" {dart_fin.get('fs_div')} K-IFRS):\n"
                                f"분석가는 위 ⚠️ yfinance corrupt 데이터"
                                f" 대신 다음 DART 정규화 값 사용 권고:"
                            ]
                            for key in (
                                "매출", "영업이익", "당기순이익", "자산총계",
                                "부채총계", "자본총계", "유동자산", "유동부채",
                            ):
                                v = f.get(key)
                                if isinstance(v, (int, float)) and v != 0:
                                    if abs(v) >= 1e12:
                                        v_str = f"{v / 1e12:,.2f}조 원"
                                    elif abs(v) >= 1e8:
                                        v_str = f"{v / 1e8:,.0f}억 원"
                                    elif abs(v) >= 1e4:
                                        v_str = f"{v / 1e4:,.0f}만 원"
                                    else:
                                        v_str = f"{v:,.0f}원"
                                    override_lines.append(f"  • {key}: {v_str}")
                            eps = f.get("EPS")
                            if isinstance(eps, (int, float)) and eps != 0:
                                override_lines.append(f"  • EPS: ₩{eps:,.0f}")
                            for key in (
                                "영업이익률", "순이익률", "ROE", "ROA",
                                "부채비율", "유동비율",
                            ):
                                v = r.get(key)
                                if isinstance(v, (int, float)):
                                    override_lines.append(
                                        f"  • {key}: {v:+.2f}%"
                                    )
                            v = r.get("이자보상배율")
                            if isinstance(v, (int, float)):
                                override_lines.append(
                                    f"  • 이자보상배율: {v:+.2f}배"
                                )
                            override_lines.append(
                                f"\n출처: DART fnlttSinglAcntAll.json (FY{yr}"
                                f" 사업보고서) 의 K-IFRS 표준 계정과목 정규화."
                                f" KRW 절대값 정확. yfinance 의 totalRevenue"
                                f" 단위 mismatch 시 위 값으로 override."
                            )
                            base += "\n".join(override_lines)
                    except Exception as exc:
                        _analyst_log.warning(
                            "DART normalized financials override failed for"
                            " %s: %s", ticker, exc,
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
        # Fix N (2026-05-24, 현대모비스 012330.KS 외부 검증 surface):
        # 이격도 단독으로 HARD GUARD 발동하면 진짜 급등 (short squeeze /
        # 호재 catalyst / M&A 등) 도 "데이터 transitional" 로 오인식하여
        # 매매 기회를 놓침. 진짜 corp action / 거래정지 / shares 정합성
        # 위반이면 외부 source 가 evidence 를 줌. 외부 evidence 없이
        # 이격도만으로 분석 포기는 over-defensive. Multi-signal
        # confirmation 으로 변경 — universal (US/KR/JP/TW/CN/HK 공통).
        sma_gap_signals: list[str] = []
        sma_gap_info: dict | None = None
        for window_label, key in [("50일 SMA", "fiftyDayAverage"),
                                  ("200일 SMA", "twoHundredDayAverage")]:
            sma = info.get(key)
            if not (isinstance(sma, (int, float)) and sma > 0):
                continue
            gap = abs(px - sma) / sma
            if gap <= 0.30:
                continue
            sma_gap_info = {
                "window_label": window_label,
                "gap": gap,
                "sma": sma,
                "direction": "below" if px < sma else "above",
            }
            break  # first window above threshold is enough

        if sma_gap_info:
            # Collect external evidence of a real data-quality issue.
            # Any single confirming signal → HARD GUARD. None → SOFT
            # WARNING (technical indicators still cite-able, but flag
            # overbought/oversold risk).
            try:
                _yf_ca = _detect_yf_corp_action(ticker, lookback_days=14)
                if _yf_ca:
                    sma_gap_signals.append(
                        f"yfinance .splits ex-date: {_yf_ca.get('date')} "
                        f"({_yf_ca.get('event')})"
                    )
            except Exception:
                pass
            # shares × price vs marketCap divergence — universal data
            # integrity check. yfinance occasionally serves a stale
            # marketCap (computed from pre-split shares) while shares
            # / price are post-split. If |implied_mc - reported_mc|
            # / reported_mc > 5% → strong evidence of corp action lag.
            try:
                _mc_chk = info.get("marketCap")
                _sh_chk = info.get("sharesOutstanding")
                if (isinstance(_mc_chk, (int, float)) and _mc_chk > 0
                        and isinstance(_sh_chk, (int, float)) and _sh_chk > 0
                        and isinstance(px, (int, float)) and px > 0):
                    _implied = _sh_chk * px
                    if abs(_implied - _mc_chk) / _mc_chk > 0.05:
                        sma_gap_signals.append(
                            f"shares × price ({_sym}{_fmt.format(_implied)}) "
                            f"vs reported marketCap ({_sym}{_fmt.format(_mc_chk)}) "
                            f"divergence > 5%"
                        )
            except Exception:
                pass
            # KRX 시장경보 / 거래정지 — KR 한정. prefetched 가 함수 scope
            # 에서 사용 가능한 시점 (KR branch 안) 이면 활용, 아니면 skip.
            try:
                if market == "KR":
                    from bot.krx_alert_client import get_krx_alert
                    _krx = get_krx_alert().get_status(ticker)
                    if _krx and (_krx.get("alert_type") or _krx.get("halted")):
                        sma_gap_signals.append(
                            f"KRX 시장경보 / 거래정지: "
                            f"{_krx.get('alert_type') or '거래정지'}"
                        )
            except Exception:
                pass

            _gap_pct = sma_gap_info["gap"] * 100
            _dir = sma_gap_info["direction"]
            _wl = sma_gap_info["window_label"]
            _sma = sma_gap_info["sma"]

            if sma_gap_signals:
                # External evidence present → HARD GUARD (existing behavior)
                base += (
                    f"\n\n=== ⛔ PRICE GAP SANITY (HARD GUARD — Fix J/N 강화) ===\n"
                    f"current price {_sym}{_fmt.format(px)} is {_gap_pct:.0f}%"
                    f" {_dir} the {_wl} {_sym}{_fmt.format(_sma)},"
                    f" AND 다음 외부 신호가 동반:\n"
                )
                for _sig in sma_gap_signals:
                    base += f"  ⛔ {_sig}\n"
                base += (
                    "\n이는 stock-split adjustment lag / corp action /"
                    " 거래정지 등의 데이터 quality 문제로 확정.\n\n"
                    "다음 사용 절대 금지 (RULE 위반):\n"
                    "  ❌ 10 EMA / 50 SMA / 200 SMA 비교 또는 cross 신호 인용\n"
                    "  ❌ MACD / RSI / Bollinger 밴드 / ATR 기반 매수/매도"
                    " 신호 narrative\n"
                    "  ❌ '단기 상승 모멘텀' / '하락 추세 지속' / '과매수/"
                    "과매도' 같은 directional 톤 결론\n\n"
                    "올바른 처리:\n"
                    f"  ✅ canonical current price ({_sym}{_fmt.format(px)})"
                    " 한 줄 인용 + '데이터 transitional, 기술 지표 분석"
                    " 보류' 명시\n"
                    "  ✅ 펀더멘털 지표 (매출 / 영업이익 / 부채비율 등) 만"
                    " 분석 진행"
                )
            else:
                # No external evidence — genuine momentum / catalyst-driven
                # move. SOFT WARNING only: flag overbought/oversold risk
                # but don't ban technical indicator analysis. 현대모비스
                # 012330.KS 2026-05-24 surfaced: 28일 사이 +50% 정상 급등
                # 인데 HARD GUARD 가 발화해 분석가가 분석 포기.
                base += (
                    f"\n\n=== ⚠️ PRICE GAP NOTICE (SOFT — Fix N 신설) ===\n"
                    f"current price {_sym}{_fmt.format(px)} is {_gap_pct:.0f}%"
                    f" {_dir} the {_wl} {_sym}{_fmt.format(_sma)}.\n"
                    "외부 corp action / 거래정지 / shares 정합성 신호 없음"
                    " — 실제 강한 추세 (호재 catalyst / short squeeze /"
                    " M&A 기대 등) 일 가능성이 높음. 데이터 오류로 결론짓지"
                    " 말 것.\n\n"
                    "권고 처리:\n"
                    "  ✅ 기술 지표 (RSI / MACD / 볼린저 / EMA) 정상 인용"
                    " 허용\n"
                    f"  ⚠️ 단 {_gap_pct:.0f}% 이격은 통계적으로 비정상 —"
                    " overbought (above) 또는 oversold (below) 위험 명시"
                    " + 5거래일 단기 mean-reversion 가능성 고려\n"
                    "  ⚠️ 가능하면 catalyst (실적 / 공시 / 뉴스) 로 급등락"
                    " 사유를 narrative 로 설명 — '왜 이격이 벌어졌는지'"
                    " 가 단기 dominant variable"
                )

    # Universal corporate-action scan via yfinance .splits — catches the
    # ex-date event for ANY market (US/KR/JP/CN). This is the only
    # corp-action signal available for US tickers (TSLA 2022-08 3:1,
    # NVDA 2024-06 10:1, AAPL 2020-08 4:1 all hit the same yfinance
    # historical-series staleness pattern as the KR 코미코 2026-05-17
    # case). For KR/JP, the DART / EDINET announcement scan above
    # usually fires first; this serves as a safety net when the
    # regulatory filing scan missed something. Universal HARD GUARD
    # body so the LLM sees the same directive regardless of market.
    try:
        yf_corp_action = _detect_yf_corp_action(ticker, lookback_days=14)
    except Exception as exc:
        _analyst_log.warning("yfinance corp action scan failed for %s: %s", ticker, exc)
        yf_corp_action = None
    if yf_corp_action:
        base += (
            "\n\n=== ⛔ CORPORATE ACTION IN-FLIGHT (HARD GUARD — yfinance ex-date) ===\n"
            f"{yf_corp_action['date']} yfinance .splits: {yf_corp_action['event']}\n"
            "이 종목은 최근 14일 이내 주식 분할 (split) ex-date가 지났다."
            " yfinance의 historical 시계열은 ex-date 전후로 자동 조정되지만,"
            " 일별 조정에 lag이 있는 경우가 흔해 currentPrice vs 50일/200일"
            " SMA 등에 대규모 갭이 남는다.\n"
            "다음 기술적 분석 요소는 사용 금지 (값이 의미 없음):\n"
            "  • 10 EMA / 50 SMA / 200 SMA 비교\n"
            "  • MACD / RSI / Bollinger 밴드 / ATR 추세 해석\n"
            "  • '과매수 / 과매도 / 단기 모멘텀 약화' 류 결론\n"
            "  • 52주 최고/최저 비교\n"
            "시장 분석가는 다음 두 항목만 다룬다:\n"
            "  (1) 분할 이벤트 자체의 정성 분석 (주주가치·유통주식수·"
            "ex-date 시점·시장 반응 톤)\n"
            "  (2) 분할 이후 가격이 안정화될 때까지 (~5-10 거래일) 기술적"
            " 추세 분석 보류 명시\n"
            "현재가만 'canonical current price' 한 줄로 인용하고 그 이상의"
            " 가격 비교는 금지. 펀더멘털 / 결정 노드도 이 가드를 따른다.\n"
            "추가 금지 (프로텍 053610.KS 2026-05-18 케이스):\n"
            "  ❌ 감자/분할 이후 share count를 본인이 가정해서 EPS / PER /"
            " 시총을 재계산 금지 (예: 'yfinance EPS 2,242는 감자 전 기준일"
            " 듯 → 9M주로 재계산하면 5,293원' 같은 추정 금지). yfinance"
            " 값이 transitional하게 inconsistent해 보여도, 본인이 만든"
            " 가정 값보다는 'yfinance 데이터 transitional — 다음 보고"
            " 주기 데이터 대기 권고' 한 줄 명시가 정답.\n"
            "  ❌ '추정 EPS' / '추정 시총' / '추정 PER'로 (추정) 라벨"
            " 붙여서 다운스트림에 넘기지 마라. 트레이더 / PM이 그 값을"
            " 진짜 multiples로 받아들여 'Forward PER 약 5배 → 매수 매력'"
            " 같은 fabricated 근거로 의사결정을 내린다. (추정) 라벨이"
            " 있어도 fabrication은 fabrication.\n"
            "  ✅ 정답: PER (TTM) / EPS (TTM) 자리에 'corp action 영향으로"
            " yfinance 값 transitional — 안정화 후 재평가' 한 줄. 그 다음"
            " 펀더멘털 항목 (매출 / 영업이익 / 부채비율 등 corp action에"
            " 영향 안 받는 절대값 지표) 으로 진행."
        )

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
            # D1 (2026-05-19): financialCurrency=USD KR 종목 강력
            # directive. 일부 KR 글로벌 자회사 (LG에너지솔루션 / 삼성SDI
            # 등) 는 KR 상장이지만 financial statements 가 USD 단위로
            # 보고됨. yfinance 가 financialCurrency='USD' return → 분석가
            # 가 yfinance 값을 KRW 로 오인해 inflation 1300x 적용.
            fin_ccy = (info.get("financialCurrency") or "").upper()
            if fin_ccy and fin_ccy not in ("KRW", "KOR", ""):
                base += (
                    f"\n\n⛔ D1 KR ticker financialCurrency MISMATCH"
                    f" (HARD GUARD):\n"
                    f"yfinance 가 `{ticker}` 의 financial statements"
                    f" 를 **{fin_ccy}** 로 보고. 그러나 거래는 KRX 라"
                    f" 시가총액 / 거래가격 등은 KRW 기준 (이미 위 canonical"
                    f" 값 KRW). 즉 yfinance 의 매출 / 영업이익 / EPS /"
                    f" 자본 / 부채 등 모든 financial 데이터가 {fin_ccy}"
                    f" 단위 — KRW 로 환산 시 ~1,300배 차이.\n\n"
                    f"다음 절대 금지:\n"
                    f"  ❌ yfinance 의 매출 / 순이익 / 자본 / 부채 등을"
                    f" KRW 단위로 인용 — '매출 50,000억 원' 같이 부풀려서"
                    f" inflated number 발생. 실제 yfinance return 50,000"
                    f" 는 {fin_ccy} 단위라 ~6.5경 원이라는 비현실 값.\n"
                    f"  ❌ PER / PBR / PSR / EV-EBITDA 등 multiples 도"
                    f" 단위 mismatch — 분모 (시총 KRW) ÷ 분자 ({fin_ccy}"
                    f" financial) 라 multiples 도 ~1,300x off.\n\n"
                    f"올바른 처리:\n"
                    f"  ✅ DART 의 한국 회계기준 (K-IFRS) 정기보고서가"
                    f" 한국어 + KRW 정확한 단위 — 분석가는 DART 공시"
                    f" 데이터 (위 DART block) 만 인용 + 'yfinance"
                    f" {fin_ccy} 보고 mismatch 로 yfinance financials"
                    f" 인용 보류' 한 줄 명시.\n"
                    f"  ✅ 펀더멘털 결론에 매출 / 순이익 / multiples 명시"
                    f" 못 한 경우 'KR 글로벌 자회사 financialCurrency"
                    f" 미스매치 (yfinance {fin_ccy} vs 거래 KRW) — 펀더"
                    f" 멘털 multiples 평가 보류, DART 정기보고서 인용'"
                    f" 처리."
                )
    except Exception:
        pass

    # Currency directive for JP tickers. yfinance returns Tokyo-listed
    # companies' financial fields in **JPY** (with one exception flagged
    # below). The analyst's US-trained default of '백만 달러' / '조 달러'
    # would render a Toyota market cap of ¥45조 as '약 45조 달러' — wrong
    # by ~150x and unreadable to Korean readers. Force native JPY with
    # native Japanese scale units (兆 / 億) and Korean-language labels
    # ('엔') so report headline values stay consistent with yfinance raw.
    #
    # Gotcha: a small number of JP-listed multinationals report financials
    # in USD (e.g. Sony reports parts of its consolidated statements in
    # USD due to global ops). Check `financialCurrency` against the
    # trading currency and warn the analyst when they diverge.
    try:
        if _cfg.get("currency") == "JPY":
            fin_ccy = (info.get("financialCurrency") or "").upper()
            base += (
                "\n\n=== CURRENCY DIRECTIVE (JP ticker, MANDATORY) ===\n"
                "This is a Tokyo-listed company. Default yfinance .info /"
                " financial fields are in **JPY**, never USD. When you"
                " render the fundamentals summary table and body:\n"
                " • Use Japanese scale units '兆 円' (≥1兆), '億 円'"
                " (1億 ~ 1兆 미만), '万 円' (1万 ~ 1億 미만). 한국어"
                " 본문에서는 '조 엔 / 억 엔 / 만 엔'으로 옮겨 써도 OK.\n"
                " • Never '달러' / 'USD' / '백만 달러' for JP tickers.\n"
                " • Headline-scale numbers (시가총액, 매출, 순이익,"
                " FCF) ROUND to two significant figures: '약 45조 엔'"
                " or '약 4,500억 엔'.\n"
                " • EPS / 주당 배당금 stays as integer JPY: '¥320'.\n"
                " • Per-share DCF outputs (Bear/Base/Bull): '¥X,XXX'.\n"
                "FORBIDDEN — place-by-place spelling. Convert raw"
                " integers (e.g. 45123456789000) to abbreviated form"
                " before they appear in the report.\n"
                " ❌ WRONG: '시가총액: 약 45조 달러' (불가능한 단위)\n"
                " ❌ WRONG: '총 매출: FY25 4조 5123억 4567만 8900 엔' (자리수별 풀스펠)\n"
                " ✅ RIGHT: '시가총액: 약 45조 엔'\n"
                " ✅ RIGHT: '총 매출 (억 엔): FY25 451,235 | FY24 412,800'\n"
                " ✅ RIGHT: '약 4.5조 엔' for headline prose mention.\n"
                "\n"
                "FISCAL YEAR — JP 상장사 대부분 회계연도 종료가 3월 31일"
                " (4월–익년 3월). yfinance의 'fiscalYearEnd' / 분기 라벨"
                " (Q1=4–6월, Q2=7–9월, Q3=10–12월, Q4=1–3월) 인용 시"
                " 미국 캘린더 분기로 착각하지 말 것. 최근 분기 실적 인용"
                " 시 '2026년 3월기 1Q (4-6월)' 식으로 표기.\n"
                "FISCAL YEAR LABEL MISMATCH — yfinance는 회계연도 종료"
                " 연도를 FY 라벨로 사용함: 2026년 3월 종료 = yfinance 'FY26'."
                " 일본 기업 IR·뉴스에서는 동일 회계연도를 '2025年度' 또는"
                " 'FY2025'로 표기. 따라서 뉴스에서 'FY2025 순이익 2.43조'"
                " = yfinance 'FY26 순이익 2.43조' — 같은 수치, 다른 라벨."
                " 리포트 내에서 yfinance 기준(FY26 등)으로 통일하고"
                " '3월 결산 기준' 을 괄호에 명시할 것. 뉴스 인용 시"
                " 'IR 발표 기준 2025年度 = yfinance FY26' 으로 매핑 명시."
                " 동일 수치를 두 가지 라벨로 혼용하면 독자 혼란 유발 금지."
            )
            if fin_ccy and fin_ccy not in ("JPY", ""):
                base += (
                    f"\n\n⚠️ FINANCIAL CURRENCY MISMATCH — yfinance reports"
                    f" `{ticker}`'s financial statements in **{fin_ccy}**,"
                    f" not JPY (trading currency). 시가총액과 일치하지"
                    f" 않을 수 있다. 손익계산서 / 대차대조표 수치 인용 시"
                    f" '{fin_ccy} 기준' 명시 + 환산하지 말고 그대로 인용."
                )
    except Exception:
        pass

    # Currency directive for TW tickers. yfinance returns TWSE/TPEx-listed
    # companies' financial fields in **TWD (NT$)**. TW tickers display
    # 'NT$' (or '元' in 繁體中文) prefix for prices, '億' / '万元' for
    # large-scale aggregates. Common LLM error: defaulting to '백만 달러'
    # for tech names since TSMC's US ADR (TSM) trades in USD — must
    # distinguish .TW (NT$) from TSM (USD).
    #
    # Gotcha: a small number of TW companies report in USD due to global
    # ops (e.g. some EMS subsidiaries). yfinance financialCurrency check
    # mirrors the JP / KR / US path.
    try:
        if _cfg.get("currency") == "TWD":
            fin_ccy = (info.get("financialCurrency") or "").upper()
            base += (
                "\n\n=== CURRENCY DIRECTIVE (TW ticker, MANDATORY) ===\n"
                "This is a Taiwan-listed company (TWSE 上市 or TPEx 上櫃)."
                " Default yfinance .info / financial fields are in **TWD**,"
                " never USD. When you render the fundamentals summary"
                " table and body:\n"
                " • Use Taiwanese scale units '兆 元' (≥1兆 = 10^12),"
                " '億 元' (1억 ~ 1兆), '万 元' (1만 ~ 1억). Or render as"
                " 'NT$' + integer (e.g. 'NT$1,234,567'). 한국어 본문에서는"
                " '조 元 / 억 元 / 만 元' 또는 'NT$ ...' 표기 OK.\n"
                " • Never '달러' / 'USD' / '백만 달러' for .TW tickers."
                " TSMC ADR (TSM) 시가총액과 TSMC .TW 시가총액 혼동 금지 —"
                " TSM은 USD, 2330.TW는 TWD, 환산비율 ~30:1.\n"
                " • Headline-scale numbers (시가총액, 매출, 순이익, FCF)"
                " ROUND to two significant figures: '약 25兆 元' or"
                " '약 25,000億 元' (둘 다 같은 값, 단위만 다름. 일관되게 한"
                " 단위로).\n"
                " • EPS / 주당 배당금 stays as integer or 1-decimal TWD:"
                " 'NT$45.6' 또는 '元45.6'.\n"
                " • Per-share DCF outputs: 'NT$X,XXX'.\n"
                "FORBIDDEN — place-by-place spelling. Convert raw"
                " integers (e.g. 25123456789000) to abbreviated form.\n"
                " ❌ WRONG: '시가총액: 약 25兆 달러' (불가능)\n"
                " ❌ WRONG: '총 매출: NT$25兆1,234억5,678만 元' (자리수별 풀스펠)\n"
                " ✅ RIGHT: '시가총액: 약 25兆 元 (NT$25T)'\n"
                " ✅ RIGHT: '총 매출 (億 元): FY25 25,123 | FY24 22,500'\n"
                " ✅ RIGHT: '약 25.1兆 元' for headline prose.\n"
                "\n"
                "FISCAL YEAR — TW 상장사는 회계연도 종료 12/31이 표준 (KR과"
                " 동일). yfinance 분기 라벨 (Q1 = 1-3월) 미국 캘린더 분기와"
                " 정합. 일부 회사 (특히 中国 자회사 큰 그룹) 는 자체 회계"
                " 주기 다를 수 있음 — yfinance 의 fiscalYearEnd 확인 후"
                " 명시. ADR (TSM) 분기 라벨도 .TW와 동일하게 캘린더 분기."
            )
            if fin_ccy and fin_ccy not in ("TWD", "NTD", ""):
                base += (
                    f"\n\n⚠️ FINANCIAL CURRENCY MISMATCH — yfinance reports"
                    f" `{ticker}`'s financial statements in **{fin_ccy}**,"
                    f" not TWD (trading currency). 시가총액과 일치하지"
                    f" 않을 수 있다. 손익계산서 / 대차대조표 수치 인용 시"
                    f" '{fin_ccy} 기준' 명시 + 환산하지 말고 그대로 인용."
                )
    except Exception:
        pass

    # USD currency directive — mirrors KR/JP/TW directives above.
    # Without this, the LLM sometimes outputs Korean-scale units for US
    # stocks (e.g. "약 426.17억 달러" for a $42.6B market cap — ON
    # Semiconductor 2026-05-22 review). The FACTUAL ANCHOR already injects
    # "$42.6B" but some models revert to place-value Korean reading.
    try:
        if _cfg.get("currency", "USD") == "USD" and market == "US":
            base += (
                "\n\n=== CURRENCY DIRECTIVE (US ticker, MANDATORY) ===\n"
                "This is a US-listed company. All monetary values are in"
                " **USD**. When you render any dollar amount:\n"
                " • Use '$XB' (billions) or '$XM' (millions) or '$X.XX'"
                " (single value). E.g. '$42.6B', '$820M', '$3.14'.\n"
                " • NEVER use Korean scale units ('억 달러', '조 달러',"
                " '백만 달러', '만 달러') for USD amounts. Korean readers"
                " of US stock analyses expect '$XB' notation, not '426억"
                " 달러'.\n"
                " ❌ WRONG: '시가총액: 약 426.17억 달러' (Korean scale + 달러)\n"
                " ✅ RIGHT: '시가총액: $42.6B'\n"
                " ❌ WRONG: '매출: 약 82,000만 달러' → '시가총액: $820M'\n"
                " ✅ RIGHT: '매출: $820M'\n"
                "BETA LABEL (fundamentals, 5년 월간): yfinance .info beta"
                " for US-listed stocks is computed vs **S&P 500**. Label it"
                " '베타 (5년 월간, vs S&P 500): X.XX'. The market analyst's"
                " 90-day beta uses SPY as benchmark — both are S&P 500 for"
                " US, so label both accordingly."
            )
    except Exception:
        pass

    # Non-US markets: inject market-aware beta label override for the
    # fundamentals analyst, which otherwise hardcodes 'vs S&P 500' for
    # all markets. yfinance .info beta for KR/JP/TW is computed against
    # the local broad index, not S&P 500. Without this override, KR
    # analyses show '베타 (5년 월간, vs S&P 500)' — a meaningless frame
    # for a KOSDAQ/KRX stock (경동나비엔 009450.KS 2026-05-22 review).
    # Rule applies to all non-US analyses going forward.
    try:
        _bench_label = _cfg.get("broad_label", "")
        if _bench_label and market not in ("US", ""):
            base += (
                f"\n\nBETA LABEL OVERRIDE (MANDATORY — overrides any"
                f" hardcoded 'vs S&P 500' instruction): yfinance .info"
                f" beta for `{ticker}` ({market} market) is computed vs"
                f" **{_bench_label}**, NOT S&P 500. In ALL sections:\n"
                f" • 펀더멘털 (5년 월간): '베타 (5년 월간, vs"
                f" {_bench_label}): X.XX'\n"
                f" • 시장 (90일): '베타 (90일, vs {_bench_label}): X.XX'\n"
                f" ❌ FORBIDDEN: '베타 (5년 월간, vs S&P 500)' for a"
                f" {market} ticker — S&P 500 is the wrong benchmark.\n"
                f" ✅ RIGHT: '베타 (5년 월간, vs {_bench_label}): X.XX'"
            )
    except Exception:
        pass

    # F3-light: parallel prefetch heavy I/O for the detected market.
    # Returns dict keyed by source tag (e.g. 'dart_disclosures',
    # 'edinet_holders', 'akshare_macro'). Each downstream try-block
    # below uses prefetched.get(tag) instead of re-fetching, eliminating
    # sequential I/O wait. Empty dict for US / ETF / unknown markets.
    if qt in ("ETF", "ETN", "MUTUALFUND"):
        prefetched: dict = {}
    else:
        try:
            prefetched = _prefetch_market_io(ticker, market)
        except Exception as exc:
            _analyst_log.warning(
                "prefetch_market_io top-level failed for %s: %s — falling"
                " back to sequential inline fetch",
                ticker, exc,
            )
            prefetched = {}

    if qt in ("ETF", "ETN", "MUTUALFUND"):
        # B2 (Step 2A ⑦, 2026-05-19): KR ETF 특화 mode. KODEX / TIGER
        # 등 KR-listed ETF 가 hardcoded metadata table 에 있으면 generic
        # fund_product narrative 대신 KR-ETF specific directive 적용 —
        # 기초자산 / 운용사 / 환헤지 / leverage / 분배금 등 ETF specific
        # 변수 명시 + 5거래일 horizon 적합성 검토 (leveraged inverse
        # 변동성 decay 위험 등).
        kr_etf_meta = None
        if market == "KR":
            try:
                from bot.kr_etf_metadata import (
                    get_kr_etf_metadata, format_kr_etf_block,
                )
                kr_etf_meta = get_kr_etf_metadata(ticker)
            except Exception as exc:
                _analyst_log.warning(
                    "kr_etf_metadata lookup failed for %s: %s", ticker, exc,
                )
        if kr_etf_meta:
            base += format_kr_etf_block(kr_etf_meta, ticker)
        # 공식 ETF/ETN 시세 (FSC 증권상품) — 괴리율·NAV·순자산·기초지수.
        # yfinance KR ETF 빈약 보완. 모든 KR ETF/ETN 적용(meta 유무 무관).
        if market == "KR":
            try:
                from bot.fsc_client import securities_product_quote
                _sp = securities_product_quote(ticker)
                if _sp and _sp.get("clpr"):
                    _prem = _sp.get("premium_pct")
                    _premtxt = (f"{'+' if (_prem or 0) >= 0 else ''}{_prem}%"
                                if _prem is not None else "N/A")
                    base += (
                        f"\n\n=== {_sp['type']} 공식 시세 (FSC 증권상품, KRX, "
                        f"{_sp.get('basDt', '')}) ===\n"
                        f"종가 ₩{int(_sp['clpr']):,} · NAV/지표가치 "
                        f"₩{int(_sp['nav']):,} · 괴리율 {_premtxt}"
                        + (f" · 기초지수 {_sp['bssIdx']}" if _sp.get("bssIdx") else "")
                        + "\n괴리율(+면 고평가 프리미엄, -면 디스카운트)은 유동성·"
                        "추종오차 신호. ±1% 초과 지속 시 호가 스프레드/유동성"
                        " 주의. 출처: 금융위원회/KRX (T+1)."
                    )
            except Exception as exc:
                _analyst_log.debug("FSC ETF quote skipped %s: %s", ticker, exc)
        if not kr_etf_meta:
            # Generic fund_product directive — KR ETF 미커버 또는
            # 다른 시장 ETF / MUTUALFUND
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

        # KR 시장 유동성 (예탁금·신용융자) — 금융투자협회 종합통계. 시장
        # 전체값(종목 무관·12h 캐시 공유). 시장 분석가가 retail 자금·
        # 레버리지 frame 으로 개별 종목 수급을 해석하도록. 실패 시 생략.
        if market == "KR":
            try:
                from bot.fsc_client import market_liquidity_line
                _liq = market_liquidity_line()
                if _liq:
                    base += (
                        "\n\n=== KR 시장 유동성 (금융투자협회 종합통계, T+1) ===\n"
                        f"{_liq}\n"
                        "투자자예탁금↑ = 대기매수 자금 유입(상방 지지), 신용융자↑"
                        " = 레버리지 누적(과열·반대매매 위험). 개별 종목 수급을"
                        " 이 시장 전체 유동성 frame 안에서 해석. 출처: 금융투자협회."
                    )
            except Exception as exc:
                _analyst_log.debug("KR liquidity block skipped %s: %s", ticker, exc)

        # KR 의무보호예수 해제(lock-up release) — 단기 공급 overhang soft 경고.
        # 임박/최근 해제일(rsrnDt) 물량 = 매도 가능 물량 출회. 기술지표 차단은
        # 안 하고(corp action 가드와 별개) 수급 경고만. 실패 시 생략.
        if market == "KR":
            try:
                from bot.fsc_client import lockup_releases
                import datetime as _dt_lk
                _today = _dt_lk.date.today()
                _lk = [r for r in lockup_releases(ticker)
                       if r.get("release_date")
                       and -7 <= (_dt_lk.date.fromisoformat(r["release_date"])
                                  - _today).days <= 90]
                if _lk:
                    _lines = []
                    for r in _lk[:6]:
                        sh = r.get("shares")
                        shtxt = f"{int(sh):,}주" if sh else "?주"
                        _lines.append(f"  {r['release_date']} · {shtxt}"
                                      f" ({r.get('reason', '')})")
                    base += (
                        "\n\n=== 📌 의무보호예수 해제 예정 (FSC, 단기 공급) ===\n"
                        + "\n".join(_lines)
                        + "\n보호예수 해제 = 최대주주/기관 매도 가능 물량 출회 →"
                        " 해당일 전후 단기 공급 압력. 5거래일 horizon 에서 해제일이"
                        " 윈도 내면 수급 부담으로 반영(기술지표 무효화는 아님)."
                        " 출처: 금융위/KSD (T+1)."
                    )
            except Exception as exc:
                _analyst_log.debug("KR lockup block skipped %s: %s", ticker, exc)

        # KR 소액주주현황 (free float·유통물량) — 작전주/유동성 판단. 시총
        # cross-check 보조. 연/분기 공시라 정적이지만 유통물량 frame. 실패 시 생략.
        if market == "KR":
            try:
                from bot.fsc_client import minority_holders
                _mh = minority_holders(ticker)
                if _mh and _mh.get("smam_ratio") is not None:
                    _yr = f" ({_mh['biz_year']})" if _mh.get("biz_year") else ""
                    _cnt = ""
                    if _mh.get("smam_cnt") and _mh.get("whole_cnt"):
                        _cnt = (f" · 소액주주수 {int(_mh['smam_cnt']):,}명"
                                f" (전체 {int(_mh['whole_cnt']):,}명)")
                    base += (
                        f"\n\n=== 소액주주현황 (FSC 기업지배구조{_yr}) ===\n"
                        f"소액주주 비율 {_mh['smam_ratio']}%{_cnt}\n"
                        "소액주주 비율↑ = 유통물량 많음(유동성·변동성↑), 비율↓ ="
                        " 최대주주/기관 집중(품절·작전 취약). 출처: 금융위(연/분기)."
                    )
            except Exception as exc:
                _analyst_log.debug("KR minority block skipped %s: %s", ticker, exc)

        # KR dilution 공시 (CB/BW 발행결정) — 잠재 희석 수급 경고(기술지표
        # 차단 X). 유상증자는 corp-action 키워드에 있어 제외(중복 방지).
        if market == "KR":
            try:
                from bot.fsc_client import dilution_events
                _dl = dilution_events(ticker)
                if _dl:
                    _lines = []
                    for r in _dl[:6]:
                        sh = r.get("new_shares")
                        shtxt = f"{int(sh):,}주" if sh else "?주"
                        pr = r.get("price")
                        prtxt = f" @₩{int(pr):,}" if pr else ""
                        _lines.append(f"  {r['date']} · {r['kind']} · 신주 {shtxt}{prtxt}")
                    base += (
                        "\n\n=== 📉 잠재 희석 이벤트 (FSC 공시 — 전환사채/BW) ===\n"
                        + "\n".join(_lines)
                        + "\nCB/BW 전환·행사 시 신주 발행 = 주식수 증가(EPS·지분"
                        " 희석). 발행결정 직후~전환청구기간 단기 공급 부담."
                        " (유상증자/무상증자/감자는 corp-action 가드가 별도 처리)"
                        " 출처: 금융위 (T+1)."
                    )
            except Exception as exc:
                _analyst_log.debug("KR dilution block skipped %s: %s", ticker, exc)

        # API KEY ABSENCE — anti-hallucination directive (Rule A).
        # Previously the per-source injection blocks (DART / EDINET /
        # FRED / Naver / Kabutan) silently no-op'd when the API key was
        # missing. Result: the entire block was absent from the prompt,
        # so the LLM had ZERO signal that the data wasn't fetched and
        # happily fabricated KR-style 공시 + insider holdings (코미코
        # 2026-05-17 "공기업/산업은행") OR EDINET-style 공시 + 5%+
        # 대량보유 (Toyota 7203.T 2026-05-18 "BlackRock Japan 5.1%
        # 2026-05-10" — pure fabrication of specific names + dates +
        # percentages). The fix: even when no block is injected,
        # explicitly tell the LLM that the data source is OFFLINE for
        # this run and forbid invention.
        import os as _os
        _missing_keys: list[str] = []
        if market == "KR":
            if not _os.getenv("DART_API_KEY", "").strip():
                _missing_keys.append(
                    "DART (한국 공시 + 임원·주요주주 지분 + 실적 윈도)"
                )
            if not _os.getenv("BOK_ECOS_API_KEY", "").strip():
                _missing_keys.append(
                    "BoK ECOS (한국 기준금리 + KR 10Y + CPI)"
                )
            if not (_os.getenv("NAVER_CLIENT_ID", "").strip()
                    and _os.getenv("NAVER_CLIENT_SECRET", "").strip()):
                _missing_keys.append(
                    "Naver News (한국어 뉴스 25K/day)"
                )
        elif market == "JP":
            if not _os.getenv("EDINET_API_KEY", "").strip():
                _missing_keys.append(
                    "EDINET (일본 공시 + 5%+ 대량보유 + 분기 보고 윈도)"
                )
            if not _os.getenv("FRED_API_KEY", "").strip():
                _missing_keys.append(
                    "FRED (BoJ 정책금리 + JGB 10Y + JP CPI)"
                )
        elif market == "TW":
            # TW data sources are all keyless (MOPS HTTP / 鉅亨網 scrape
            # / FRED keys already covered). FRED is the only API key
            # involved — same registration / .env entry as JP. MOPS +
            # 鉅亨網 scrape are part of Phase 4-TW-B (next commit), so
            # at this point the OFFLINE block lists FRED only and won't
            # fire until the client modules ship.
            if not _os.getenv("FRED_API_KEY", "").strip():
                _missing_keys.append(
                    "FRED (TW 중앙은행 重貼現率 + CPI — JP와 키 공유)"
                )
        elif market in ("CN_A", "HK"):
            # CN/HK uses AKShare (no API key) — but the ~200MB dep may
            # not be installed on the bot host. Probe by attempting a
            # cheap lazy import. ImportError → emit DATA OFFLINE entry
            # so the LLM doesn't fabricate 公告 dates / 主要 流通股东 /
            # 港股通 flow numbers / LPR rate that AKShare couldn't fetch.
            try:
                import akshare as _akshare_probe  # noqa: F401
            except Exception:
                _missing_keys.append(
                    "AKShare (中国 公告 + 东方财富 뉴스 + 港股通 flow"
                    " + LPR / CPI / PMI — `pip install akshare` 필요)"
                )
        if _missing_keys:
            _missing_list = "\n".join(f"  • {k}" for k in _missing_keys)
            base += (
                "\n\n=== ⛔ DATA SOURCE OFFLINE (ANTI-HALLUCINATION HARD GUARD) ===\n"
                "다음 데이터 소스가 이번 실행에서 API 키 부재로 OFFLINE입니다:\n"
                f"{_missing_list}\n\n"
                "이 데이터 소스에서 제공해야 할 정보 (예: 최근 공시 / 임원·주요주주"
                " 지분 / 대량보유 보고 / 자국 기준금리 / 자국어 뉴스 등)를"
                " 절대 fabrication하지 마라.\n\n"
                "특히 금지되는 패턴:\n"
                "  ❌ '최근 공시: YYYY-MM-DD 자사주 매입 / 가이던스 하향 등'"
                " 형태로 specific 날짜 + 이벤트 만들기\n"
                "  ❌ '대량보유 공시: BlackRock 5.1% / Vanguard 5.0%' 형태로"
                " specific 보유자 + 정확한 % 만들기 (대형주는 그럴 가능성이"
                " 높다는 추정만으로 specific 값 fabrication 금지)\n"
                "  ❌ '공기업/공공 entity', '재벌 계열사 지배구조', '創業家"
                " family holdings' 등 generic 소유구조 narrative 만들기\n"
                "  ❌ '한국은행 기준금리 X.X%, KR 10Y X.X%' 등 self-quote\n"
                "  ❌ '한국어 뉴스: 최근 ... 보도' / '日経 보도에 따르면 ...' 등\n\n"
                "올바른 처리: 해당 항목은 보고서에서 한 줄 'X 데이터 미수집 (이번"
                " 실행)' 로 명시하고 다음 항목으로 진행. 펀더멘털 / 결정 노드도"
                " 이 가드를 따른다."
            )

        # EDGAR (US-only) — SEC 8-K material events + Form 4 insider
        # trades. No API key required; rate-limit + User-Agent enforced
        # in bot/edgar_client.py. Parallel-prefetched by
        # _prefetch_market_io (US branch). Gate both via _section_allowed
        # so social analyst doesn't see irrelevant blocks.
        if market == "US":
            try:
                if _section_allowed(analyst_id, "edgar_8k"):
                    from bot.edgar_client import format_edgar_8k_block
                    filings_8k = prefetched.get("edgar_8k") or []
                    block_8k = format_edgar_8k_block(filings_8k)
                    if block_8k:
                        base += "\n\n" + block_8k
            except Exception as exc:
                _analyst_log.warning(
                    "edgar 8k injection failed for %s: %s", ticker, exc,
                )
            try:
                if _section_allowed(analyst_id, "edgar_form4"):
                    from bot.edgar_client import format_edgar_form4_block
                    filings_f4 = prefetched.get("edgar_form4") or []
                    block_f4 = format_edgar_form4_block(filings_f4)
                    if block_f4:
                        base += "\n\n" + block_f4
            except Exception as exc:
                _analyst_log.warning(
                    "edgar form4 injection failed for %s: %s", ticker, exc,
                )
            try:
                if _section_allowed(analyst_id, "options_signals"):
                    from bot.options_client import format_options_block
                    opt = prefetched.get("options_signals")
                    try:
                        import datetime as _d2e_dt
                        _d2e = days_until_earnings(
                            ticker, _d2e_dt.date.today().isoformat()
                        )
                    except Exception:
                        _d2e = None
                    opt_block = format_options_block(opt, days_to_earnings=_d2e)
                    if opt_block:
                        base += "\n\n" + opt_block
            except Exception as exc:
                _analyst_log.warning(
                    "options injection failed for %s: %s", ticker, exc,
                )

        # DART (KR-only) — 공시 / 임원지분 / 실적 윈도. yfinance returns
        # nothing useful for these on KRX-listed names; DART is the
        # authoritative source. Graceful degradation: if DART_API_KEY is
        # missing or the API is down, the block is just empty and the
        # analyst continues without it.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "KR":
                # F3-light: use prefetched results (parallel fetch at top
                # of function) instead of sequential inline fetch.
                disclosures = prefetched.get("dart_disclosures") or []
                insiders = prefetched.get("dart_insiders") or []
                window = prefetched.get("dart_window")

                # B4: KRX 시장경보 HARD GUARD inject. fetch는 prefetch
                # 단계에서 완료 — 여기는 banner 렌더링만. CN_A/HK 의
                # ST/*ST/停牌 HARD GUARD 와 동일 shape, KR 시장의 거래
                # 정지 / 관리종목 / 단기과열 / 투자위험 등 분류 시 5거래
                # 일 정상 가격 분석 보류 + 펀더멘털 결론에 분류 명시
                # 의무 directive.
                try:
                    from bot.krx_alert_client import format_krx_alert_block
                    krx_status = prefetched.get("krx_alert") or {}
                    krx_alert_banner = format_krx_alert_block(krx_status)
                    if krx_alert_banner:
                        base += (
                            "\n\n=== ⚠️ KR 시장경보 (HARD GUARD —"
                            " 거래정지 / 관리종목 / 단기과열 / 투자경보) ===\n"
                            + krx_alert_banner
                        )
                except Exception as exc:
                    _analyst_log.warning(
                        "krx alert banner inject failed for %s: %s",
                        ticker, exc,
                    )

                # US overnight futures (KR context) — ES=F + NQ=F 방향성.
                # KR 개장 전 미국 선물 변동률 주입 (전일 close 대비).
                # gate: market analyst only (기술적 방향성 signal).
                try:
                    if _section_allowed(analyst_id, "us_futures"):
                        us_fut = prefetched.get("us_futures") or {}
                        if us_fut:
                            fut_lines = ["=== 미국 선물 (KR 개장 전 참고) ==="]
                            for sym_key, d in us_fut.items():
                                sign = "+" if d["pct"] >= 0 else ""
                                fut_lines.append(
                                    f"  {d['label']} ({sym_key}):"
                                    f" {d['price']:,.0f}"
                                    f" ({sign}{d['pct']:.2f}%)"
                                )
                            fut_lines.append(
                                "▶ 전일 미국 시장 마감 이후 선물 방향성."
                                " KR 시장 갭 오픈 / 외국인 수급 방향 예측에 활용."
                            )
                            base += "\n\n" + "\n".join(fut_lines)
                except Exception as exc:
                    _analyst_log.warning(
                        "us_futures injection failed for %s: %s", ticker, exc,
                    )

                # CORPORATE ACTION IN-FLIGHT detection (Rule A). A stock
                # split / bonus issue / reverse split announced in the
                # last 14 days corrupts yfinance's historical price
                # series (current price is split-adjusted but SMA / EMA
                # / MACD / Bollinger still reference unadjusted closes).
                # 코미코 2026-05-17: 무상증자 + 주식분할 결정 2026-05-12,
                # currentPrice ₩81,000 vs 50일 SMA ₩129,484 (-37% gap);
                # market analyst built a full technical thesis on the
                # corrupted series. The existing PRICE GAP SANITY warning
                # is too soft — the LLM acknowledged the split then
                # continued the analysis anyway. Inject a HARD guard
                # that names the event by date so the analyst can't
                # rationalize past it.
                corp_action = _detect_kr_corp_action(disclosures)
                if not corp_action:
                    # DART scan miss → FSC 권리일정(KSD) 백업 (키 없거나 키워드
                    # 변형으로 DART 가 놓친 경우). 정기 기준일·배당은 제외됨.
                    corp_action = _detect_fsc_corp_action(ticker)
                if corp_action:
                    _ca_src = corp_action.get("source", "DART 공시")
                    base += (
                        "\n\n=== ⛔ CORPORATE ACTION IN-FLIGHT (HARD GUARD) ===\n"
                        f"{corp_action['date']} {_ca_src}: {corp_action['event']}\n"
                        "이 종목은 현재 분할 / 무상증자 / 감자 / 액면분할 등"
                        " corporate action이 진행 중이다. yfinance의 historical"
                        " 가격 시계열은 ex-date 전후로 비조정 또는 부분 조정"
                        " 상태일 가능성이 매우 크다 — currentPrice는 조정 반영,"
                        " fiftyDayAverage / twoHundredDayAverage / 일별 close는"
                        " 미조정인 혼합 상태가 흔하다.\n"
                        "다음 기술적 분석 요소는 사용 금지 (값이 의미 없음):\n"
                        "  • 10 EMA / 50 SMA / 200 SMA 비교\n"
                        "  • MACD / RSI / Bollinger 밴드 / ATR 추세 해석\n"
                        "  • '과매수 / 과매도 / 단기 모멘텀 약화' 류 결론\n"
                        "  • 50주·52주 최고/최저 비교\n"
                        "시장 분석가는 다음 두 항목만 다룬다:\n"
                        "  (1) corporate action 이벤트 자체의 정성 분석"
                        " (주주가치 영향, 유통 주식 수 변화, ex-date 시점,"
                        " 시장의 반응 톤)\n"
                        "  (2) 분할 이후 가격이 안정화될 때까지"
                        " (~5-10 거래일) 기술적 추세 분석 보류 명시\n"
                        "현재가만 'canonical current price' 한 줄로 인용하고"
                        " 그 이상의 가격 비교는 금지. 펀더멘털 / 결정 노드도"
                        " 이 가드를 따른다.\n"
                        "추가 금지 (프로텍 053610.KS 2026-05-18 케이스):\n"
                        "  ❌ 감자/분할 이후 share count를 본인이 가정해서"
                        " EPS / PER / 시총을 재계산 금지. yfinance 값이"
                        " transitional해 보여도 본인 가정으로 만든 값보다는"
                        " 'yfinance 데이터 transitional — 다음 보고 주기"
                        " 대기' 한 줄 명시가 정답.\n"
                        "  ❌ '(추정) EPS / PER / 시총' 라벨로 트레이더 /"
                        " PM에 넘기지 마라. (추정) 라벨이 있어도 fabricated"
                        " 값으로 다운스트림이 'Forward PER 약 5배 매력' 같은"
                        " 의사결정 근거를 만든다.\n"
                        "  ✅ corp action에 영향 안 받는 절대값 지표 (매출 /"
                        " 영업이익 / 부채비율 등) 위주로 펀더멘털 진행."
                    )

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
            if detect_market(ticker) == "KR" and _section_allowed(analyst_id, "krx_flow"):
                from bot.pykrx_client import (
                    format_flow_for_prompt,
                    format_trend_for_prompt,
                )
                # F3-light: use prefetched (parallel) results
                flow = prefetched.get("pykrx_flow")
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
                else:
                    # Fix F (2026-05-19 현대차증권 001500.KS surfaced):
                    # pykrx 가 mid-cap 일부 종목 / 일부 trading day 에
                    # 빈 응답 ('empty trading flow response') 또는 JSON
                    # parse 실패 반환. 자동 fallback directive — 분석가
                    # 가 'pykrx 데이터 없음 → 외국인 flow generic narrative
                    # 만들기' 패턴 차단.
                    base += (
                        "\n\n⚠️ KR 외국인 / 기관 / 개인 flow 데이터 미수집"
                        " (pykrx 빈 응답 또는 KRX endpoint 실패):\n"
                        "이 종목의 5거래일 투자자 flow 데이터를 fetch 할 수"
                        " 없었다 (mid-cap 일부 / 일시 KRX rate-limit / 휴장일"
                        " 등). 다음 패턴 절대 금지:\n"
                        "  ❌ 'KR 외국인 순매수' / '기관 순매도' generic"
                        " narrative 만들기 — 데이터 없는 상황에서 추측"
                        " 금지.\n"
                        "  ❌ 'KR 시장의 외국인 자금 흐름' 같은 시장 전체"
                        " 추론도 본 종목의 flow 부재 사실을 모호하게"
                        " 만듦 — 본 종목 flow 데이터 미수집 명시 우선.\n"
                        "올바른 처리: 시장 분석에 한 줄로 'pykrx flow 데이터"
                        " 미수집 — 5일 외국인/기관 수급 평가 보류' 명시."
                    )

                # 30-day positioning trends: foreign ownership pct +
                # short-balance pct. Daily flow (above) shows what
                # happened in the last 5 days; these show longer
                # positioning trajectory (foreigners accumulating
                # vs distributing, shorts building vs squeezing).
                foreign_trend = prefetched.get("pykrx_foreign_trend")
                short_trend = prefetched.get("pykrx_short_trend")
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

        # Step 2B A1: KIS 7종 수급 데이터 inject (시장 분석가 전용).
        # pykrx flow (KRX 공개 데이터) 보다 상세 — 기관 주체별 / 공매도 /
        # 프로그램 / 외인 한도소진율이 추가됨. 두 소스 동시 주입 OK:
        # pykrx = 5일 누적 외인/기관/개인, KIS = 당일 + 5일 + 기관세분화.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "KR" and _section_allowed(analyst_id, "kis_supply"):
                from bot.kis_client import format_kis_block, KIS_INTERP_GUIDE
                kis_data = {
                    "price":         prefetched.get("kis_price"),
                    "investor_flow": prefetched.get("kis_investor_flow"),
                    "foreign_limit": prefetched.get("kis_foreign_limit"),
                    "credit_short":  prefetched.get("kis_credit_short"),
                    "program_trade": prefetched.get("kis_program_trade"),
                    "short_sale":    prefetched.get("kis_short_sale"),
                }
                has_any = any(v for v in kis_data.values() if v)
                if has_any:
                    block = format_kis_block(kis_data)
                    # KIS price vs FACTUAL ANCHOR price ratio mismatch detector
                    # (319660.KS 2026-05-23 surfaced). KIS ₩116,900 vs ANCHOR
                    # ₩32,300 = 3.62x — 분명한 분할 signature 였으나 corp
                    # action HARD GUARD 발화 없이 분석가가 "데이터 transitional"
                    # 텍스트로만 인지. yfinance .splits 14일 lookback + DART
                    # 30일 window 둘 다 놓치는 case (분할결의→ex-date 갭).
                    # 이 가드는 두 source 가 동시에 현재가 를 cite 할 때 자동
                    # 비율 비교로 corp action 을 잡아냄.
                    corp_action_warning = ""
                    try:
                        kis_px = (kis_data.get("price") or {}).get("price")
                        anchor_px = info.get("currentPrice") or info.get("regularMarketPrice")
                        if (isinstance(kis_px, (int, float)) and kis_px > 0
                                and isinstance(anchor_px, (int, float)) and anchor_px > 0):
                            ratio = max(kis_px, anchor_px) / min(kis_px, anchor_px)
                            if ratio > 1.30:
                                # Fix ③ upgrade: KIS ratio > 1.30x 는
                                # 이미 CRITICAL — pykrx 15% 와 동일 severity.
                                # PM HOLD lock + CRITICAL banner.
                                info["_price_critical_divergence"] = {
                                    "src1": "KIS",
                                    "px1": kis_px,
                                    "src2": "FACTUAL ANCHOR",
                                    "px2": anchor_px,
                                    "diff_pct": (ratio - 1) * 100,
                                }
                                corp_action_warning = (
                                    f"\n\n🔴 PRICE CRITICAL DIVERGENCE +"
                                    f" CORP ACTION HARD GUARD — KIS vs"
                                    f" FACTUAL ANCHOR 가격 불일치 자동 감지"
                                    f" (319660.KS 2026-05-23 surfaced):\n"
                                    f"KIS 현재가 ₩{int(kis_px):,} vs FACTUAL"
                                    f" ANCHOR 현재가 ₩{int(anchor_px):,} → ratio"
                                    f" {ratio:.2f}x ({(ratio-1)*100:.1f}%"
                                    f" 괴리). yfinance/KIS 한쪽이 분할-"
                                    f" 조정(split-adjusted), 다른 쪽이 unadjusted"
                                    f" 일 때 발생.\n"
                                    f"⛔ 기술 지표(SMA/EMA/MACD/RSI/볼린저) +"
                                    f" PER/PBR cite 모두 INVALID.\n"
                                    f"⛔ PM 결론 강제: 데이터 정합성 확인 완료 전"
                                    f" 매수/매도 금지. 최종 권고를 반드시 HOLD"
                                    f" (보유/관망) 로 고정할 것.\n"
                                    f"DART 분할 공시 또는 yfinance .splits 확인"
                                    f" 권고.\n"
                                )
                    except Exception:
                        pass
                    if block:
                        base += (
                            "\n\n=== KIS 단기 수급 데이터 (7종, verbatim —"
                            " 이 수치를 그대로 본문에 인용할 것) ===\n"
                            + block
                            + corp_action_warning
                            + "\n\n" + KIS_INTERP_GUIDE
                            + "\n\n⛔ KIS API SCOPE — HARD GUARD"
                              " (현대오토에버 307950.KS 2026-05-23 surfaced):\n"
                              "KIS 는 단기 수급 데이터 (현재가 · 외인/기관/개인"
                              " flow · 한도소진율 · 신용/대차 · 프로그램매매 ·"
                              " 공매도) 만 제공. **PER · PBR · PSR · EV/EBITDA"
                              " · EPS · 시가총액 · 매출 · 순이익 · 영업이익 등"
                              " valuation/펀더멘털 지표는 KIS 가 제공하지 않음**.\n"
                              "❌ FORBIDDEN: 'KIS 데이터 기준 PER 94.8배' /"
                              " 'KIS 시가총액' / 'KIS EPS' 같은 인용 — fabrication."
                              " PM/Trader/모든 분석가가 위반 시 thesis invalid.\n"
                              "✅ 정답: valuation 은 FACTUAL ANCHOR / Canonical"
                              " market cap / PRE-COMPUTED 비율 블록에서만 인용."
                              " 해당 블록에서 'N/A' 면 데이터 미수집으로 명시,"
                              " KIS 또는 다른 source 로 채우려 시도 금지."
                        )
                else:
                    # KIS 키 있지만 전체 fetch 실패 → fabrication 차단
                    import os as _os
                    if _os.environ.get("KIS_APP_KEY"):
                        base += (
                            "\n\n⚠️ KIS 단기 수급 데이터 미수집"
                            " (API 오류 또는 장 마감 시간 외):\n"
                            "다음 패턴 금지:\n"
                            "  ❌ '외국인 순매수 지속' / '기관 매수세' 등"
                            " generic KR 수급 narrative 생성.\n"
                            "  ❌ 공매도 비율 / 신용잔고 수치 추정 또는 인용.\n"
                            "올바른 처리: 'KIS 수급 데이터 미수집 —"
                            " 5거래일 수급 평가 보류' 한 줄로 명시."
                        )
        except Exception as exc:
            _analyst_log.warning(
                "kis supply inject failed for %s: %s", ticker, exc,
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
            if detect_market(ticker) == "KR" and kr_name and _section_allowed(analyst_id, "naver_news"):
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

        # KR macro from Bank of Korea ECOS (KR-only). yfinance gives us
        # US 10Y / DXY / etc. but the KR rate environment was missing
        # entirely. KR equity analysts kept reasoning about "고금리
        # 환경" from ^TNX alone, which is the wrong frame — what
        # actually moves KR rate-sensitive sectors (utilities / banks /
        # consumer discretionary) is the 한국은행 기준금리 + KR 10Y
        # spread, not the US curve. Three indicators: base rate, KR
        # 10Y, CPI YoY. Daily / monthly cadence so the 12h cache in
        # bok_ecos_client absorbs the cost.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "KR" and _section_allowed(analyst_id, "bok_macro"):
                from bot.bok_ecos_client import format_kr_macro_for_prompt
                kr_macro = prefetched.get("bok_macro") or {}
                kr_macro_block = format_kr_macro_for_prompt(kr_macro)
                if kr_macro_block:
                    base += (
                        "\n\n=== Pre-fetched KR macro (한국은행 ECOS,"
                        " verbatim — KR-specific rate environment) ===\n"
                        + kr_macro_block
                        + "\n\n위 KR 거시 지표는 KR equity 분석의"
                        " 기본 frame이다. ^TNX (미국 10Y)만 보고"
                        " '고금리 환경' 결론을 내리지 말고, KR"
                        " 기준금리 + KR 10Y의 절대 수준 + 직전 변동"
                        " 방향을 같이 인용하라. KR CPI는 한은의"
                        " 다음 통화정책 회의 방향을 결정하는 핵심"
                        " 변수이므로 인플레이션 흐름 언급 시 KR CPI"
                        " 우선."
                    )

                # D2 (Step 2A ⑤, 2026-05-19): USD/KRW 영향 자동 계산.
                # 수출 의존도 큰 KR 산업 (반도체 / 자동차 / 배터리 /
                # 조선 / 해운 등) 의 영업이익 sensitivity 자동 inject —
                # 분석가들이 generic 'KRW 약세 = 수출주 긍정' narrative
                # 만 적던 패턴 차단 + specific % 영향 추정 제공.
                try:
                    from bot.kr_fx_sensitivity import compute_fx_impact
                    fx_impact_text = compute_fx_impact(
                        industry, prefetched.get("krw_30d_pct"),
                    )
                    if fx_impact_text:
                        base += "\n\n" + fx_impact_text
                except Exception as exc:
                    _analyst_log.warning(
                        "kr_fx_sensitivity inject failed for %s: %s",
                        ticker, exc,
                    )
        except Exception as exc:
            _analyst_log.warning(
                "ecos macro injection failed for %s: %s", ticker, exc,
            )

        # ─────────────────────────────────────────────────────────────
        # JP equity-only injections — same shape as the KR blocks above,
        # so the JP analyst's prompt is symmetric with the KR analyst's.
        # All four sources degrade silently (missing key / 403 / parse
        # failure → block omitted, never an exception that bubbles up).
        # ─────────────────────────────────────────────────────────────

        # EDINET (JP regulatory filings — annual / quarterly / 임시 /
        # 5%+ stake). yfinance has nothing here; EDINET is the FSA-run
        # authoritative source. Mirrors the KR DART block.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "JP":
                from bot.edinet_client import format_edinet_jp_block
                # F3-light: prefetched in parallel
                jp_disclosures = prefetched.get("edinet_disclosures") or []
                jp_holders = prefetched.get("edinet_holders") or []
                jp_window = prefetched.get("edinet_window")

                # Same corporate-action staleness guard as the KR DART
                # branch — JP companies announce 株式分割 / 株式併合 /
                # 株式無償割当 via EDINET 臨時報告書 with the same yfinance
                # historical-series adjustment lag.
                jp_corp_action = _detect_jp_corp_action(jp_disclosures)
                if jp_corp_action:
                    base += (
                        "\n\n=== ⛔ CORPORATE ACTION IN-FLIGHT (HARD GUARD) ===\n"
                        f"{jp_corp_action['date']} EDINET 공시: {jp_corp_action['event']}\n"
                        "이 종목은 현재 株式分割 / 無償割当 / 株式併合 등"
                        " corporate action이 진행 중이다. yfinance의 historical"
                        " 가격 시계열은 ex-date 전후로 비조정 또는 부분 조정"
                        " 상태일 가능성이 매우 크다.\n"
                        "다음 기술적 분석 요소는 사용 금지:\n"
                        "  • 10 EMA / 50 SMA / 200 SMA 비교\n"
                        "  • MACD / RSI / Bollinger 밴드 / ATR 추세 해석\n"
                        "  • 52주 최고/최저 비교\n"
                        "시장 분석가는 (1) corporate action 자체의 정성"
                        " 분석 (株主価値 영향, 浮動株 변화, ex-date),"
                        " (2) 안정화 가격이 잡힐 때까지 (~5-10 거래일)"
                        " 기술적 추세 분석 보류 명시 — 두 항목만 다룬다."
                    )

                edinet_block = format_edinet_jp_block(jp_disclosures, jp_holders, jp_window)
                if edinet_block:
                    base += (
                        "\n\n=== Pre-fetched JP market data (EDINET, verbatim —"
                        " do NOT call any tool for these numbers; use them in"
                        " the news / fundamentals / risk sections as ground"
                        " truth) ===\n"
                        + edinet_block
                        + "\n\nRENDERING RULES for the EDINET block:\n"
                        " • 최근 공시: render as a bullet list, one filing"
                        " per line, with the date prefix preserved.\n"
                        " • 대량보유 공시: render as a bullet list with"
                        " filer name + 보유 변동 사유. 5% rule 공시는"
                        " activist / institutional positioning 신호이므로"
                        " 단순 나열하지 말고 결론에서 해석 한 줄 추가.\n"
                        " • 다음 정기보고서 윈도: one prose sentence."
                        " JP 회계연도 3월말 종료가 표준이라 분기 라벨"
                        " (1Q=4-6월 등)을 명시하라."
                    )

                # Anti-hallucination guard mirroring the KR DART branch
                # (Rule B). EDINET 대량보유 (5%+) returns nothing for many
                # mid/small-cap JP names; yfinance heldPercentInsiders is
                # also frequently absent for `.T` tickers. Without this
                # directive the fundamentals LLM tends to backfill a
                # generic 'BlackRock / Vanguard 등 institutional investors'
                # or '創業家 family holdings' narrative invented from
                # zero data — the JP equivalent of the 코미코 '공기업'
                # hallucination.
                yf_insider_pct = info.get("heldPercentInsiders")
                yf_inst_pct = info.get("heldPercentInstitutions")
                insider_data_missing = (
                    not jp_holders
                    and not (isinstance(yf_insider_pct, (int, float)) and yf_insider_pct >= 0.001)
                    and not (isinstance(yf_inst_pct, (int, float)) and yf_inst_pct >= 0.001)
                )
                if insider_data_missing:
                    base += (
                        "\n\n=== JP 소유구조 데이터 부재 (ANTI-HALLUCINATION) ===\n"
                        "EDINET 대량보유 공시(5%+) 도 없고 yfinance"
                        " heldPercentInsiders / heldPercentInstitutions 도"
                        " 미수집 상태이다. 'BlackRock / Vanguard 등 글로벌"
                        " 패시브 펀드 보유', '創業家 / family holdings 우세',"
                        " '主要株主는 ___그룹 계열사' 같은 generic 지배구조"
                        " narrative를 만들지 마라. 한 줄로 'JP 소유구조"
                        " 데이터 미수집' 만 명시하고 다음 항목으로 진행."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "edinet injection failed for %s: %s", ticker, exc,
            )

        # Japanese news via Kabutan scrape (JP-only). yfinance / Alpha
        # Vantage barely cover JP-language publishers. Kabutan
        # aggregates Reuters Japan / 日経 / 東洋経済 / 株探 desk into
        # one per-ticker news listing. Mirrors the Naver KR block.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "JP" and _section_allowed(analyst_id, "kabutan_news"):
                from bot.kabutan_news import format_news_for_prompt as fmt_jp_news
                jp_news = prefetched.get("kabutan_news") or []
                if jp_news:
                    base += (
                        "\n\n=== Pre-fetched JP news (Kabutan 스크랩, verbatim) ===\n"
                        + fmt_jp_news(jp_news)
                        + "\n\n위 일본어 뉴스가 일본 종목에 대한 primary"
                        " news source이다. 영문 뉴스 (Alpha Vantage /"
                        " yfinance) 가 비어있더라도 위 리스트를 활용해"
                        " news / sentiment 분석을 진행하라. 미국 무관"
                        " 뉴스를 끌어다가 간접 추론으로 메우는 패턴"
                        " (호텔신라 2026-05-17 케이스) 금지. 위 리스트가"
                        " 비어있는 경우만 일본어 뉴스 부재로 인정."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "kabutan news injection failed for %s: %s", ticker, exc,
            )

        # JP macro from FRED (BoJ policy rate / JGB 10Y / JP CPI). yfinance
        # ^TNX is US; the JP rate environment was previously absent.
        # Mirrors the BoK ECOS KR block.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "JP" and _section_allowed(analyst_id, "fred_jp_macro"):
                from bot.fred_client import format_macro_for_prompt
                jp_macro = prefetched.get("fred_jp_macro") or {}
                jp_macro_block = format_macro_for_prompt(jp_macro, "JP")
                if jp_macro_block:
                    base += (
                        "\n\n=== Pre-fetched JP macro (FRED 미러: BoJ / OECD,"
                        " verbatim — JP-specific rate environment) ===\n"
                        + jp_macro_block
                        + "\n\n위 JP 거시 지표는 JP equity 분석의 기본"
                        " frame이다. ^TNX (미국 10Y)만 보고 '고금리"
                        " 환경' 결론을 내리지 말고, BoJ 정책금리 + JGB"
                        " 10Y의 절대 수준 + 직전 변동 방향을 같이"
                        " 인용하라. 2024년 3월 BoJ NIRP 종료 이후"
                        " 정상화 경로가 진행 중이므로, 은행 / 부동산 /"
                        " J-REIT 종목 분석에서는 이 변수가 펀더멘털을"
                        " 압도하는 단일 매크로이다."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "fred jp macro injection failed for %s: %s", ticker, exc,
            )

        # JP consensus fallback — yfinance 1st, Kabutan 2nd. yfinance
        # already covers Nikkei 225 / large TOPIX names; the Kabutan
        # path here is invoked from get_market_signals_for() at the
        # same layer the FnGuide fallback is. We don't duplicate the
        # call here — get_market_signals_for is already wired upstream
        # and returns the merged consensus line for JP via the same
        # path as KR. (See get_market_signals_for in this module.)

        # ─────────────────────────────────────────────────────────────
        # TW equity-only injections — same shape as JP, mirror the
        # KR/JP layout one-for-one. MOPS for disclosures + 內部人持股,
        # 鉅亨網 for 繁體中文 news, FRED for CBC 重貼現率 + TW 10Y +
        # CPI. All four sources degrade silently when client modules
        # or keys are missing.
        # ─────────────────────────────────────────────────────────────

        # MOPS (TW regulatory disclosures — 重大訊息 + 內部人持股 +
        # next earnings 윈도). No API key needed; HTML scrape of the
        # publicly accessible TWSE disclosure platform. Mirrors the
        # KR DART + JP EDINET injection blocks.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "TW":
                from bot.mops_client import format_mops_tw_block
                # F3-light: prefetched in parallel
                tw_disclosures = prefetched.get("mops_disclosures") or []
                tw_insiders = prefetched.get("mops_insiders") or []
                tw_window = prefetched.get("mops_window")

                # Same corporate-action staleness guard as KR DART and JP
                # EDINET branches — TW MOPS 重大訊息 carry 減資 / 無償配股
                # / 股票分割 / 庫藏股 in the 主旨 (subject) field. When
                # any of those hit, inject the universal HARD GUARD banner
                # so the analyst doesn't anchor on stale SMA/MACD/RSI
                # comparisons. Universal yfinance .splits layer (Rule A
                # layer 3) is still wired downstream as backup.
                tw_corp_action = _detect_tw_corp_action(tw_disclosures)
                if tw_corp_action:
                    base += (
                        "\n\n=== ⛔ CORPORATE ACTION IN-FLIGHT (HARD GUARD) ===\n"
                        f"{tw_corp_action['date']} MOPS 重大訊息:"
                        f" {tw_corp_action['event']}\n"
                        "이 종목은 현재 減資 / 增資 / 無償配股 / 股票分割 /"
                        " 庫藏股 등 corporate action이 진행 중이다. yfinance"
                        " 의 historical 가격 시계열은 ex-date 전후로 비조정"
                        " 또는 부분 조정 상태일 가능성이 매우 크다.\n"
                        "다음 기술적 분석 요소는 사용 금지:\n"
                        "  • 10 EMA / 50 SMA / 200 SMA 비교\n"
                        "  • MACD / RSI / Bollinger 밴드 / ATR 추세 해석\n"
                        "  • 52주 최고/최저 비교\n"
                        "시장 분석가는 (1) corporate action 자체의 정성 분석"
                        " (주주가치 영향, 유통 주식 수 변화, ex-date), (2)"
                        " 안정화 가격이 잡힐 때까지 (~5-10 거래일) 기술적"
                        " 추세 분석 보류 명시 — 두 항목만 다룬다.\n"
                        "추가 금지 (감자/분할 이후 yfinance EPS/PER/시총이"
                        " transitional해 보여도 본인이 만든 share count로"
                        " 재계산 금지)."
                    )

                mops_block = format_mops_tw_block(tw_disclosures, tw_insiders, tw_window)
                if mops_block:
                    base += (
                        "\n\n=== Pre-fetched TW market data (MOPS, verbatim —"
                        " do NOT call any tool for these numbers; use them in"
                        " the news / fundamentals / risk sections as ground"
                        " truth) ===\n"
                        + mops_block
                        + "\n\nRENDERING RULES for the MOPS block:\n"
                        " • 重大訊息: render as a bullet list, one per line,"
                        " with the date prefix preserved. Categories vary"
                        " (法說會 / 取得處分資產 / 營運狀況 / 董事會 결의)"
                        " — quote 主旨 verbatim, don't paraphrase.\n"
                        " • 內部人持股: 위 block에 명시된 행만 그대로 렌더링."
                        " 보유 주식 수 상위 N 외 추가 인사 / 經理人 / 董事 row"
                        " FABRICATION 금지. MediaTek 2454.TW 2026-05-18 케이스:"
                        " block에 5개만 있는데 분석가가 50+ 추가 行을 100,000"
                        " 주 수준으로 hallucinate (黃尚凱 / 顧大為 / ... 등"
                        " 가짜 이름 + 가짜 수치). MOPS가 반환 안 한 인사는"
                        " 본 보고서에 나타나면 안 됨.\n"
                        " • 단위 표기: 주식 수는 '주' (예: 1,600,000 주). 절대"
                        " '약 X만 원 주' / '약 X만 元 주' 같은 통화-단위 혼합"
                        " 금지. TWD '元' / KRW '원' 은 통화이지 주식 단위가"
                        " 아니다.\n"
                        " • TW pct는 MOPS가 직접 반환 안 함 — share count로만"
                        " 표시. KR DART의 % 표시처럼 percentage가 없는 게"
                        " 정상 — generic '공기업/政府 보유' narrative 절대"
                        " 만들지 마라.\n"
                        " • 다음 정기보고서 윈도: one prose sentence. TW 회계"
                        " 연도 12/31 (KR과 동일, JP 3/31과 다름) — 분기 라벨"
                        " 1Q=1-3월, 2Q=4-6월 etc."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "mops injection failed for %s: %s", ticker, exc,
            )

        # 鉅亨網 news (TW-only) — yfinance .news covers TW large-caps in
        # English with 1-3 day lag for TW-domestic events (法說會 calendar
        # / 月營收 disclosure / 內部人 transactions). 鉅亨網 fills the
        # gap with 繁體中文 articles. Mirrors the Naver / Kabutan paths.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "TW" and _section_allowed(analyst_id, "cnyes_news"):
                from bot.cnyes_client import format_news_for_prompt as fmt_tw_news
                tw_news = prefetched.get("cnyes_news") or []
                if tw_news:
                    base += (
                        "\n\n=== Pre-fetched TW news (鉅亨網 스크랩, verbatim) ===\n"
                        + fmt_tw_news(tw_news)
                        + "\n\n위 繁體中文 뉴스가 TW 종목 분석의 primary"
                        " news source이다. 영문 뉴스 (yfinance / Alpha"
                        " Vantage) 가 비어있거나 다소 lag이 있더라도 위"
                        " 리스트를 활용해 news / sentiment 분석을 진행."
                        " TW 종목 분석 시 미국·일본 무관 영문 뉴스 끌어다가"
                        " 간접 추론으로 메우는 패턴 (호텔신라 2026-05-17 /"
                        " 두산 2026-05-18 케이스) 금지. 위 리스트가 비어"
                        " 있는 경우만 繁體中文 뉴스 부재로 인정."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "cnyes news injection failed for %s: %s", ticker, exc,
            )

        # TW macro from FRED (CBC 重貼現率 / TW 10Y / TW CPI). Same
        # FRED API key as JP. Mirrors the BoK ECOS KR + FRED JP blocks.
        try:
            from bot.market import detect_market
            if detect_market(ticker) == "TW" and _section_allowed(analyst_id, "fred_tw_macro"):
                from bot.fred_client import format_macro_for_prompt
                tw_macro = prefetched.get("fred_tw_macro") or {}
                tw_macro_block = format_macro_for_prompt(tw_macro, "TW")
                if tw_macro_block:
                    base += (
                        "\n\n=== Pre-fetched TW macro (FRED 미러: CBC / OECD,"
                        " verbatim — TW-specific rate environment) ===\n"
                        + tw_macro_block
                        + "\n\n위 TW 거시 지표는 TW equity 분석의 기본"
                        " frame이다. ^TNX (미국 10Y)만 보고 '고금리 환경'"
                        " 결론을 내리지 말고, CBC 重貼現率 + TW 10Y의 절대"
                        " 수준 + 직전 변동 방향을 같이 인용. TW 央行은 美 Fed"
                        " 대비 보수적으로 움직이는 경향이 있어 (TW는 export-"
                        " driven 경제라 통화가치 안정 우선), 美 금리와 TW"
                        " 금리 sync가 1:1이 아님을 인지."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "fred tw macro injection failed for %s: %s", ticker, exc,
            )

        # CN sub-board ±limit awareness (Phase 4-CN-C, 2026-05-18).
        # STAR / ChiNext / 北交소 / HK GEM have daily price limits that
        # differ from the mainboard ±10% / HK unrestricted norm. Surface
        # the sub-board so the market analyst's volatility / momentum
        # framing is calibrated correctly — RULE 13 텍스트가 정성 인지를
        # 강제하지만, 런타임 banner 가 누락을 더 확실히 차단.
        try:
            from bot.market import detect_cn_sub_market
            sub = detect_cn_sub_market(ticker)
            if sub == "CN_A_STAR":
                base += (
                    "\n\n=== CN A-share sub-board: 上海 STAR板 (科創板) ===\n"
                    f"{ticker} 는 上海 STAR板 (688.SS prefix) 상장 종목. 일일"
                    " 가격 변동 한도가 일반 메인보드 (±10%) 와 다르게 ±20%"
                    " (신상장 첫 5거래일 ±30%). 5거래일 momentum / volatility"
                    " 평가 시 이 한도를 반영. 보고서 결론에 sub-board 한 줄"
                    " 명시 (RULE 13 STAR/ChiNext 보강)."
                )
            elif sub == "CN_A_CHINEXT":
                base += (
                    "\n\n=== CN A-share sub-board: 深圳 ChiNext (創業板) ===\n"
                    f"{ticker} 는 深圳 ChiNext (300/301.SZ prefix) 상장 종목."
                    " 일일 가격 변동 한도 ±20% (신상장 첫 5거래일 ±30%)."
                    " STAR板 과 동일한 등록제 board, 일반 메인보드 (±10%) 와"
                    " 다름. 보고서 결론에 sub-board 한 줄 명시 의무."
                )
            elif sub == "CN_A_BJSE":
                base += (
                    "\n\n=== CN A-share sub-board: 北京 北交소 (BJSE) ===\n"
                    f"{ticker} 는 北京证券交易所 (.BJ) 상장 종목. 일일 가격"
                    " 변동 한도 ±30%, yfinance 커버리지 미약 + 유동성 낮음."
                    " 5거래일 분석 보류 + 정성 평가만 작성 권고."
                )
            elif sub == "HK_GEM":
                base += (
                    "\n\n=== HK sub-board: GEM (창업판) ===\n"
                    f"{ticker} 는 HK GEM 보드 (8XXX.HK) 상장 종목. 유동성"
                    " 낮음 + 일일 거래량 영세 + 본격 분석가 커버리지 매우"
                    " 제한적. 5거래일 분석은 정성 평가 + 펀더멘털 추이 위주."
                )
        except Exception as exc:
            _analyst_log.warning("cn sub-market detection failed for %s: %s", ticker, exc)

        # ─────────────────────────────────────────────────────────────
        # CN_A + HK equity-only injections (Phase 4-CN-B, 2026-05-18).
        # Mirror the KR/JP/TW shape one-for-one:
        #  • AKShare disclosures + holders + earnings window + ST + 停牌
        #  • Eastmoney news (CN_A) / HK news (via same endpoint, best-effort)
        #  • 港股通 / 沪股通 / 深股通 flow summary
        #  • CN macro (LPR / CPI / PMI) via AKShare
        # All sources degrade silently when AKShare not installed —
        # Rule A DATA OFFLINE guard above surfaces the absence.
        # ─────────────────────────────────────────────────────────────

        # AKShare 公告 + 主要 流通股东 + 정기보고서 윈도 + ST/*ST + 停牌
        try:
            from bot.market import detect_market
            mk_cn = detect_market(ticker)
            if mk_cn in ("CN_A", "HK"):
                from bot.akshare_client import format_akshare_cn_block
                # F3-light: prefetched in parallel (8 fetches in
                # one ThreadPoolExecutor — biggest latency win across
                # all markets, ~6-8s sequential → ~3-4s parallel).
                cn_disclosures = prefetched.get("akshare_disclosures") or []
                cn_holders = prefetched.get("akshare_holders") or []
                cn_window = prefetched.get("akshare_window")
                cn_st = bool(prefetched.get("akshare_is_st"))
                cn_suspended = bool(prefetched.get("akshare_is_suspended"))

                # ST/*ST is a HARD GUARD — surface as separate banner
                # before the regular AKShare block so the analyst can't
                # bury it.  Similar to KR/JP/TW corporate-action guards
                # but the regime change is different (±5% limit + 退市
                # process vs split-adjustment confusion).
                if cn_st:
                    base += (
                        "\n\n=== ⚠️ ST/*ST 분류 (HARD GUARD — CN A-share) ===\n"
                        f"{ticker} 는 거래소의 ST 또는 *ST 특별처리 종목이다.\n"
                        "다음 항목이 일반 종목과 다르다:\n"
                        "  • 일일 변동 한도: ±5% (일반 ±10% / STAR·ChiNext ±20% 와 다름)\n"
                        "  • 退市 (delisting) 프로세스 가능성 (특히 *ST)\n"
                        "  • 자본잠식 / 재무 부실 / 회계감리 의견 거절 가능성\n"
                        "RULE 13 에 따라 펀더멘털 / 결정 노드는 다음을 명시:\n"
                        "  (1) ST 분류 자체를 결론에 한 줄로 언급\n"
                        "  (2) 5거래일 분석에서 退市 timing 변수 + 자본잠식 확률 가산\n"
                        "  (3) 일반 ±10% 가정 하의 momentum 분석 금지"
                    )

                if cn_suspended:
                    base += (
                        "\n\n=== ⛔ 停牌 (TRADING HALTED — HARD GUARD) ===\n"
                        f"{ticker} 는 현재 거래 정지 상태이다. yfinance 가격이"
                        " freeze 상태이고 일별 close 가 갱신되지 않는다.\n"
                        "다음 항목은 사용 금지 (의미 없음):\n"
                        "  • 10 EMA / 50 SMA / 200 SMA / MACD / RSI / Bollinger\n"
                        "  • 5거래일 수익률 / momentum / 추세 분석\n"
                        "  • Comps 표의 multiples (stale price 기준)\n"
                        "허용된 분석:\n"
                        "  (1) 停牌 사유 정성 분석 (실적 / M&A / 규제 / 사건)\n"
                        "  (2) 復牌 시 가격 갭 시나리오 (Bull / Base / Bear)\n"
                        "  (3) 펀더멘털은 지난 분기 数据 기준으로만 진행"
                    )

                # 공시 fetch 실패 (빈 list) 시 reconstruct 금지 guard
                # (Rule A 강화, 2026-05-19 SMIC 케이스). 분석가가 公告
                # block 비어있을 때 Eastmoney 뉴스 본문에서 'YYYY-MM-DD
                # X 공시' specific 날짜 + 정확한 공시 제목 재구성하는
                # 패턴 차단. SMIC 688981.SS 04:22 분석: AKShare
                # 'Response ended prematurely' fetch 실패했는데 분석가가
                # 5월 15일 7건 + 5월 13일 1건 공시 정확히 인용 — news
                # 출처에서 reconstruct (Eastmoney 뉴스의 '1분기 보고
                # 발표' 같은 paraphrase 를 분석가가 공시 제목으로 reverse-
                # engineer). 정확하긴 했지만 fetch 실패 시 같은 패턴이
                # 사실과 다른 공시를 fabricate 할 위험 동일.
                if not cn_disclosures:
                    base += (
                        "\n\n=== ⛔ CN/HK 공시 fetch 실패 / 빈 결과"
                        " (HARD GUARD — Rule A 강화) ===\n"
                        "AKShare get_recent_disclosures 가 빈 리스트"
                        " 반환 (network transient / endpoint 응답 잘림"
                        " / 해당 종목 최근 공시 부재 중 하나).\n"
                        "다음 패턴 절대 금지 (SMIC 688981.SS 2026-05-19"
                        " 케이스):\n"
                        "  ❌ Eastmoney 뉴스 본문에서 '1분기 보고서 발표'"
                        " 같은 paraphrase 를 보고 'YYYY-MM-DD: 2026년"
                        " 1분기 보고서' 식의 specific 공시 row 재구성.\n"
                        "  ❌ '5월 15일 회계법인 재선임 공고 / 헤지 업무"
                        " 개시' 등 정확한 공시 제목 + 날짜 fabricate"
                        " (뉴스에 이런 내용 paraphrase 있더라도 공시"
                        " 자체와는 별개).\n"
                        "  ❌ '최근 공시는 5월 N일에 집중되어 있으며'"
                        " 같은 reconstruct narrative — 公告 데이터 부재"
                        " 시 어떤 날짜 cluster 도 claim 금지.\n"
                        "올바른 처리:\n"
                        "  ✅ '본 분석 시점 公告 데이터 fetch 실패 — 최근"
                        " 공시 내역 미수집' 한 줄로 처리.\n"
                        "  ✅ 뉴스에서 공시 관련 paraphrase 발견 시"
                        " '뉴스 보도에 따르면 N분기 보고가 발표된 것으로"
                        " 알려졌다 (공시 자체 데이터 미수집)' 식으로"
                        " 뉴스 출처와 공시 출처 명확히 분리 표기."
                    )

                cn_block = format_akshare_cn_block(
                    cn_disclosures, cn_holders, cn_window, cn_st, cn_suspended,
                )
                if cn_block:
                    base += (
                        "\n\n=== Pre-fetched CN/HK market data (AKShare, verbatim —"
                        " do NOT call any tool for these numbers; use them in"
                        " the news / fundamentals / risk sections as ground"
                        " truth) ===\n"
                        + cn_block
                        + "\n\nRENDERING RULES for the AKShare CN block:\n"
                        " • 公告: render as a bullet list, one filing per line,"
                        " with the date prefix preserved. 业绩快报 / 重大资产"
                        " 重组 / 减持 / 增持 / 停牌 / 复牌 등 specific"
                        " 公告类型 keyword 가 5거래일 가격을 흔드는"
                        " 직접 신호이므로 paraphrase 금지 — 主旨 verbatim 인용.\n"
                        " • 主要 流通股东 / 지배주주: 위 block 명시 행만"
                        " 그대로 렌더링. 추가 인사 / 기관 / 펀드 row"
                        " FABRICATION 금지. AKShare 가 반환 안 한 인사는"
                        " 보고서에 나타나면 안 됨. 각 행의 [国有股] /"
                        " [境外法人股] / [境内自然人股] 라벨을 반드시 함께"
                        " 인용 — US 'insider' 의미 (회사 임원) 와 다르고"
                        " CN 의 '内部 / 内部자' 는 大股东 / 控股股东 / VIE"
                        " holding co. 를 포함한다. yfinance heldPercentInsiders"
                        " 가 60%+ 면 거의 항상 모회사 / SOE 그룹 지분이지"
                        " 회사 임원 지분이 아니다. 보고서 본문에 '내부자 N%'"
                        " 단독 표기 금지 — '지배주주 (国资 / 控股 / VIE"
                        " 등 포함) N%' 로 표기.\n"
                        " • 단위 표기: A주는 '股' / '股东' / '%'. HK 는 '股'"
                        " / '%'. 통화 prefix 는 ¥ (CNY, A-share) 또는 HK$"
                        " (HKD, HK-listed) — 절대 섞지 말 것.\n"
                        " • Dual-listed names (BYD 002594.SZ / 1211.HK, ICBC"
                        " 601398.SS / 1398.HK 등): 이 보고서의 default ticker"
                        " (yfinance 의 .SS/.SZ 또는 .HK) 통화 기준만 인용."
                        " 다른 listing 의 multiples / 시총 끌어들이지 말 것.\n"
                        " • 다음 정기보고서 윈도: A주 회계연도 12/31, 분기"
                        " 마감 4/30 (Q1+Annual), 8/31 (H1), 10/31 (Q3)."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "akshare cn injection failed for %s: %s", ticker, exc,
            )

        # 港股通 / 沪股通 / 深股통 flow — short-horizon directional signal,
        # same role as KR pykrx flow. Gate via "hsgt_flow" section tag.
        try:
            from bot.market import detect_market
            if (detect_market(ticker) in ("CN_A", "HK")
                    and _section_allowed(analyst_id, "hsgt_flow")):
                from bot.akshare_client import format_hsgt_flow_for_prompt
                hsgt = prefetched.get("akshare_hsgt_flow")
                if hsgt:
                    base += (
                        "\n\n=== Pre-fetched CN/HK investor flow"
                        " (港股通 / 沪股通 / 深股通, AKShare verbatim — quote"
                        " in 시장 분석 본문) ===\n"
                        + format_hsgt_flow_for_prompt(hsgt)
                        + "\n\nINTERPRETATION GUIDE (mandatory):"
                        " Northbound (海外 자금이 沪股通 + 深股通 통해 본토"
                        " 매수) 는 KR 외국인 순매수와 동일한 역할 — 본토"
                        " 종목 분석 시 가장 강력한 5거래일 directional"
                        " 신호. Southbound (본토 자금이 港股通 통해 HK 매수)"
                        " 는 HK 종목 분석에서 그 역할. 절대값 (억 元 / 억"
                        " HKD) 보다 방향 + 강도 추이를 중점 인용. AKShare"
                        " 가 GFW 영향으로 빈 결과 반환 시 'CN/HK flow"
                        " 데이터 미수집 (이번 실행)' 명시 + 다음 항목으로."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "hsgt flow injection failed for %s: %s", ticker, exc,
            )

        # Eastmoney 中文 news (CN_A + HK) — yfinance / Alpha Vantage
        # don't index 东方财富 / 财新 reliably; Eastmoney aggregates the
        # 主요 본토 + HK desks under one ticker tag. Mirrors Naver /
        # Kabutan / cnyes paths. Gate via "eastmoney_news" tag.
        try:
            from bot.market import detect_market
            if (detect_market(ticker) in ("CN_A", "HK")
                    and _section_allowed(analyst_id, "eastmoney_news")):
                from bot.akshare_client import format_news_for_prompt as fmt_cn_news
                cn_news = prefetched.get("akshare_news") or []
                if cn_news:
                    base += (
                        "\n\n=== Pre-fetched CN/HK news (东方财富 / AKShare,"
                        " verbatim) ===\n"
                        + fmt_cn_news(cn_news)
                        + "\n\n위 中文 뉴스가 CN/HK 종목 분석의 primary news"
                        " source이다. 영문 뉴스 (yfinance / Alpha Vantage)"
                        " 가 비어있거나 1-2일 지연되더라도 위 리스트를 활용해"
                        " news / sentiment 분석을 진행. 무관 미국 뉴스 끌어다가"
                        " 간접 추론으로 메우는 패턴 (호텔신라 2026-05-17 /"
                        " 두산 2026-05-18 케이스) 금지. 위 리스트가 비어"
                        " 있는 경우만 中文 뉴스 부재로 인정."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "eastmoney news injection failed for %s: %s", ticker, exc,
            )

        # CN macro from AKShare (LPR 1Y/5Y + CPI YoY + 제조 PMI).
        # ^TNX is US — 본토 자산 흐름은 LPR + PMI 가 직접 변수.
        # Gate via "akshare_macro" tag.
        try:
            from bot.market import detect_market
            if (detect_market(ticker) in ("CN_A", "HK")
                    and _section_allowed(analyst_id, "akshare_macro")):
                from bot.akshare_client import format_cn_macro_for_prompt
                cn_macro = prefetched.get("akshare_macro") or {}
                cn_macro_block = format_cn_macro_for_prompt(cn_macro)
                if cn_macro_block:
                    base += (
                        "\n\n=== Pre-fetched CN macro (AKShare 미러: PBoC /"
                        " 国家统计局, verbatim — CN-specific rate environment)"
                        " ===\n"
                        + cn_macro_block
                        + "\n\n위 CN 거시 지표는 CN_A / HK equity 분석의 기본"
                        " frame 이다. ^TNX (美 10Y) 만 보고 '고금리 환경'"
                        " 결론을 내리지 말고, LPR 1Y + LPR 5Y 의 절대 수준 +"
                        " 직전 변동 방향을 같이 인용. 부동산 / 银行 / 소비"
                        " 종목 분석에서는 이 변수가 펀더멘털을 압도하는"
                        " 단일 매크로. PMI < 50 = 제조업 위축 (수출 +"
                        " 설비투자 부진 신호), PMI > 50 = 확장 (소비재 +"
                        " 산업재 우호)."
                    )
        except Exception as exc:
            _analyst_log.warning(
                "akshare cn macro injection failed for %s: %s", ticker, exc,
            )

    # Phase B-1 (Step 2A, 2026-05-19): Standard View daily brief inject.
    # Universal-by-default — every market (US/KR/JP/TW/CN) receives the
    # brief because Standard View covers global macro variables (Fed /
    # 유가 / 달러 / 지정학) that drive every market's 5-day price action.
    # Silent degradation: if the .md is absent (e.g. fresh deploy before
    # first 08:00 KST timer fire, or non-VM dev environment), bridge
    # returns '' and the analyst directive is unchanged. See
    # bot/standardview_bridge.py for source attribution.
    try:
        from bot.standardview_bridge import get_standardview_block
        sv_block = get_standardview_block()
        if sv_block:
            base += sv_block
    except Exception as exc:
        _analyst_log.warning(
            "standardview brief injection failed for %s: %s", ticker, exc,
        )

    # System-generated financial tables: fundamentals analyst only.
    # Python computes numbers from yfinance directly so the LLM never
    # has to do unit conversions (the source of most number errors).
    # Each block degrades silently on yfinance failure.
    _fin_blocks = [
        ("rule1_skeleton",  _build_rule1_skeleton,   (ticker, info, _cfg, market)),
        ("cashflow_block",  _build_cashflow_block,   (ticker, info, _cfg, market)),
        ("balance_block",   _build_balance_block,    (ticker, info, _cfg, market)),
        ("ratios_block",    _build_ratios_block,     (ticker, info, _cfg, market)),
    ]
    for _key, _builder, _args in _fin_blocks:
        if _section_allowed(analyst_id, _key):
            try:
                _block = _builder(*_args)
                if _block:
                    base += "\n\n" + _block
            except Exception as exc:
                _analyst_log.warning(
                    "%s injection failed for %s: %s", _key, ticker, exc,
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


        
