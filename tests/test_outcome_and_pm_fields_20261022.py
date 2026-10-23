"""③ 추천 사후 채점 규약 + PM 출력 확장 (2026-10-22).

외부 레포(ZhuLinsen/daily_stock_analysis) 검토에서 가져온 두 아이디어:
  · 판정 방향별 채점 + ±2%p 중립 구간(보유도 채점) — `bot.outcome_verdict`
  · PM 이 보유자/미보유자 할 일·판정을 바꿀 조건·데이터 한계를 같이 낸다
"""

from __future__ import annotations

import pytest

from bot import outcome_verdict as ov


@pytest.mark.parametrize("rating,alpha,want", [
    ("Buy", 2.5, "hit"), ("Buy", -2.5, "miss"), ("Buy", 1.9, "neutral"),
    ("Buy", -2.0, "neutral"), ("overweight", 3.0, "hit"),
    ("Sell", -2.1, "hit"), ("Sell", 2.1, "miss"), ("underweight", 0.5, "neutral"),
    ("Hold", 1.5, "hit"), ("Hold", -2.0, "hit"), ("Hold", 2.01, "miss"),
    ("Hold", -5.0, "miss"),
    ("Buy", None, None), ("???", 5.0, None), ("Buy", float("nan"), None),
])
def test_verdict_rule(rating, alpha, want):
    assert ov.verdict(rating, alpha) == want


def test_verdict_text_names_the_band():
    assert ov.verdict_text("Buy", 0.5) == "매수 추천 판정 보류(섹터 대비 ±2%p 안)"
    assert ov.verdict_text("Sell", -3.0) == "매도 추천 맞음"
    assert ov.verdict_text("Hold", 4.0) == "보유 추천 틀림(섹터 대비 ±2%p 밖)"
    assert ov.verdict_text("Hold", None) == ""


def test_card_line_uses_the_shared_rule():
    from bot.dashboard import _render_outcome_html
    h = _render_outcome_html({"rating": "Buy", "ticker": "AAPL",
                              "raw": "+1.0%", "alpha": "+0.5%"})
    assert "판정 보류" in h and " hit" not in h and " miss" not in h
    h2 = _render_outcome_html({"rating": "Hold", "ticker": "AAPL",
                               "raw": "+1.0%", "alpha": "+0.5%"})
    assert "보유 추천 맞음" in h2 and 'class="outcome hit"' in h2
    h3 = _render_outcome_html({"rating": "Sell", "ticker": "AAPL",
                               "raw": "+5.0%", "alpha": "+4.0%"})
    assert "매도 추천 틀림" in h3 and 'class="outcome miss"' in h3


def test_stats_use_the_shared_rule(monkeypatch):
    import bot.dashboard as d
    resolved = [
        {"rating": "Buy", "raw": "+5%", "alpha": "+3%"},     # hit
        {"rating": "Buy", "raw": "+1%", "alpha": "+1%"},     # neutral
        {"rating": "Sell", "raw": "+5%", "alpha": "+3%"},    # miss
        {"rating": "Hold", "raw": "+1%", "alpha": "+1%"},    # hold hit
        {"rating": "Hold", "raw": "+9%", "alpha": "+8%"},    # hold miss
    ]
    monkeypatch.setattr(d, "_read_memory_full", lambda: {
        "resolved": resolved, "pending_directional": 0, "pending_hold": 0,
        "resolved_hold": 2})
    st = d._compute_stats([{"ticker": "AAPL", "trade_date": "2026-10-01",
                            "elapsed_sec": 100}])
    assert (st["evaluated"], st["correct"], st["neutral"]) == (2, 1, 1)
    assert (st["hold_evaluated"], st["hold_correct"]) == (2, 1)
    assert st["accuracy"] == 0.5
    html = d._render_stats_panel(st)
    assert "판정 보류 1건" in html and "Hold 맞음 1/2" in html and "±2%p" in html


def test_pm_decision_renders_new_fields_in_order():
    from tradingagents.agents.schemas import (PortfolioDecision, PortfolioRating,
                                              render_pm_decision)
    d = PortfolioDecision(
        investment_thesis="근거", rating=PortfolioRating.HOLD,
        executive_summary="요약", holder_action="보유 유지",
        new_entry_action="관망", watch_conditions=["현재가 9만원 하회 시", " ", "실적 D-3"],
        data_limitations=["수급 데이터 없음"])
    out = render_pm_decision(d)
    assert out.startswith("**Rating**: Hold")
    assert out.index("**Investment Thesis**") < out.index("**보유 중이라면**") \
        < out.index("**미보유라면**") < out.index("**판정을 바꿀 조건**") < out.index("**데이터 한계**")
    assert "- 현재가 9만원 하회 시" in out and "- 실적 D-3" in out and "-  " not in out


def test_pm_decision_without_new_fields_is_unchanged():
    from tradingagents.agents.schemas import (PortfolioDecision, PortfolioRating,
                                              render_pm_decision)
    d = PortfolioDecision(investment_thesis="근거", rating=PortfolioRating.BUY,
                          executive_summary="요약")
    assert render_pm_decision(d) == (
        "**Rating**: Buy\n\n**Executive Summary**: 요약\n\n**Investment Thesis**: 근거")


def test_new_fields_are_generated_after_rating():
    # 구조화 출력은 선언 순서로 생성된다 — 할 일·조건은 결론 뒤에(ALAB 2026-05-26 규약).
    from tradingagents.agents.schemas import PortfolioDecision
    names = list(PortfolioDecision.model_fields)
    assert names.index("rating") < names.index("holder_action") < names.index("watch_conditions")
    for k in ("holder_action", "new_entry_action", "watch_conditions", "data_limitations"):
        assert not PortfolioDecision.model_fields[k].is_required()
