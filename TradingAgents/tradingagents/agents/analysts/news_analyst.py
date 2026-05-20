from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    finalize_analyst_result,
    get_analyst_directive,
    get_global_news,
    get_language_instruction,
    get_macro_for,
    get_news,
)
from tradingagents.dataflows.config import get_config


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        symbol = state["company_of_interest"]
        instrument_context = build_instrument_context(symbol, analyst_id="news")

        # Pre-fetch macro and inject (same rationale as market_analyst):
        # the LLM was skipping the MANDATORY get_macro_context tool call
        # entirely. Doing the fetch in Python guarantees the snapshot is
        # in the prompt; the cache (agent_utils._MACRO_CACHE) means the
        # market analyst's earlier fetch is reused at zero extra cost.
        macro_snapshot = get_macro_for(symbol, current_date)
        if macro_snapshot:
            instrument_context += (
                "\n\n=== Pre-fetched macro snapshot (use VERBATIM in the"
                " '거시 경제' subsection — do NOT claim the data is"
                " unavailable, do NOT call any macro tool, this IS the"
                f" macro data for {current_date}) ===\n{macro_snapshot}"
            )
        else:
            instrument_context += (
                "\n\n=== Macro snapshot was attempted at node entry but"
                " yfinance returned no usable series. Write the '거시"
                " 경제' subsection as a single sentence acknowledging"
                " the absence; do NOT apologise; do NOT attempt a tool"
                " call. ==="
            )

        tools = [
            get_news,
            get_global_news,
            # get_macro_context intentionally removed — pre-fetched above.
        ]

        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends over the past 4 weeks. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for company-specific or targeted news searches, and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " STRUCTURE: Output the Markdown summary table FIRST (right after a 1-2 line"
            " opening), THEN the detailed body analysis. This protects the most useful"
            " reference content from being cut if the response hits the output budget."
            + " MACRO SECTION: Use the pre-fetched macro snapshot embedded"
            " in your instrument context above for the '거시 경제' subsection."
            " Do NOT call any macro tool — the snapshot is already there."
            " Quote the snapshot's actual numbers (10Y yield, VIX, dollar"
            " index, oil) verbatim and connect them to the company's exposure"
            " (e.g. high yields hurt long-duration growth multiples, oil"
            " spike helps energy names). If the snapshot is absent, write"
            " the single fallback line specified in the instrument context."
            + " RELEVANCE FILTER: This report is about ONE specific ticker. Headlines"
            " about unrelated companies (different sub-industry, no business or"
            " supplier/customer overlap) get AT MOST one bullet under '간접 시사점'"
            " — never an extended paragraph. The 2026-05-10 GOOGL run wasted budget"
            " on extended analysis of ARM vs INTC, SpaceX IPO, and Allbirds going"
            " AI-native; none of those move GOOGL meaningfully. Do not pad the"
            " report with industry-trend filler when there's no direct line to the"
            " subject ticker. If you genuinely have nothing company-specific to"
            " report, write a short honest summary instead of stretching."
            " KR/JP TICKER + UNRELATED ENGLISH NEWS — STRONG GUARD: when the"
            " subject is a `.KS`/`.KQ`/`.T` ticker and a Naver / Kabutan native-"
            " language news block was injected as the primary source, treat the"
            " yfinance .news / Alpha Vantage English feed STRICTLY as supplementary"
            " context. Random English headlines about unrelated foreign companies"
            " (호텔신라 2026-05-17 cited a US battery company headline as its sole"
            " datapoint; 두산 000150.KS 2026-05-18 cited 'Ceres Power Holdings"
            " 골드만 목표가 상향' as the primary news item — Ceres is a UK fuel"
            " cell company with zero 두산 connection) are FORBIDDEN as the main"
            " content of the body. If the KR/JP native-language news block is"
            " empty AND English news has nothing relevant, write '관련 뉴스"
            " 부재' as one honest sentence and stop — do NOT pad with unrelated"
            " English headlines presented as if they were relevant."
            + " NO TOOL APOLOGIES: Tools that wire up here (get_news, get_global_news,"
            " get_macro_context) are always available — even when an upstream API is"
            " flaky, the tool returns a partial-success message that is itself the"
            " usable input. NEVER write phrases like '도구를 사용할 수 없습니다',"
            " 'tool is not available', '죄송합니다, … 가져올 수 없', or English"
            " equivalents. If the tool's response says data was missing, mention"
            " that the data was missing in one short sentence and continue with"
            " the rest of the analysis."
            + " NO HEADER RE-EMISSION: Write ONE news report per call and stop."
            " Do NOT restate '뉴스 분석', '<Company> (TICKER) 뉴스', '<Company>"
            " (TICKER) 트레이딩 및 거시 경제 분석 보고서', or any variant of the"
            " section title in the body — the downstream renderer adds a single"
            " '## 📰 뉴스 분석' header for you. Do NOT re-emit '요약 테이블',"
            " '요약표', or 'Summary table' more than once anywhere in the body."
            " SNPS 2026-05-13 emitted three different section titles plus a"
            " 'SNEPS' typo lead-in; 한국전력공사 2026-05-17 emitted '요약 테이블'"
            " THREE times in the first 10 lines with broken markdown table"
            " syntax in between ('|---:|: ## ...' — half-rendered header"
            " breaks the renderer). Both are exactly this failure mode."
            " Start with EXACTLY ONE '요약 테이블' header + a complete"
            " markdown table (rows with values, separator line"
            " '|---|---|---|', no truncated cells), then the body, end"
            " cleanly. NEVER emit a section title or table header twice."
            " Also: NEVER mistype the ticker symbol in the body (e.g."
            " 'SNEPS' instead of 'SNPS'); double-check every ticker"
            " mention against the symbol passed in the instrument context"
            " block."
            " F7 (Hon Hai 2317.TW 2026-05-20 surfaced): NEWS FABRICATION"
            " HARD GUARD. 정책 / 규제 / 임원 발언 / 거래 승인 같은"
            " specific claim 인용 시 다음 중 하나 의무:\n"
            " (a) instrument context 의 DART/EDINET/MOPS/AKShare/Naver/"
            " Kabutan/cnyes 공시 데이터 그대로 quote — 출처 명시\n"
            " (b) 또는 yfinance .news / 외부 검색 결과 link / publisher 명시\n"
            " (c) 위 둘 다 부재면 '정책상 의심 / 출처 미확인' 한 줄로"
            " 명시 후 분석 진행. ❌ FORBIDDEN — Hon Hai 2317.TW 2026-"
            "05-20 패턴: 'Nvidia H200 중국 판매 승인 + Hon Hai 유통사'"
            " 같이 US BIS 제재 정책 (H200 to China 일반 금지) 와 충돌"
            " 하는 가짜 catalyst, 또는 'Jensen Huang 트럼프 중국 방문"
            " 인류 역사상 가장 중요한 방문' 같은 출처 없는 임원 발언"
            " 인용 금지. PM 의 결정 catalyst 가 fabricated 면 thesis"
            " 전체 invalid. 의심 news 는 인용 안 하는 게 safer."
            + get_analyst_directive()
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        result, report = finalize_analyst_result(
            prompt, llm, state["messages"], result, "news"
        )

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
