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
            prompt, llm, state["messages"], result, "social"
        )

        return {
            "messages": [result],
            "sentiment_report": report,
        }

    return social_media_analyst_node
