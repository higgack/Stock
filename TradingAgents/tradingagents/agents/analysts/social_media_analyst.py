from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    finalize_analyst_result,
    get_analyst_directive,
    get_language_instruction,
    get_news,
)
from tradingagents.dataflows.config import get_config


def create_social_media_analyst(llm):
    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"], analyst_id="social")

        tools = [
            get_news,
        ]

        system_message = (
            "You are a social media and company specific news researcher/analyst tasked with analyzing social media posts, recent company news, and public sentiment for a specific company over the past 4 weeks. You will be given a company's name your objective is to write a comprehensive long report detailing your analysis, insights, and implications for traders and investors on this company's current state after looking at social media and what people are saying about that company, analyzing sentiment data of what people feel each day about the company, and looking at recent company news. Use the get_news(query, start_date, end_date) tool to search for company-specific news and social media discussions. Try to look at all sources possible from social media to sentiment to news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " STRUCTURE: Output the Markdown summary table FIRST (right after a 1-2 line"
            " opening), THEN the detailed body analysis. This protects the most useful"
            " reference content from being cut if the response hits the output budget."
            + " F4 v4 DASH-FORMAT BAN (BYD 002594.SZ 2026-05-20 surfaced):"
            " 표 cell 구분자 로 long-dash `—` 또는 `-` 의 chain"
            " ('A — B — C — D') 사용 절대 금지. reader 가 multiples /"
            " 항목 label 을 구별 못함. 표는 정상 markdown pipe 형식"
            " ('| 항목 | 값 |' + '|---|---|' separator) 또는 inline"
            " label 형식 ('항목 X / 항목 Y / ...') 만 허용."
            + " F9 MID-RENDER MARKDOWN BREAKAGE BAN (BYD 002594.SZ 2026-"
            " 05-20 surfaced): 표 헤더 한 줄 안에 `:----:` 같은 partial"
            " separator + `##` markdown 헤더가 섞여 들어가서 표 자체가"
            " 깨진 채 rendering 되는 패턴 금지. 표를 시작했으면 헤더"
            " row + separator row '|---|---|---|' + data row 3개 이상"
            " 까지 한 번에 완결한 뒤에야 다음 section header (`##`) 또는"
            " prose 시작. 표 도중에 `##` 또는 다른 markdown block 삽입"
            " FORBIDDEN. 표가 길어질 것 같으면 inline label 형식으로"
            " 전환하는 게 안전."
            + " F12 STALE-DATA HALLUCINATION HARD GUARD (CRM 2026-05-28"
            " surfaced): 너는 오직 get_news 도구가 반환한 데이터와"
            " instrument context 에 주입된 뉴스/소셜 블록만 사용해서"
            " 분석한다. 도구 결과가 비어 있거나 해당 종목 관련 항목이"
            " 없으면, 사전학습 지식으로 뉴스·실적 발표·가이던스·M&A·"
            " 임원 발언 같은 구체적 사건을 절대 창작하지 마라. ❌"
            " FORBIDDEN — CRM 2026-05-28 패턴: get_news 가 빈 결과를"
            " 반환했는데도 '지난 분기 실적이 예상치를 상회했으나"
            " 가이던스 실망으로 주가 하락' 같은 과거(2024년) 학습"
            " 데이터를 현재 사건인 것처럼 과거형으로 서술 (같은 run 의"
            " 뉴스 분석가는 동일 데이터로 '뉴스 부재'를 정직하게 출력"
            " 했음 — 두 분석가의 timeline 이 어긋나면 PM thesis 가"
            " 오염된다). 특히 실적 발표가 임박/당일인 종목은, 아직"
            " 발표되지 않은 실적을 '이미 발표했다'고 단정하는 환각이"
            " 치명적이다. 데이터가 없으면 '최근 4주간 유의미한 소셜/"
            " 뉴스 시그널이 수집되지 않았습니다' 한 줄로 정직하게"
            " 출력하고, 가능하면 기술적 지표 / 컨센서스 등 실제 주입된"
            " 데이터로만 분석을 이어가라. 확인 불가한 사건의 날짜 / 방향"
            " 단정 금지."
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
            prompt, llm, state["messages"], result, "social"
        )

        return {
            "messages": [result],
            "sentiment_report": report,
        }

    return social_media_analyst_node
