from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    finalize_analyst_result,
    get_analyst_directive,
    get_global_news,
    get_language_instruction,
    get_macro_context,
    get_news,
)
from tradingagents.dataflows.config import get_config


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_news,
            get_global_news,
            get_macro_context,
        ]

        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends over the past 4 weeks. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for company-specific or targeted news searches, and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " STRUCTURE: Output the Markdown summary table FIRST (right after a 1-2 line"
            " opening), THEN the detailed body analysis. This protects the most useful"
            " reference content from being cut if the response hits the output budget."
            + " MANDATORY: Call `get_macro_context(curr_date)` once at the start so the"
            " 거시 경제 section is grounded in the actual current 10Y yield, VIX, dollar"
            " index, oil, and other headline macro levels — do NOT rely on training-time"
            " knowledge for current rate / commodity numbers. Quote the snapshot's"
            " values verbatim and connect them to the company's exposure (e.g. high"
            " yields hurt long-duration growth multiples, oil spike helps energy names)."
            + " RELEVANCE FILTER: This report is about ONE specific ticker. Headlines"
            " about unrelated companies (different sub-industry, no business or"
            " supplier/customer overlap) get AT MOST one bullet under '간접 시사점'"
            " — never an extended paragraph. The 2026-05-10 GOOGL run wasted budget"
            " on extended analysis of ARM vs INTC, SpaceX IPO, and Allbirds going"
            " AI-native; none of those move GOOGL meaningfully. Do not pad the"
            " report with industry-trend filler when there's no direct line to the"
            " subject ticker. If you genuinely have nothing company-specific to"
            " report, write a short honest summary instead of stretching."
            + " NO TOOL APOLOGIES: Tools that wire up here (get_news, get_global_news,"
            " get_macro_context) are always available — even when an upstream API is"
            " flaky, the tool returns a partial-success message that is itself the"
            " usable input. NEVER write phrases like '도구를 사용할 수 없습니다',"
            " 'tool is not available', '죄송합니다, … 가져올 수 없', or English"
            " equivalents. If the tool's response says data was missing, mention"
            " that the data was missing in one short sentence and continue with"
            " the rest of the analysis."
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
