"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(str, Enum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    # Field order is deliberate: the free-text rationale is generated BEFORE
    # the recommendation enum so the model reasons its way to the verdict
    # rather than committing to a rating first and rationalising afterwards.
    # Structured-output providers (Gemini response_schema / OpenAI json_schema)
    # generate fields in schema-declaration order, so reasoning-before-enum
    # reduces the text↔enum divergence seen on ALAB 2026-05-26 (PM thesis
    # argued Hold but the enum came out Buy). strategic_actions depends on the
    # recommendation, so it follows the enum.
    # Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation that follows from the rationale "
            "above. Exactly one of Buy / Overweight / Hold / Underweight / "
            "Sell. Reserve Hold for situations where the evidence on both "
            "sides is genuinely balanced; otherwise commit to the side with "
            "the stronger arguments."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including position sizing guidance consistent with the rating."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    # Reasoning-before-enum ordering (see ResearchPlan note): the case is
    # written first, then the action enum follows from it, then the numeric
    # levels that depend on the chosen action.
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    action: TraderAction = Field(
        description=(
            "The transaction direction that follows from the reasoning above. "
            "Exactly one of Buy / Hold / Sell."
        ),
    )
    entry_price: Optional[float] = Field(
        default=None,
        description="Optional entry price target in the instrument's quote currency.",
    )
    stop_loss: Optional[float] = Field(
        default=None,
        description="Optional stop-loss price in the instrument's quote currency.",
    )
    position_sizing: Optional[str] = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )
    kill_trigger: Optional[str] = Field(
        default=None,
        description=(
            "Thesis-breaking event/data (NOT a price level — stop_loss covers "
            "price). One short line naming the specific news, guidance, or "
            "macro datum that would invalidate the case and warrant immediate "
            "exit/reverse regardless of price. Examples: 'Q1 revenue miss vs "
            "guide', '美 對中 수출규제 발표', '50일 SMA 데드크로스 + 거래량 "
            "급증', '경쟁사 qual 통과 공식 발표'. Distinct from stop_loss "
            "(price-based) and Plan catalyst (positive trigger)."
        ),
    )


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.extend(["", f"**Entry Price**: {proposal.entry_price}"])
    if proposal.stop_loss is not None:
        parts.extend(["", f"**Stop Loss**: {proposal.stop_loss}"])
    if proposal.position_sizing:
        parts.extend(["", f"**Position Sizing**: {proposal.position_sizing}"])
    if proposal.kill_trigger:
        parts.extend(["", f"**Kill Trigger**: {proposal.kill_trigger}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    # Field order is deliberate (ALAB 2026-05-26 fix): investment_thesis (the
    # evidence-anchored reasoning) is generated FIRST so the model reasons to
    # its verdict; the rating enum follows the thesis; the executive_summary
    # action plan and the numeric targets — all consequences of the rating —
    # come after. Structured-output providers generate fields in declaration
    # order, so this minimises the text↔enum divergence where the PM thesis
    # argued Hold but the rating enum emitted Buy (committed before reasoning).
    # render_pm_decision keeps the display order (Rating first) so parse_rating
    # and the report layout are unchanged.
    # Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. Write this BEFORE settling on the rating. If prior lessons "
            "are referenced in the prompt context, incorporate them; otherwise "
            "rely solely on the current analysis."
        ),
    )
    rating: PortfolioRating = Field(
        description=(
            "The final position rating that follows from the investment thesis "
            "above. Exactly one of Buy / Overweight / Hold / Underweight / "
            "Sell, picked based on the analysts' debate."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    # 외부 레포 검토(ZhuLinsen/daily_stock_analysis `position_advice`·
    # `watch_conditions`·`data_limitations`, 2026-10-22) — 판정 하나만으로는
    # 이미 가진 사람과 아직 없는 사람이 할 일이 갈린다. 결론(rating) 뒤에
    # 생성되도록 rating 다음에 둔다(필드 순서 = 생성 순서). 전부 선택 필드라
    # 옛 출력·자유문 폴백은 그대로 렌더된다.
    holder_action: Optional[str] = Field(
        default=None,
        description=(
            "One sentence: what an investor who ALREADY HOLDS the stock should do "
            "over the 5-trading-day horizon, consistent with the rating (e.g. keep, "
            "trim, add on a pullback to a level quoted in the reports)."
        ),
    )
    new_entry_action: Optional[str] = Field(
        default=None,
        description=(
            "One sentence: what an investor who does NOT hold the stock should do, "
            "consistent with the rating (e.g. wait, enter in tranches, avoid)."
        ),
    )
    watch_conditions: Optional[list[str]] = Field(
        default=None,
        description=(
            "Up to 3 concrete, checkable conditions that would change this rating "
            "within 5 trading days. Each must name a price level, indicator value, "
            "or dated event taken from the analysts' reports. Never invent levels."
        ),
    )
    data_limitations: Optional[list[str]] = Field(
        default=None,
        description=(
            "Data that was missing, stale, or contradictory in the analyst reports "
            "and limited this decision. Empty list if none. Never invent limitations."
        ),
    )
    price_target: Optional[float] = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )

    # 선택 필드가 결정 전체를 깨지 않게 — 툴콜 경로에서 모델이 배열을 문자열로
    # 보내면 ValidationError 로 구조화 결정 전체를 잃는다(독립 리뷰 M4).
    @field_validator("watch_conditions", "data_limitations", mode="before")
    @classmethod
    def _coerce_str_list(cls, v):
        if v is None or isinstance(v, list):
            return [x for x in v if isinstance(x, str)] if isinstance(v, list) else v
        if isinstance(v, str):
            return [v] if v.strip() else None
        return None

    @field_validator("holder_action", "new_entry_action", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        return v if v is None or isinstance(v, str) else None
    time_horizon: Optional[str] = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    if decision.time_horizon:
        parts.extend(["", f"**Time Horizon**: {decision.time_horizon}"])
    # 새 섹션은 **맨 뒤**에 둔다 — 요약은 앞 4줄만 싣고, 차트의 목표가·손절선
    # 파서(`chart_data.parse_trade_levels`)는 첫 매치를 쓰므로 앞에 두면
    # Price Target 을 가로챈다(2026-10-22 독립 리뷰 M2). 그리고 PM override
    # 트리거 검사는 이 섹션들을 잘라내고 본다(`pm_core_text`).
    if decision.holder_action:
        parts.extend(["", f"{PM_HOLDER_HEAD}: {decision.holder_action}"])
    if decision.new_entry_action:
        parts.extend(["", f"{PM_ENTRY_HEAD}: {decision.new_entry_action}"])
    _wc = [c.strip() for c in (decision.watch_conditions or []) if c and c.strip()][:3]
    if _wc:
        parts.extend(["", f"{PM_WATCH_HEAD}:"] + [f"- {c}" for c in _wc])
    _dl = [c.strip() for c in (decision.data_limitations or []) if c and c.strip()]
    if _dl:
        parts.extend(["", f"{PM_LIMITS_HEAD}:"] + [f"- {c}" for c in _dl])
    return "\n".join(parts)


PM_HOLDER_HEAD = "**보유 중이라면**"
PM_ENTRY_HEAD = "**미보유라면**"
PM_WATCH_HEAD = "**판정을 바꿀 조건**"
PM_LIMITS_HEAD = "**데이터 한계**"
# 등급에 딸린 할 일 — 등급이 바뀌면(강제 HOLD) 같이 무효가 된다.
PM_ACTION_HEADS = (PM_HOLDER_HEAD, PM_ENTRY_HEAD, PM_WATCH_HEAD)
PM_EXTRA_HEADS = PM_ACTION_HEADS + (PM_LIMITS_HEAD,)


def _is_head(para: str, heads) -> bool:
    return para.lstrip().startswith(heads)


def pm_core_text(md: str) -> str:
    """결정문에서 보조 섹션(할 일·조건·데이터 한계)을 걷어낸 본문.

    PM override 트리거 검사는 **근거(thesis)** 에 트리거가 있는지를 묻는다 —
    '판정을 바꿀 조건' 의 '실적 발표'·'D-5', '데이터 한계' 의 '데이터 없음' 은
    근거가 아니라 부속 문구라, 그대로 두면 분석가 전원 반대 방향의 PM 등급이
    트리거 없이 통과한다(2026-10-22 독립 리뷰 B1 실측)."""
    paras = (md or "").split("\n\n")
    return "\n\n".join(p for p in paras if not _is_head(p, PM_EXTRA_HEADS))


def strip_pm_actions(md: str) -> str:
    """등급에 딸린 할 일·조건 섹션만 걷어낸다(데이터 한계는 남긴다) —
    시스템이 등급을 HOLD 로 강제하면 원래 등급 기준의 '추가 매수' 같은 할 일이
    HOLD 배너 아래에 남아 모순된다(독립 리뷰 H1)."""
    paras = (md or "").split("\n\n")
    return "\n\n".join(p for p in paras if not _is_head(p, PM_ACTION_HEADS))


# ---------------------------------------------------------------------------
# ESON (Efficient Structured Object Notation) handoff serializers
# ---------------------------------------------------------------------------


def research_plan_to_eson(plan: ResearchPlan, ticker: str) -> str:
    """Convert ResearchPlan to ESON for Research Manager → Trader handoff.

    ESON is lossless and cuts ~50% tokens vs JSON for agent-to-agent pipes.
    Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).

    레포의 코덱(`bot.eson.encode_document`)으로 만든다 — 옛 판은 셀 규칙을 복제하고 머리줄에
    레코드 수(`[N]`)를 빠뜨려, 레포의 디코더(`decode_document`)가 레코드를 통째로 놓치고
    `=` 가 든 문장을 스칼라로 읽었다(독립 리뷰 #432 L3 — 왕복이 안 되면 무손실이 아니다).
    """
    from bot.eson import encode_document
    return encode_document({"ticker": ticker}, {"plan": [{
        "recommendation": plan.recommendation.value,
        "rationale": plan.rationale,
        "strategic_actions": plan.strategic_actions,
    }]}, number=False)


def trader_proposal_to_eson(proposal: TraderProposal, ticker: str) -> str:
    """Convert TraderProposal to ESON for Trader → Portfolio Manager handoff.

    Rule applies to all analyses going forward (US + KR + JP + TW + CN_A + HK).
    레포의 코덱으로 만든다(위 `research_plan_to_eson` 과 같은 이유).
    """
    from bot.eson import encode_document
    return encode_document({"ticker": ticker}, {"proposal": [{
        "action": proposal.action.value,
        "reasoning": proposal.reasoning,
        "entry_price": proposal.entry_price,
        "stop_loss": proposal.stop_loss,
        "position_sizing": proposal.position_sizing,
        "kill_trigger": proposal.kill_trigger,
    }]}, number=False)
