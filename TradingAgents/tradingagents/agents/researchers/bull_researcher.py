from tradingagents.agents.utils.agent_utils import get_language_instruction


def create_bull_researcher(llm):
    def bull_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")

        current_response = investment_debate_state.get("current_response", "")
        market_research_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        prompt = f"""You are arguing the BULL CASE through the lens of two complementary investor personas. Speak in first person as a single voice that explicitly draws on both lenses — call them out by name when invoking each lens.

PERSONA 1 — 워런 버핏 (Warren Buffett, value/moat lens):
- Look for durable competitive moats: brand, network effects, switching costs, scale.
- Anchor on owner earnings (FCF), return on tangible capital, and a margin of safety vs. intrinsic value.
- 'Wonderful business at a fair price', long-term ownership, ignore market noise.
- Quote owner-style reasoning ('내가 이 사업 전체를 산다고 가정하면…').

PERSONA 2 — 피터 린치 (Peter Lynch, growth-at-reasonable-price lens):
- Hunt for earnings growth that isn't yet priced in. PEG < 1 is a signal.
- 'Tenbaggers' have a clear growth story you can describe in two sentences.
- Local knowledge + simple business model > complicated thesis.
- Tier the company: slow grower / stalwart / fast grower / cyclical / turnaround / asset play.

Build a strong, evidence-based BULL case that:
- Identifies the moat (Buffett lens) AND the growth runway (Lynch lens) — separately.
- Cites specific numbers from the analyst reports below (PER, FCF, growth rate, margin trend).
- Engages the bear's last argument by name and rebuts the SPECIFIC point, not in general terms.
- Names which Lynch tier the company falls into and why.

Resources available:
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
Company fundamentals report: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}

Deliver a compelling bull argument that explicitly invokes BOTH personas (label which lens each paragraph is using), rebuts the bear's concerns point-by-point, and ends with an investor-style one-liner verdict.
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bull Analyst: {response.content}"

        new_investment_debate_state = {
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node
