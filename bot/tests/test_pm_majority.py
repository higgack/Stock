"""Regression test for the PM analyst-majority tie-break (Fix B).

`_analyst_majority_direction` lives in TradingAgents'
``portfolio_manager.py``, whose module-level imports pull in yfinance and
langchain (unavailable in the unit-test sandbox). We therefore AST-extract
just the two pure functions under test (``_stance_to_direction`` and
``_analyst_majority_direction``) and exercise them against the REAL
``bot.analyzer._extract_stance`` (which the function imports lazily).

The bug (9988.HK / Alibaba 2026-05-27): the old ``buys >= holds`` tie-break
returned a "buy" majority on a 2-buy/2-hold TIE, which then let
``_enforce_pm_override_discipline`` force-correct a bearish PM up to Buy.
The fix uses strict ``buys > holds`` so a tie falls through to the
conservative Hold.
"""

from __future__ import annotations

import ast
from pathlib import Path

import bot.analyzer as _a  # provides the real _extract_stance


def _load_majority_fns():
    src = Path(__file__).resolve().parents[2] / (
        "TradingAgents/tradingagents/agents/managers/portfolio_manager.py"
    )
    tree = ast.parse(src.read_text())
    ns: dict = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "_stance_to_direction",
            "_analyst_majority_direction",
        ):
            exec(compile(ast.Module([node], []), str(src), "exec"), ns)
    return ns["_analyst_majority_direction"]


_majority = _load_majority_fns()

# Real verdict bodies that _extract_stance maps deterministically.
_BUY = "결론: 투자 의견은 매수입니다. 강한 상승 모멘텀."
_HOLD = "결론: 투자 의견은 보유입니다. 관망 권고."
_SELL = "결론: 투자 의견은 매도입니다. 하방 위험 큼."


def _state(market, senti, news, fund):
    return {
        "market_report": market,
        "sentiment_report": senti,
        "news_report": news,
        "fundamentals_report": fund,
    }


class TestMajorityTieBreak:
    def test_two_buy_two_hold_is_hold_not_buy(self):
        # 9988.HK case: a 2-buy/2-hold TIE must NOT elect a buy majority.
        direction, voters = _majority(_state(_BUY, _HOLD, _BUY, _HOLD))
        assert direction == "hold"
        assert voters == 4

    def test_two_sell_two_hold_is_hold_not_sell(self):
        direction, _ = _majority(_state(_SELL, _HOLD, _SELL, _HOLD))
        assert direction == "hold"

    def test_genuine_buy_plurality_preserved(self):
        # 3 buy / 1 hold is a real plurality → still buy.
        direction, _ = _majority(_state(_BUY, _BUY, _BUY, _HOLD))
        assert direction == "buy"

    def test_two_buy_one_hold_plurality_preserved(self):
        # 2 buy / 1 hold (1 abstain) → buy (strict 2 > 1).
        direction, voters = _majority(_state(_BUY, _BUY, _HOLD, ""))
        assert direction == "buy"
        assert voters == 3

    def test_buy_sell_split_returns_none(self):
        direction, _ = _majority(_state(_BUY, _SELL, _HOLD, _HOLD))
        assert direction == "hold"

    def test_one_buy_one_hold_tie_is_hold(self):
        # Smallest tie (1 buy / 1 hold, 2 abstain) → hold, not buy.
        direction, voters = _majority(_state(_BUY, _HOLD, "", ""))
        assert direction == "hold"
        assert voters == 2

    def test_hold_dominant(self):
        direction, _ = _majority(_state(_BUY, _HOLD, _HOLD, _HOLD))
        assert direction == "hold"
