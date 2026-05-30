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
            + " F4 v4 DASH-FORMAT BAN (BYD 002594.SZ 2026-05-20 surfaced):"
            " 표 cell 구분자 로 long-dash `—` 또는 `-` 의 chain"
            " ('A — B — C — D') 사용 절대 금지. reader 가 항목 label"
            " 을 구별 못함. 정상 markdown pipe 형식 ('| 항목 | 값 |' +"
            " '|---|---|' separator) 또는 inline label 형식 ('항목 X"
            " / 항목 Y / ...') 만 허용."
            + " F7 v2 ANALYST RATING SOURCE MANDATORY (Tencent 0700.HK"
            " 2026-05-21 surfaced): 'Goldman/Morgan Stanley/JP Morgan/"
            " 골드만/모건스탠리/UBS/HSBC/Jefferies/Bernstein' 등 specific"
            " 애널리스트 / 투자은행 의 종목 등급 (매수 / Buy / 매도 등)"
            " 또는 목표가 인용 시 다음 중 하나 의무: (a) yfinance .news"
            " 또는 외부 검색 결과 link 명시, (b) publisher (Bloomberg /"
            " Reuters / cnyes / 鉅亨網) + 날짜 명시, (c) 위 둘 다 부재면"
            " '출처 미확인 — 본 시점 검증 보류' 한 줄. ❌ FORBIDDEN —"
            " Tencent 0700.HK 2026-05-21: '골드만삭스가 AI 전략을 긍정적"
            " 으로 평가하며 매수 등급을 재확인' (출처 link / 날짜 / 보고서"
            " 번호 등 부재). PM 이 이 정보를 catalyst 로 채택했기 때문에"
            " fabricated 면 thesis invalid.\n"
            + " F11 NEWS SELF-DEDUP (Tencent 0700.HK 2026-05-21 surfaced):"
            " 본 분석 안에서 같은 paragraph / 같은 문장이 2회 이상 등장"
            " 금지. ❌ FORBIDDEN: '고금리 환경 장기화는 금융자산 가격에"
            " 하방 압력으로 작용할 수 있으며, 특히 그림자금융의 리스크"
            " 관리 필요성이 부각된다.' 같은 문장이 본문에 2회 출현"
            " (Tencent 2026-05-21 패턴). LLM 이 동일 source 반복 cite"
            " 시 paraphrase 충분히 변화시키거나 한 번만 사용. summary"
            " 표 / 결론 section 에서는 압축형 재인용 OK 지만 본문 paragraph"
            " 중복은 금지."
            " F13 FLOW TIMELINE COHERENCE (Meituan 3690.HK 2026-05-28 surfaced):"
            " instrument context 에 港股통 / KRX 수급 / KIS 외인-기관 flow 등"
            " '최근 5거래일 net buy/sell' LIVE API 수치가 주입돼 있으면, 그"
            " 수치가 종목의 단기 수급 진실. 4주간 뉴스 헤드라인의 정성적"
            " flow 코멘트 ('순매도 지속', '매수세 둔화' 등) 가 LIVE 수치와"
            " 방향이 다르면 ❌ 둘 다 그대로 cite 금지 — reader 가 '자금이"
            " 들어오는지 빠지는지' 모름. ✅ 반드시 transition 으로 명시:"
            " '5/13-5/15 N억 순매도 → 최근 5거래일 LIVE 수치 +M억 net buy'"
            " 같은 시계열 reversal 또는 acceleration 한 줄 추가. ❌ FORBIDDEN"
            " — Meituan 2026-05-28: 뉴스가 5/13 + 5/15 net SELL 헤드라인을"
            " 인용하면서 같은 리포트의 시장 섹션은 港股통 +96억 HKD net BUY"
            " 5거래일 연속 (LIVE) 을 인용 — 동일 자금에 대해 두 시점 둘 다"
            " 그대로 두고 transition 명시 안 함 = reader 혼란. 두 시점 모두"
            " 유효하면 '중순 매도 → 후반 매수 전환' 같이 reversal narrative."
            + " F7 v3 PRICE-CLAIM RECONCILE (NOK 2026-05-30 surfaced): 뉴스에"
            " '52주 최고가 경신' · 'X% 급등/폭등/급락' 같은 가격 수준·변동률"
            " claim 인용 시, instrument context 의 canonical 현재가 + 52주"
            " 최고/최저 + 50일 SMA 이격도와 **반드시 대조**. 모순 시 ❌ 그대로"
            " 복창 금지 — 뉴스의 과장을 지적하고 실제 수치로 정정. ❌ FORBIDDEN"
            " — NOK 2026-05-30: 현재가 $14.84 (52주 최고 $16.63, 50일 SMA 대비"
            " +31%) 인데 '52주 최고가 경신 + AI 수요로 140% 급등' 서술 (다른"
            " AI 테마주 NVDA/MU 헤드라인을 본 종목에 전가한 환각). 존재하지"
            " 않는 수익률·신고가를 본 종목에 갖다 붙이지 말 것. 뉴스 헤드라인"
            " 의 수치는 반드시 본 종목 canonical 가격 팩트로 교차검증."
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

        # Analysts run on Flash; the context cache is Pro-only, so binding it
        # here is a model-mismatch no-op (verified 2026-05-26). Use llm directly.
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
