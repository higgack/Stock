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


_PEER_MULTIPLES_CACHE: dict[str, str] = {}


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
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).info or {}
        # Canonical company name first — used as the row label so
        # analysts can't substitute their own guess.
        nm = raw.get("longName") or raw.get("shortName") or ""
        if isinstance(nm, str) and nm.strip():
            company_name = nm.strip()
        # PER (trailing) — most universal multiple
        per = raw.get("trailingPE")
        if isinstance(per, (int, float)) and not (isinstance(per, float) and per != per) and 0 < per < 1000:
            out_parts.append(f"PER {per:.1f}")
        # Forward PER — useful when TTM is distorted by one-offs
        fper = raw.get("forwardPE")
        if isinstance(fper, (int, float)) and not (isinstance(fper, float) and fper != fper) and 0 < fper < 1000:
            out_parts.append(f"Fwd PER {fper:.1f}")
        # PSR
        psr = raw.get("priceToSalesTrailing12Months")
        if isinstance(psr, (int, float)) and not (isinstance(psr, float) and psr != psr) and 0 < psr < 100:
            out_parts.append(f"PSR {psr:.2f}")
        # PBR
        pbr = raw.get("priceToBook")
        if isinstance(pbr, (int, float)) and not (isinstance(pbr, float) and pbr != pbr) and 0 < pbr < 100:
            out_parts.append(f"PBR {pbr:.2f}")
        # EV/EBITDA
        evebitda = raw.get("enterpriseToEbitda")
        if isinstance(evebitda, (int, float)) and not (isinstance(evebitda, float) and evebitda != evebitda) and -100 < evebitda < 1000:
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
    },
    # 감정 (sentiment): doesn't quantify rates or KRX/HSGT flow. Keeps news
    # blocks (sentiment fuel) and peer set (Comps consistency).
    "social": {
        "krx_flow", "hsgt_flow", "kis_supply",
        "bok_macro", "fred_jp_macro", "fred_tw_macro", "akshare_macro",
    },
    # 뉴스 (news): keeps everything except flow data (numbers without
    # narrative don't add to news synthesis).
    "news": {"krx_flow", "hsgt_flow", "kis_supply"},
    # 펀더멘털 (fundamentals): doesn't read native-language news, doesn't
    # need short-horizon flow. Keeps macro (rate-sensitive valuation).
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

    Returns empty dict for `market in ("US",)` — no extra fetches
    needed (yfinance .info already cached in _INSTRUMENT_INFO_CACHE).
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
            )
            tasks["pykrx_flow"] = lambda: get_kr_trading_flow(ticker, days_back=5)
            tasks["pykrx_foreign_trend"] = lambda: get_kr_foreign_ownership_trend(ticker, days_back=30)
            tasks["pykrx_short_trend"] = lambda: get_kr_short_balance_trend(ticker, days_back=30)
            # D1: pykrx 시가총액 + 최신 close — yfinance .info marketCap
            # 및 currentPrice cross-check 용. canonical 시총 directive
            # 에서 사용.
            tasks["pykrx_market_cap"] = lambda: get_kr_market_cap(ticker)
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


def _section_allowed(analyst_id: str | None, section: str) -> bool:
    """Return True when this section should appear in the analyst's
    prompt. `analyst_id is None` (non-analyst callers — PM, trader,
    research manager) always sees every section."""
    if analyst_id is None:
        return True
    return section not in _ANALYST_CONTEXT_EXCLUDE.get(analyst_id, set())


def build_instrument_context(ticker: str, analyst_id: str | None = None) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified
    tickers and adjust their data expectations for non-equity products.

    `analyst_id` (optional): one of "market" / "social" / "news" /
    "fundamentals". When set, heavy sections not relevant to that
    analyst are excluded (see `_ANALYST_CONTEXT_EXCLUDE`). Non-analyst
    callers (portfolio manager, trader, research manager) pass None /
    omit the argument and see the full context — backward-compatible."""
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
                    + "\n".join(peer_lines) + "\n"
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
                pykrx_mc_data = prefetched.get("pykrx_market_cap")
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
                    # pykrx close vs yfinance currentPrice cross-check
                    if (isinstance(px, (int, float)) and px > 0
                            and pykrx_close > 0):
                        px_diff = abs(pykrx_close - px) / px
                        if px_diff > 0.01:
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
        for window_label, key in [("50일 SMA", "fiftyDayAverage"),
                                  ("200일 SMA", "twoHundredDayAverage")]:
            sma = info.get(key)
            if not (isinstance(sma, (int, float)) and sma > 0):
                continue
            gap = abs(px - sma) / sma
            if gap <= 0.30:
                continue
            direction = "below" if px < sma else "above"
            # Fix J upgrade (2026-05-19 노바렉스 194700.KS surfaced) —
            # ⚠️ → ⛔ HARD GUARD. 노바렉스 200d gap 36% (current ₩10,140
            # vs 200d ₩15,958) 였는데 분석가가 banner 를 silent omit 한
            # 채 MACD/RSI/EMA/Bollinger 모두 매수 신호로 인용. 단순
            # warning 으로는 약함 — Fix E (macro suspect) 처럼 narrative
            # cite 자체 금지로 강화.
            base += (
                f"\n\n=== ⛔ PRICE GAP SANITY (HARD GUARD — Fix J 강화) ===\n"
                f"current price {_sym}{_fmt.format(px)} is {gap*100:.0f}%"
                f" {direction} the {window_label}"
                f" {_sym}{_fmt.format(sma)}. 이 정도 gap 은 stock-split"
                f" adjustment lag / corp action / 거래정지 후 가격"
                f" reset 등의 데이터 quality 문제 의심 — 일반적인 추세"
                f" 변동 아님.\n\n"
                f"다음 사용 절대 금지 (RULE 위반):\n"
                f"  ❌ 10 EMA / 50 SMA / 200 SMA 비교 또는 cross 신호"
                f" (골든/데드 크로스 등) 인용\n"
                f"  ❌ MACD / RSI / Bollinger 밴드 / ATR 기반 매수/매도"
                f" 신호 narrative\n"
                f"  ❌ '단기 상승 모멘텀' / '하락 추세 지속' / '과매수/"
                f"과매도' 같은 directional 톤 결론 — 데이터 자체가 stale"
                f" 인데 추세 해석 의미 없음\n\n"
                f"올바른 처리:\n"
                f"  ✅ canonical current price ({_sym}{_fmt.format(px)})"
                f" 한 줄 인용 + '데이터 transitional, 기술 지표 분석 보류'"
                f" 명시\n"
                f"  ✅ 펀더멘털 지표 (매출 / 영업이익 / 부채비율 등) 만"
                f" 분석 진행 — 가격 기반 multiples (PER / PBR / EV-EBITDA)"
                f" 도 transitional 가능성 인지\n"
                f"  ✅ KRX 시장경보 / DART 공시 / 거래정지 status 확인"
                f" 권고 — 노바렉스 194700.KS 2026-05-19 케이스: 200d gap"
                f" 36% + 52w low > current 가 동반 — corp action 또는"
                f" 거래정지 진행 가능성 매우 큼"
            )
            break  # one warning per analysis is enough — don't spam both windows

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
                " 시 '2026년 3월기 1Q (4-6월)' 식으로 표기."
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

    # F3-light: parallel prefetch heavy I/O for the detected market.
    # Returns dict keyed by source tag (e.g. 'dart_disclosures',
    # 'edinet_holders', 'akshare_macro'). Each downstream try-block
    # below uses prefetched.get(tag) instead of re-fetching, eliminating
    # sequential I/O wait. Empty dict for US / ETF / unknown markets.
    if qt in ("ETF", "ETN", "MUTUALFUND") or market == "US":
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
        else:
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
                if corp_action:
                    base += (
                        "\n\n=== ⛔ CORPORATE ACTION IN-FLIGHT (HARD GUARD) ===\n"
                        f"{corp_action['date']} DART 공시: {corp_action['event']}\n"
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
                    if block:
                        base += (
                            "\n\n=== KIS 단기 수급 데이터 (7종, verbatim —"
                            " 이 수치를 그대로 본문에 인용할 것) ===\n"
                            + block
                            + "\n\n" + KIS_INTERP_GUIDE
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


        
