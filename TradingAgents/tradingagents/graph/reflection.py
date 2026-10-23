# TradingAgents/graph/reflection.py

import math
import re
from typing import Any

_RATING_RE = re.compile(r"\*\*Rating\*\*:\s*([A-Za-z]+)")


def _verdict_line(final_decision: str, alpha_return: float) -> str:
    """대시보드 채점과 **같은 함수**(`bot.outcome_verdict`)로 낸 판정 한 줄.

    회고가 알파 부호만으로 '맞았다' 를 자유 판단하면 같은 추천을 대시보드는
    '판정 보류'(±2%p 안), 회고는 '맞음' 으로 적어 다음 분석에 주입된다(#38,
    2026-10-22 독립 리뷰 M3). 등급을 못 읽거나 모듈이 없으면 그 사실을 적는다."""
    m = _RATING_RE.search(final_decision or "")
    try:
        from bot.outcome_verdict import NEUTRAL_BAND_PP, verdict
    except Exception:                                   # noqa: BLE001
        return "Scoring verdict: unavailable (scoring module not importable)"
    if not m or not isinstance(alpha_return, (int, float)) or not math.isfinite(alpha_return):
        return "Scoring verdict: unavailable (rating or alpha missing)"
    v = verdict(m.group(1), alpha_return * 100)
    words = {"hit": "correct", "miss": "wrong",
             "neutral": f"inconclusive (alpha within ±{NEUTRAL_BAND_PP:g}%p)"}
    return f"Scoring verdict: {m.group(1)} call {words.get(v, 'unscored')}"


class Reflector:
    """Handles reflection on trading decisions."""

    def __init__(self, quick_thinking_llm: Any):
        """Initialize the reflector with an LLM."""
        self.quick_thinking_llm = quick_thinking_llm
        self.log_reflection_prompt = self._get_log_reflection_prompt()

    def _get_log_reflection_prompt(self) -> str:
        """Concise prompt for reflect_on_final_decision (Phase B log entries).

        Produces 2-4 sentences of plain prose — compact enough to be re-injected
        into future agent prompts without bloating the context window.
        """
        return (
            "You are a trading analyst reviewing your own past decision now that the outcome is known.\n"
            "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).\n\n"
            "Cover in order:\n"
            "1. Was the call correct? Quote the 'Scoring verdict' line verbatim — do not re-judge it\n"
            "2. Which part of the investment thesis held or failed?\n"
            "3. One concrete lesson to apply to the next similar analysis.\n\n"
            "Be specific and terse. Your output will be stored verbatim in a decision log "
            "and re-read by future analysts, so every word must earn its place."
        )

    def reflect_on_final_decision(
        self,
        final_decision: str,
        raw_return: float,
        alpha_return: float,
    ) -> str:
        """Single reflection call on the final trade decision with outcome context.

        Used by Phase B deferred reflection. The final_trade_decision already
        synthesises all analyst insights, so no separate market context is needed.
        """
        messages = [
            ("system", self.log_reflection_prompt),
            (
                "human",
                (
                    f"Raw return: {raw_return:+.1%}\n"
                    # 기준은 매칭된 섹터 ETF(없으면 SPY) — 옛 라벨 'vs SPY' 는 틀렸다.
                    f"Alpha vs benchmark (sector ETF, else SPY): {alpha_return:+.1%}\n"
                    f"{_verdict_line(final_decision, alpha_return)}\n"
                    f"Final Decision:\n{final_decision}"
                ),
            ),
        ]
        return self.quick_thinking_llm.invoke(messages).content
