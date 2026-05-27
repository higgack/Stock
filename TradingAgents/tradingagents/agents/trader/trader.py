"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools
import logging

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import build_instrument_context, get_language_instruction
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


_trader_log = logging.getLogger("tradingagents.trader")


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        instrument_context = build_instrument_context(company_name)

        # F1-MVP Gemini context caching (2026-05-19). Same shape as
        # research_manager — use cached_content when available.
        cache_name = state.get("gemini_cache_name", "")
        if cache_name:
            try:
                active_llm = llm.bind(cached_content=cache_name)
                active_structured_llm = bind_structured(
                    active_llm, TraderProposal, "Trader (cached)",
                )
                _trader_log.info("trader-cache: using gemini cache %s", cache_name)
            except Exception as exc:
                _trader_log.warning(
                    "trader-cache: bind(cached_content) failed (%s) — fallback",
                    exc,
                )
                active_structured_llm = structured_llm
        else:
            active_structured_llm = structured_llm
        investment_plan = state["investment_plan"]
        past_context = state.get("past_context", "")
        lessons_block = (
            "\n\nLessons from prior decisions on this name and recent cross-"
            "ticker outcomes (use these to weigh confidence — if the last "
            "BUY call already played out, factor that into whether to repeat "
            "or reverse the stance):\n"
            f"{past_context}"
            if past_context
            else ""
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent analyzing market data to make investment decisions. "
                    "Based on your analysis, provide a specific recommendation to buy, sell, or hold. "
                    "Anchor your reasoning in the analysts' reports and the research plan."
                    "\n\n5거래일 HORIZON ANCHORING (mandatory): 본 trader 권고는 5거래일"
                    " raw return (vs SPY / 섹터 ETF 알파) 기준으로 평가된다. 근거 작성 시:\n"
                    " (1) Entry / Stop Loss / Target 모두 5거래일 가격 범위 내에서 설정."
                    " 6-12개월 thesis 의 target (DCF Base case 등) 을 5거래일 target 으로"
                    " 사용 금지 — 그건 ATR / 단기 변동성 범위 outside.\n"
                    " (1.1) STOP LOSS MINIMUM WIDTH (Tencent 0700.HK 2026-05-21"
                    " surfaced): Stop Loss 와 Entry 가격 차이는 다음 중 큰"
                    " 값보다 작게 설정 절대 금지 — (a) 1.5×ATR(20), (b)"
                    " currentPrice 의 1.0% (단기 종목 기준), (c) 90일"
                    " σ 일일 변환값. 즉 일반적 변동성 종목은 Entry 대비"
                    " ≥1% 폭. ❌ FORBIDDEN — Tencent 2026-05-21: Entry"
                    " HK$447 / Stop HK$445 (-0.45%) — σ 32% × √(1/252)"
                    " = 일일 2.0% 변동성. noise 1회만 발생해도 stop"
                    " hit. 의미 없는 trade. ✅ RIGHT: Entry HK$447 /"
                    " Stop HK$440 (-1.57%, 0.78×일일 σ) 또는 더 넓게."
                    " Stop 폭이 너무 좁으면 trader 가 risk-reward 계산"
                    " 자체를 다시 해야 함 — '신호가 약하다' = '안 들어가는"
                    " 게 낫다'. tight stop 보다는 position 축소 또는"
                    " entry skip 권고.\n"
                    " (2) 근거 (rationale) 에 5거래일 dominant driver 명시: imminent"
                    " catalyst (가격 인상 / 신제품 / 어닝 / M&A 등), RSI extreme, 외국인"
                    " flow 방향, 港股통 flow, corp action HARD GUARD 인지 등.\n"
                    " (3) '장기 보유 의견' / '중장기 관점' 같은 12개월 thesis 톤 결론"
                    " 금지. 본 봇의 evaluation horizon 은 5거래일이고, trader 결정도 동일"
                    " horizon 안에서 measurable 해야 한다.\n"
                    " PLAN ALIGNMENT — STOP LOSS (8306.T 2026-05-27 surfaced): 투자"
                    " 계획(Research Manager Plan)에 명시된 Stop Loss 기준값을 Trader"
                    " 제안의 출발점으로 사용. Plan stop 과 ±5% 이상 차이나는 값으로"
                    " 임의 변경 금지 — Plan 이 200일 SMA 기반 stop 을 제시했다면"
                    " Trader 도 동일 수준(±5% 내 미세조정)만 허용. Plan stop 과 현저히"
                    " 다른 수준을 사용하려면 반드시 한 문장으로 이유를 명시 (예:"
                    " '52주 고점 근접 — Plan stop 보다 타이트하게 ¥X 로 조정함')."
                    " Entry Price 와 Position Size 도 동일 원칙 적용. Rule applies"
                    " to all analyses going forward (US + KR + JP + TW + CN_A + HK)."
                    + get_language_instruction()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}"
                    f"{lessons_block}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            active_structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
