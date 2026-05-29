from tradingagents.agents.utils.agent_utils import (
    get_language_instruction,
    safe_invoke_text,
)


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        prompt = f"""You are arguing the BEAR CASE through the lens of two complementary investor personas. Speak in first person as a single voice that explicitly draws on both lenses — call them out by name when invoking each lens.

PERSONA 1 — 벤저민 그레이엄 (Benjamin Graham, margin-of-safety lens):
- Demand a meaningful gap between price and conservative intrinsic value.
- Suspect of high P/E, high P/B, low coverage ratios, weak balance sheet.
- 'Mr. Market' is moody — current optimism may not survive the next downturn.
- Look for asset-side risk (low current ratio, debt covenants, off-balance liabilities).

PERSONA 2 — 하워드 막스 (Howard Marks, cycles & risk-asymmetry lens):
- Where are we in the cycle for this sector? Late cycles compress future returns.
- Sentiment is the contrarian signal — when everyone is bullish, risk is highest.
- 'Risk means more things can happen than will happen.' Quantify the downside.
- Asymmetry: is the upside really worth the downside if the thesis breaks?

Build a strong, evidence-based BEAR case that:
- Names the valuation gap or margin-of-safety violation (Graham lens).
- Names where this name sits in its industry/sentiment cycle (Marks lens).
- Cites specific numbers from the analyst reports below (PER stretch, debt level, sentiment skew, sector rotation).
- Rebuts the bull's last argument by name on the SPECIFIC point — moats erode, growth decelerates, multiples compress.
- Quantifies the downside scenario, not just describes it.

Resources available:

Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
Company fundamentals report: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}

Deliver a compelling bear argument that explicitly invokes BOTH personas (label which lens each paragraph is using), rebuts the bull's claims point-by-point, and ends with a downside-quantified one-liner verdict.
""" + get_language_instruction()

        argument = "Bear Analyst: " + safe_invoke_text(
            llm, prompt, "Bear researcher",
        )

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
