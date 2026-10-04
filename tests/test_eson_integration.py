"""ESON 코덱과 에이전트 핸드오프 직렬화 — `make test` 안으로(#24·#54).

이 파일은 레포 **최상위**에 있어 어느 게이트 프로세스에도 안 실렸고(게이트 완전성
가드가 최상위 파일을 조용히 면제했다), 그동안 4개가 전부 빨간불이었다(2026-10-04
실측). 둘은 테스트 쪽 오류(`APPL` 오타 · 코드가 명시한 규칙과 다른 따옴표 기대),
둘은 제품 결함이었다 — 직렬화기의 머리줄이 일반 문자열 안의 `{{…}}` 라 중괄호가
**두 겹**으로 나갔다(실수 #8 과 같은 모양). 같은 자리에서 디코더가 JSON 따옴표 셀을
못 풀던 것(`decode_cell` 이 셀 전체를 한 글자와 비교)도 드러났다 — 무손실이 계약인데
따옴표 붙은 값이 따옴표째 돌아왔다.

⚠️ 운영 영향은 없다 — 2026-10-04 현재 이 코덱·직렬화기를 부르는 곳이 레포에 없다.
"""

from bot.eson import (_encode_cell, decode_cell, decode_document, encode_document,
                      encode_record_array)
from TradingAgents.tradingagents.agents.schemas import (
    PortfolioRating, ResearchPlan, TraderAction, TraderProposal,
    research_plan_to_eson, trader_proposal_to_eson)


def test_eson_cells_follow_the_bare_string_rule():
    """맨 문자열 규칙은 `_is_bare_string` 이 정한다 — 탭 구분 셀을 깨는 것(앞뒤 공백 ·
    탭·개행 · 예약어 · 숫자로 읽히는 값)만 따옴표를 붙이고 **사이 공백은 그대로**다.
    옛 테스트는 사이 공백도 따옴표를 기대했다(코드의 명시 규칙과 다른 가정)."""
    assert _encode_cell(None) == 'null'
    assert _encode_cell(True) == 'true'
    assert _encode_cell(False) == 'false'
    assert _encode_cell(42) == '42'
    assert _encode_cell(3.14) == '3.14'
    assert _encode_cell('hello') == 'hello'
    assert _encode_cell('hello world') == 'hello world'
    assert _encode_cell(' hello') == '" hello"'
    assert _encode_cell('a\tb') == '"a\\tb"'
    assert _encode_cell('true') == '"true"'
    assert _encode_cell('3.14') == '"3.14"'


def test_eson_cells_round_trip_losslessly():
    """무손실이 계약이다 — 따옴표가 붙은 값도 디코드하면 **원래 값**이어야 한다.
    옛 `decode_cell` 은 셀 전체를 `'"'` 한 글자와 비교해 따옴표째 돌려줬다."""
    for v in (None, True, False, 42, 3.14, 'hello', 'hello world', ' hello',
              'a\tb', 'true', '3.14', '', '[1]', {'k': 1}):
        assert decode_cell(_encode_cell(v)) == v, (v, _encode_cell(v))


def test_research_plan_eson():
    plan = ResearchPlan(
        rationale="Tech sector growth strong despite macro headwinds.",
        recommendation=PortfolioRating.BUY,
        strategic_actions="Accumulate on dips below 150; target 180 by Q3 2026.")
    lines = research_plan_to_eson(plan, "AAPL").splitlines()
    assert lines[:3] == ['!eson/1', 'ticker=AAPL',
                         'plan{recommendation,rationale,strategic_actions}'], lines
    assert lines[3].split('\t') == [
        'Buy', 'Tech sector growth strong despite macro headwinds.',
        'Accumulate on dips below 150; target 180 by Q3 2026.']


def test_trader_proposal_eson():
    proposal = TraderProposal(
        reasoning="Plan is bullish; entry zone 145-150 on RSI pullback.",
        action=TraderAction.BUY, entry_price=148.50, stop_loss=140.00,
        position_sizing="2.5% of portfolio", kill_trigger="Q3 earnings miss vs guidance")
    lines = trader_proposal_to_eson(proposal, "AAPL").splitlines()
    assert lines[:3] == ['!eson/1', 'ticker=AAPL',
                         'proposal{action,reasoning,entry_price,stop_loss,'
                         'position_sizing,kill_trigger}'], lines
    assert lines[3].split('\t') == [
        'Buy', 'Plan is bullish; entry zone 145-150 on RSI pullback.', '148.5', '140.0',
        '2.5% of portfolio', 'Q3 earnings miss vs guidance']


def test_eson_record_array_and_document_round_trip():
    records = [
        {'ticker': 'AAPL', 'signal': 'BUY', 'confidence': 0.85},
        {'ticker': 'MSFT', 'signal': 'HOLD', 'confidence': 0.62},
    ]
    eson = encode_record_array('signals', records, number=True)
    assert eson.splitlines() == ['signals[2]{n,ticker,signal,confidence}',
                                 '1\tAAPL\tBUY\t0.85', '2\tMSFT\tHOLD\t0.62'], eson
    doc = decode_document(encode_document({'kind': 'signals'}, {'signals': records}))
    assert doc['scalars'] == {'kind': 'signals'}
    assert doc['arrays']['signals'] == [dict(r, n=i) for i, r in enumerate(records, 1)]
