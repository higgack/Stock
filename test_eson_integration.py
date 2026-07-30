"""Test ESON codec and agent handoff serializers."""

import sys
sys.path.insert(0, 'bot')
sys.path.insert(0, 'TradingAgents/tradingagents')

from bot.eson import encode_record_array, decode_document, _encode_cell
from TradingAgents.tradingagents.agents.schemas import (
    PortfolioRating, TraderAction, ResearchPlan, TraderProposal,
    research_plan_to_eson, trader_proposal_to_eson
)


def test_eson_cells():
    """Test ESON cell encoding."""
    assert _encode_cell(None) == 'null'
    assert _encode_cell(True) == 'true'
    assert _encode_cell(False) == 'false'
    assert _encode_cell(42) == '42'
    assert _encode_cell(3.14) == '3.14'
    assert _encode_cell('hello') == 'hello'
    assert _encode_cell('hello world') == '"hello world"'
    print("✓ ESON cell encoding OK")


def test_research_plan_eson():
    """Test ResearchPlan to ESON conversion."""
    plan = ResearchPlan(
        rationale="Tech sector growth strong despite macro headwinds.",
        recommendation=PortfolioRating.BUY,
        strategic_actions="Accumulate on dips below 150; target 180 by Q3 2026."
    )
    eson = research_plan_to_eson(plan, "AAPL")
    
    assert '!eson/1' in eson
    assert 'ticker=AAPL' in eson
    assert 'plan{recommendation,rationale,strategic_actions}' in eson
    assert 'Buy' in eson
    assert 'Tech sector growth' in eson
    print("✓ ResearchPlan ESON serialization OK")


def test_trader_proposal_eson():
    """Test TraderProposal to ESON conversion."""
    proposal = TraderProposal(
        reasoning="Plan is bullish; entry zone 145-150 on RSI pullback.",
        action=TraderAction.BUY,
        entry_price=148.50,
        stop_loss=140.00,
        position_sizing="2.5% of portfolio",
        kill_trigger="Q3 earnings miss vs guidance"
    )
    eson = trader_proposal_to_eson(proposal, "AAPL")
    
    assert '!eson/1' in eson
    assert 'ticker=AAPL' in eson
    assert 'proposal{action,reasoning,entry_price,stop_loss,position_sizing,kill_trigger}' in eson
    assert 'Buy' in eson
    assert 'Plan is bullish' in eson
    assert '148.5' in eson
    print("✓ TraderProposal ESON serialization OK")


def test_eson_record_array():
    """Test ESON record array encoding."""
    records = [
        {'ticker': 'AAPL', 'signal': 'BUY', 'confidence': 0.85},
        {'ticker': 'MSFT', 'signal': 'HOLD', 'confidence': 0.62},
    ]
    eson = encode_record_array('signals', records, number=True)
    
    assert 'signals[2]{n,ticker,signal,confidence}' in eson
    assert '1\tAPPL\tBUY\t0.85' in eson
    assert '2\tMSFT\tHOLD\t0.62' in eson
    print("✓ ESON record array encoding OK")


if __name__ == '__main__':
    test_eson_cells()
    test_research_plan_eson()
    test_trader_proposal_eson()
    test_eson_record_array()
    print("\n✅ All ESON tests passed!")
