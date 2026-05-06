from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_analyst_directive,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
)
from tradingagents.dataflows.config import get_config


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " STRUCTURE: Output the Markdown summary table FIRST (right after a 1-2 line"
            " opening), THEN the detailed body analysis. This protects the most useful"
            " reference content from being cut if the response hits the output budget."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + " UNITS ARE MANDATORY: every monetary value you cite from balance sheet, cash flow,"
            " or income statement MUST be followed by an explicit unit."
            " ❌ WRONG: '총 자산: 10,176 — 9,710 — 9,395 — 8,932'"
            " ✅ RIGHT: '총 자산 (백만 달러): 10,176 — 9,710 — 9,395 — 8,932'"
            " ❌ WRONG: '시가총액: 2217억 8778만 3168 달러' (Korean place-by-place reading)"
            " ✅ RIGHT: '시가총액: 약 2218억 달러' or '시가총액: $221.8B'"
            " Always abbreviate large dollar amounts to '약 X.X조/억/백만 달러' or '$X.XB/M' rather"
            " than spelling out every place value."
            + " SUMMARY TABLE GUIDELINES — try to follow these, but if the data isn't"
            " available just use what you have. Don't emit empty placeholders."
            " (a) When you list time-series values, label each with its period if you can"
            " (e.g. 'FY25 281.7 | FY24 245.1' rather than '281.7 — 245.1 — ...')."
            " (b) For point-in-time values (시가총액, PER, 베타, 52주 최고/최저, 이동평균),"
            " just give a single value — don't pad with '해당 없음 — 해당 없음'."
            " (c) Try to include both annual (last 4 fiscal years) AND the most recent"
            " quarter for major statement items (매출, 순이익, EPS, 영업활동 현금흐름)."
            " Quarterly data must use 'Q1 26', 'Q4 25' style labels, not just numbers."
            " (d) Useful ratio metrics to surface in the table when available:"
            " 영업이익률, 순이익률, ROE, ROA, 부채비율, 유동비율, 잉여현금흐름 마진."
            " (e) Show the latest year-over-year growth rate (e.g. '+15%') alongside"
            " the most recent value when you can compute it from the data."
            " If a metric isn't in the tool output, simply omit that bullet — never"
            " emit a row whose values are all blank, all '-', or all '해당 없음'."
            " For point-in-time metrics like 시가총액 / PER / 베타 / 52주 최고/최저 / 이동평균,"
            " a bullet should have ONE value and stop — do not append placeholders."
            " ❌ WRONG: '시가총액: $36.94B — - — - — - — -'"
            " ✅ RIGHT: '시가총액: $36.94B'"
            " (f) TTM vs FY consistency: when you cite a TTM figure (e.g. 'PER (TTM)',"
            " 'EPS (TTM)', 'TTM 잉여현금흐름') and a fiscal-year figure for the same"
            " metric, ALWAYS label which is which and never blend them. If the TTM and"
            " latest annual numbers diverge significantly, briefly note the reason in"
            " prose (e.g. 'TTM은 최근 분기 대규모 자본 지출로 음수')."
            " Do NOT use HTML break tags like '<br>' inside table cells — Telegram"
            " ignores them and they show as literal text. Use real newlines or"
            " separate bullets instead."
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

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
