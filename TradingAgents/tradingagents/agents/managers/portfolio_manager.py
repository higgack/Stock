"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**DATA-AVAILABILITY GUARD (mandatory):** If the analyst reports
materially fail (e.g. 'data unavailable', '데이터 없음', '정보
없음', '데이터 부족', 'currentPrice 미수집', '재무제표 검색
불가') — i.e. you cannot quote even one specific number from the
fundamentals or market sections — your verdict MUST be **Hold**,
with the rationale '데이터 부족으로 평가 불가, 사용자 재시도 필요'.
Do NOT pick Sell / Underweight on a 'no data = risk' line of
reasoning; that's an artificial bearish signal manufactured from
the absence of evidence. NAVER on 2026-05-17 took exactly that
wrong path — fundamentals had 'PER 정보 없음 / EPS 정보 없음'
across the board and the PM still output Sell, which actively
mislead the user. The correct response to empty data is to
neither buy nor sell; ask for a retry.

**ANALYST CONSENSUS OVERRIDE DISCIPLINE (mandatory):** When the
four analysts' stance bar shows unanimous (4-0) or near-unanimous
(3-0 with 1 abstain) agreement on a direction AND the Wall Street
/ KR analyst consensus rating is 강매수 / 매수, you MAY override
to the opposite direction (e.g. Underweight / Sell) ONLY if you
explicitly name at least ONE of these triggers in your rationale:
(a) 5-day-horizon technical extreme — RSI > 75 for Buy-reverse,
    RSI < 25 for Sell-reverse
(b) imminent specific catalyst — earnings within ±5 days, FOMC,
    guide cut, regulatory event in the next 5 days
(c) stance-vs-decision mismatch detector explicit warning text
    was visible in your prompt
(d) data-availability HOLD per the GUARD above
Without ONE of these triggers named, default to the analyst
direction: Buy / Overweight when analysts lean buy, Hold when
split, Sell / Underweight when analysts lean sell. The pattern
'분석가 4명 합의 보유 + 컨센서스 강매수 → PM Underweight on a
single RSI signal' (현대모비스 / 호텔신라 / 한전 2026-05-17
cluster) is exactly what this rule prevents. Unanimous analyst
signals plus consensus should not flip on a single technical
indicator without explicit justification.

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        final_trade_decision = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
        )

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
        }

    return portfolio_manager_node
