"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

import logging
import re

from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    render_pm_decision,
)
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


_pm_log = logging.getLogger("tradingagents.portfolio_manager")


# Rating direction buckets used by the override-discipline post-check.
# Hold sits in its own bucket so a Hold majority can be preserved while
# either Buy- or Sell-side flips trigger the discipline rule.
_BUY_SIDE = {PortfolioRating.BUY, PortfolioRating.OVERWEIGHT}
_SELL_SIDE = {PortfolioRating.SELL, PortfolioRating.UNDERWEIGHT}
_HOLD_SIDE = {PortfolioRating.HOLD}


def _rating_direction(rating: PortfolioRating) -> str:
    """Map a 5-tier rating to a 3-direction bucket for discipline checks."""
    if rating in _BUY_SIDE:
        return "buy"
    if rating in _SELL_SIDE:
        return "sell"
    return "hold"


def _stance_to_direction(stance: str) -> str:
    """Map an analyst stance string ('매수' / '보유' / '매도') to a direction."""
    s = (stance or "").strip()
    if s in ("매수", "강매수", "Buy", "Overweight"):
        return "buy"
    if s in ("매도", "강매도", "Sell", "Underweight"):
        return "sell"
    return "hold"


def _analyst_majority_direction(state: dict) -> tuple[str | None, int]:
    """Return (majority_direction, voter_count) from the 4 analyst reports.

    Skipped analysts (empty / failed report bodies) don't vote — same rule
    bot/analyzer.py uses when rendering the stance bar to the user. If
    every analyst that ran leans the same way, that direction is the
    majority. If they split (e.g. 1 매수 + 1 매도 + 2 보유), majority is
    Hold (the safe default). Returns (None, 0) when no analyst stance
    can be extracted — caller skips the override check in that case
    (cannot enforce discipline without a baseline).
    """
    try:
        from bot.analyzer import _extract_stance
    except Exception:
        return None, 0

    report_keys = (
        "market_report", "sentiment_report",
        "news_report", "fundamentals_report",
    )
    directions: list[str] = []
    for key in report_keys:
        body = state.get(key)
        if not body or not isinstance(body, str):
            continue
        stance = _extract_stance(body)
        if not stance:
            continue
        directions.append(_stance_to_direction(stance))

    if not directions:
        return None, 0

    # Hold counts as a vote for the Hold direction. If ANY analyst leans
    # buy and the rest are Hold, the majority is still ambiguous between
    # Buy and Hold — we default to Hold (more conservative) unless the
    # buy side is the plurality. Same logic for sell.
    buys = directions.count("buy")
    sells = directions.count("sell")
    holds = directions.count("hold")
    if buys > sells and buys >= holds:
        return "buy", len(directions)
    if sells > buys and sells >= holds:
        return "sell", len(directions)
    # Holds dominate, or buys == sells (true split) — both go to Hold.
    return "hold", len(directions)


# Trigger keywords (Korean / English variants) that justify a PM override
# of the analyst-consensus direction per CLAUDE.md PM override discipline.
# Detection is text-based on the verdict's `investment_thesis` (the PM's
# rationale) — generous matching to avoid false rejections, since the
# downstream cost of a false reject (Hold override of a legitimate PM
# Sell call) is one wrong direction, while the cost of a false accept
# (letting through a no-trigger override) is the bug we're trying to
# fix in the first place. Be slightly inclusive.
_OVERRIDE_TRIGGERS = [
    # (a) 5-day technical extreme
    re.compile(r"RSI[^0-9가-힣\n]*?([0-9]{2,3})"),  # any RSI value mentioned
    # (b) imminent catalyst — earnings / FOMC / specific regulatory event
    re.compile(
        r"(어닝|실적 발표|earnings)\s*(?:발표)?\s*(?:일정|window)?"
        r"\s*(?:가|이|는)?\s*(?:5거래일|5\s*일|5\s*days|이번 주|D-?[0-5])"
    ),
    re.compile(r"FOMC|연준\s*금리\s*결정|기준금리\s*결정|BoJ\s*(?:회의|정책)"),
    re.compile(r"가이던스\s*(?:하향|컷|상향)|guide\s*(?:cut|raise)"),
    re.compile(r"승인\s*(?:연기|취소|보류)|규제\s*(?:반려|차단)"),
    # (c) mismatch warning explicit reference
    re.compile(r"stance.?vs.?decision\s*mismatch|stance↔결정\s*mismatch"),
    re.compile(r"분석가\s*stance\s*vs\s*결정"),
    # (d) data-availability HOLD
    re.compile(r"데이터\s*부족|데이터\s*가용성|data\s*availability"),
    # Corollary — corporate action HARD GUARD acknowledgment
    re.compile(r"corporate\s*action|분할|무상증자|주식분할|株式分割|HARD\s*GUARD"),
]


def _override_trigger_present(rationale: str) -> bool:
    """Scan PM rationale text for at least one named override trigger.
    Returns True if any pattern matches. Conservative — RSI must be
    accompanied by a numeric value (so a generic 'RSI도 약세' without a
    threshold value doesn't count). This is the exact gap that lets
    coal-mine '단기 위험 요소' prose flip a consistent analyst signal."""
    if not rationale:
        return False
    text = rationale
    for pat in _OVERRIDE_TRIGGERS:
        m = pat.search(text)
        if not m:
            continue
        # Special handling for RSI: require a value AND the value must be
        # in the extreme zone (>= 70 or <= 30) to satisfy "5-day technical
        # extreme". 'RSI 47.37 중립' should NOT count as a trigger.
        if pat.pattern.startswith("RSI"):
            try:
                val = float(m.group(1))
                if val >= 70 or val <= 30:
                    return True
            except (ValueError, IndexError):
                pass
            continue
        return True
    return False


def _enforce_pm_override_discipline(
    decision: PortfolioDecision, state: dict,
) -> tuple[PortfolioDecision, str | None]:
    """If PM verdict direction opposes the analyst majority AND no named
    override trigger appears in the rationale, downgrade the rating to
    align with the analyst majority. Returns (possibly-adjusted decision,
    adjustment note for memory / logging or None).

    Rule mirrors `## Portfolio Manager override discipline` in CLAUDE.md
    and the prompt text in `_PM_DISCIPLINE_PROMPT` above. Text-only
    instructions weren't sufficient — Toyota 7203.T 2026-05-18 had 4-of-4
    analysts on Hold and the PM still chose Underweight with no named
    trigger (RSI 47.37 quoted as 'neutral recovery', no imminent catalyst,
    no mismatch warning, no data-availability HOLD). This Python post-
    check enforces the rule structurally.

    Conservative on missing data: when analyst majority can't be
    determined (no readable stance from any report) we skip the check
    entirely. Better to let one false-negative pass than to incorrectly
    flip a legitimate PM call.

    Defensive design (post 두산 000150.KS 2026-05-18 silent skip):
    every step wrapped in try/except with INFO/WARNING logs so a
    cryptic Pydantic / state-shape / regex bug doesn't degrade silently
    into 'rule appears not to fire'. journalctl -u stock-bot | grep
    pm-discipline tells you exactly which path executed."""
    try:
        majority, voter_count = _analyst_majority_direction(state)
    except Exception as exc:
        _pm_log.warning(
            "pm-discipline: analyst-majority extraction failed (%s) — skipping check",
            exc,
        )
        return decision, None
    if not majority:
        _pm_log.info(
            "pm-discipline: analyst majority indeterminate (no readable stances) — skipping check"
        )
        return decision, None

    pm_dir = _rating_direction(decision.rating)
    if pm_dir == majority:
        _pm_log.info(
            "pm-discipline: PM=%s aligns with majority=%s (%d voters) — no adjustment",
            decision.rating.value, majority, voter_count,
        )
        return decision, None
    # Hold → Buy/Sell flip is also subject to the discipline (and the
    # other direction). The text covers any "opposite of analyst
    # majority" case.
    try:
        has_trigger = _override_trigger_present(decision.investment_thesis)
    except Exception as exc:
        _pm_log.warning(
            "pm-discipline: trigger-scan regex failed (%s) — treating as no-trigger to be safe",
            exc,
        )
        has_trigger = False
    if has_trigger:
        _pm_log.info(
            "pm-discipline: PM=%s vs majority=%s (%d voters), override trigger present — no adjustment",
            decision.rating.value, majority, voter_count,
        )
        return decision, None

    # No trigger named. Downgrade PM verdict to match analyst majority.
    target = {
        "buy": PortfolioRating.BUY,
        "sell": PortfolioRating.SELL,
        "hold": PortfolioRating.HOLD,
    }[majority]
    note = (
        f"[PM override discipline 자동 보정] 분석가 {voter_count}명의 다수 방향"
        f" ({majority.upper()})과 PM 1차 판단 ({decision.rating.value})이"
        f" 반대 방향인데, override 근거 (RSI>75/<25, 임박 catalyst, mismatch"
        f" warning, data-availability HOLD) 중 명시된 trigger가 없어"
        f" {target.value}로 보정됨. CLAUDE.md '## Portfolio Manager override"
        f" discipline' 규칙."
    )
    _pm_log.warning(
        "pm-discipline: PM=%s OVERRIDDEN to %s — majority=%s (%d voters), no trigger in rationale",
        decision.rating.value, target.value, majority, voter_count,
    )
    # Pydantic v1/v2 compatibility: v1 uses .copy(update=...), v2 prefers
    # .model_copy(update=...) (with .copy still working but deprecated).
    # 두산 000150.KS 2026-05-18 may have failed silently here if v2's
    # .copy() raised on the Enum field — try v2 first, fall back to v1.
    new_fields = {
        "rating": target,
        "investment_thesis": decision.investment_thesis + "\n\n" + note,
    }
    try:
        adjusted = decision.model_copy(update=new_fields)
    except (AttributeError, TypeError):
        try:
            adjusted = decision.copy(update=new_fields)
        except Exception as exc:
            _pm_log.warning(
                "pm-discipline: both model_copy and copy failed (%s) — falling back to in-place mutation",
                exc,
            )
            # Last resort: mutate fields in-place. PortfolioDecision is a
            # Pydantic model so setattr works; downstream render_pm_decision
            # reads .rating and .investment_thesis fresh either way.
            try:
                decision.rating = target
                decision.investment_thesis = (
                    decision.investment_thesis + "\n\n" + note
                )
                adjusted = decision
            except Exception as exc2:
                _pm_log.error(
                    "pm-discipline: in-place mutation failed too (%s) — returning unchanged decision (override NOT applied)",
                    exc2,
                )
                return decision, None
    return adjusted, note


def create_portfolio_manager(llm, llm_light=None):
    """Create the Portfolio Manager node.

    `llm`: full-budget decision LLM (Gemini 2.5 Pro w/ thinking_budget=4096
        by default). Used for conflict cases — analysts split or PM
        verdict opposes analyst majority — where genuine synthesis is
        required.
    `llm_light` (optional, Option 4 cost reduction 2026-05-18): lighter
        decision LLM (thinking_budget=2048 by default). Used when all
        running analysts lean the same direction, where the PM's job
        reduces to summarising agreement rather than resolving conflict.
        Falls back to `llm` when unset — identical to pre-Option-4
        behaviour."""
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")
    if llm_light is not None and llm_light is not llm:
        structured_llm_light = bind_structured(
            llm_light, PortfolioDecision, "Portfolio Manager (consensus)",
        )
    else:
        structured_llm_light = structured_llm

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])

        # Option 4 routing: when the four analysts are unanimous in
        # direction (매수/보유/매도), use the light LLM (thinking_budget=
        # 2048) — synthesis is mostly summarisation. Conflict / split
        # cases keep the heavy LLM (4096) to preserve override-discipline
        # reasoning quality. Hold-only consensus also routes light: the
        # PM still has to check the DATA-AVAILABILITY GUARD and the
        # consistency-of-evidence requirement, but neither demands the
        # extra thinking budget reserved for split-direction debates.
        try:
            majority, voter_count = _analyst_majority_direction(state)
            use_light = (
                majority is not None
                and voter_count >= 2
                and structured_llm_light is not structured_llm
            )
        except Exception as exc:
            _pm_log.warning(
                "pm-budget: majority extraction failed (%s) — defaulting to heavy LLM",
                exc,
            )
            use_light = False
        active_structured_llm = structured_llm_light if use_light else structured_llm
        if use_light:
            _pm_log.info(
                "pm-budget: %d analysts unanimous on %s — using light LLM (thinking_budget=2048)",
                voter_count, majority,
            )

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
analyst stance bar shows consistent agreement on a direction —
defined as ALL the analysts that actually ran (i.e. weren't pre-
skipped for missing data) leaning the same way — you MAY override
to the opposite direction (e.g. Underweight / Sell when analysts
lean Hold / Buy) ONLY if you explicitly name at least ONE of these
triggers in your rationale:
(a) 5-day-horizon technical extreme — RSI > 75 for Buy-reverse,
    RSI < 25 for Sell-reverse
(b) imminent specific catalyst — earnings within ±5 days, FOMC,
    guide cut, regulatory event in the next 5 days
(c) stance-vs-decision mismatch detector explicit warning text
    was visible in your prompt
(d) data-availability HOLD per the GUARD above
Without ONE of these triggers named, default to the analyst
direction: Buy / Overweight when analysts lean buy, Hold when
analysts lean hold (even partially: 2-of-2 Hold with 2 abstain
counts as consistent Hold), Sell / Underweight when analysts
lean sell.

This rule covers ALL of:
 • 4-of-4 unanimous
 • 3-of-4 with 1 abstain
 • 2-of-2 with 2 abstain (news / sentiment skipped — common
   for KR/JP low-coverage tickers; the smaller voter count does
   NOT lower the trigger bar)
 • 3-of-3 with 1 abstain

The pattern 'analysts 보유 + RSI alone → PM Sell' (현대모비스 /
호텔신라 / 한전 2026-05-17 cluster, 코미코 2026-05-17 with only
시장+펀더멘털 voters both Hold yet PM flipped to Sell on RSI
55.36 / general 모멘텀 약화) is exactly what this rule prevents.
A consistent analyst signal should not flip on a single technical
indicator or generic '단기 위험 요소' phrasing without explicit
justification.

Corollary — when the build_instrument_context block contains a
CORPORATE ACTION IN-FLIGHT (HARD GUARD), the standard technical
triggers (RSI / MACD / SMA) are invalid for this analysis and
CANNOT be used as override triggers; only (b) imminent catalyst
or (d) data-availability HOLD apply.

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}

---

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        # Structured path first so we can inspect the typed decision
        # (rating + investment_thesis) for the override-discipline post
        # check. Free-text fallback bypasses the structural check because
        # we'd be regex-parsing markdown — keep the prompt's text rule
        # as the only guard in that path.
        final_trade_decision: str
        if active_structured_llm is not None:
            try:
                decision: PortfolioDecision = active_structured_llm.invoke(prompt)
                decision, _override_note = _enforce_pm_override_discipline(
                    decision, state,
                )
                final_trade_decision = render_pm_decision(decision)
            except Exception as exc:
                _pm_log.warning(
                    "Portfolio Manager: structured invocation failed (%s);"
                    " falling back to free-text without discipline check",
                    exc,
                )
                final_trade_decision = llm.invoke(prompt).content
        else:
            final_trade_decision = invoke_structured_or_freetext(
                active_structured_llm,
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
