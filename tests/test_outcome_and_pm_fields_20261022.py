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
    ("Buy", float("inf"), None), ("Buy", 2.0, "neutral"),
    ("Sell", 2.0, "neutral"), ("Sell", -2.0, "neutral"), ("Hold", 2.0, "hit"),
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
        {"rating": "Buy", "raw": "+6%", "alpha": "+4%"},     # hit (비대칭 — 뒤집힘을 잡는다)
        {"rating": "Hold", "raw": "+0%", "alpha": "-1%"},    # hold hit
    ]
    monkeypatch.setattr(d, "_read_memory_full", lambda: {
        "resolved": resolved, "pending_directional": 0, "pending_hold": 0,
        "resolved_hold": 2})
    st = d._compute_stats([{"ticker": "AAPL", "trade_date": "2026-10-01",
                            "elapsed_sec": 100}])
    assert (st["evaluated"], st["correct"], st["neutral"]) == (3, 2, 1)
    assert (st["hold_evaluated"], st["hold_correct"]) == (3, 2)
    assert abs(st["accuracy"] - 2 / 3) < 1e-9
    html = d._render_stats_panel(st)
    assert "판정 보류 1건" in html and "Hold 맞음 2/3" in html and "±2%p" in html
    st["pending_hold"] = 4
    assert "Hold 미해소 4건" in d._render_stats_panel(st)


def test_pm_decision_renders_new_fields_last_in_order():
    """새 섹션은 Price Target·Time Horizon **뒤** — 차트 목표가 파서는 첫 매치를
    쓰고 요약은 앞 4줄만 싣는다(2026-10-22 독립 리뷰 M2)."""
    from tradingagents.agents.schemas import (PortfolioDecision, PortfolioRating,
                                              render_pm_decision)
    d = PortfolioDecision(
        investment_thesis="근거", rating=PortfolioRating.HOLD,
        executive_summary="요약", holder_action="보유 유지",
        new_entry_action="관망",
        watch_conditions=["현재가 9만원 하회 시", " ", "실적 D-3", "c4", "c5"],
        data_limitations=["수급 데이터 없음"], price_target=110000.0,
        time_horizon="5거래일")
    out = render_pm_decision(d)
    assert out.startswith("**Rating**: Hold")
    assert out.index("**Investment Thesis**") < out.index("**Price Target**") \
        < out.index("**Time Horizon**") < out.index("**보유 중이라면**") \
        < out.index("**미보유라면**") < out.index("**판정을 바꿀 조건**") < out.index("**데이터 한계**")
    assert "- 현재가 9만원 하회 시" in out and "- 실적 D-3" in out and "-  " not in out
    # 조건은 3개까지(빈 항목은 세지 않는다)
    assert "- c4" in out and "- c5" not in out


def test_chart_target_is_not_hijacked_by_new_sections():
    from bot.chart_data import parse_trade_levels
    from tradingagents.agents.schemas import (PortfolioDecision, PortfolioRating,
                                              render_pm_decision)
    d = PortfolioDecision(
        investment_thesis="근거", rating=PortfolioRating.BUY, executive_summary="요약",
        holder_action="일부 익절(목표가: 120,000원 근접 시)", price_target=110000.0)
    lv = parse_trade_levels("## ✅ 최종 결정\n" + render_pm_decision(d),
                            close_values=[100000.0] * 30)
    assert lv.get("target") == 110000.0


def test_list_fields_tolerate_a_string_or_junk():
    from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating
    d = PortfolioDecision(investment_thesis="t", rating=PortfolioRating.BUY,
                          executive_summary="e", watch_conditions="실적 D-3",
                          data_limitations=5, holder_action=7)
    assert d.watch_conditions == ["실적 D-3"]
    assert d.data_limitations is None and d.holder_action is None


def _decision_text(**kw):
    from tradingagents.agents.schemas import (PortfolioDecision, PortfolioRating,
                                              render_pm_decision)
    return render_pm_decision(PortfolioDecision(
        investment_thesis="모멘텀이 개선되고 있어 비중 확대가 합리적이다.",
        rating=PortfolioRating.BUY, executive_summary="매수", **kw))


@pytest.mark.parametrize("extra", [
    {},
    {"data_limitations": ["수급 데이터 없음"]},
    {"watch_conditions": ["2026-10-28 실적 발표 결과"]},
    {"holder_action": "거래정지 해제 확인 후 보유"},
])
def test_new_sections_do_not_unlock_the_override_backstop(monkeypatch, extra):
    """Fix F 백스톱은 근거에서 트리거를 찾는다 — 보조 섹션의 '실적 발표'·
    '데이터 없음'·'정지' 가 트리거로 읽히면 규율이 꺼진다(리뷰 B1 실측)."""
    import bot.analyzer as an
    monkeypatch.setattr(an, "_get_unanimous_analyst_direction", lambda st: "hold")
    rating, note = an._check_pm_override_required({}, _decision_text(**extra))
    assert rating == "Hold" and note


def test_strip_pm_actions_keeps_data_limits_only():
    from tradingagents.agents.schemas import pm_core_text, strip_pm_actions
    md = _decision_text(holder_action="추가 매수", new_entry_action="분할 진입",
                        watch_conditions=["판정 하향: Sell"],
                        data_limitations=["수급 데이터 없음"])
    st = strip_pm_actions(md)
    assert "추가 매수" not in st and "분할 진입" not in st and "판정 하향" not in st
    assert "**데이터 한계**" in st and "**Investment Thesis**" in st
    core = pm_core_text(md)
    assert "데이터 한계" not in core and "**Rating**: Buy" in core


def test_forced_hold_in_graph_clears_actions(monkeypatch):
    """그래프 안 강제 보정도 원래 등급 기준 할 일을 지운다(리뷰 H1)."""
    from tradingagents.agents.managers import portfolio_manager as pm
    from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating
    monkeypatch.setattr(pm, "_analyst_majority_direction", lambda st: ("hold", 4))
    monkeypatch.setattr(pm, "_override_trigger_directional", lambda t, d=None: False)
    d = PortfolioDecision(investment_thesis="t", rating=PortfolioRating.BUY,
                          executive_summary="e", holder_action="추가 매수",
                          new_entry_action="진입", watch_conditions=["x"],
                          data_limitations=["y"])
    out, note = pm._enforce_pm_override_discipline(d, {})
    assert note and out.rating != PortfolioRating.BUY
    assert out.holder_action is None and out.new_entry_action is None
    assert out.watch_conditions is None and out.data_limitations == ["y"]


def test_reflection_quotes_the_shared_verdict():
    from tradingagents.graph.reflection import _verdict_line
    md = _decision_text()
    assert "inconclusive" in _verdict_line(md, 0.005)
    assert "correct" in _verdict_line(md, 0.03)
    assert "wrong" in _verdict_line(md, -0.03)
    assert "unavailable" in _verdict_line("no rating", 0.03)
    assert "unavailable" in _verdict_line(md, float("inf"))


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


def test_analyze_strips_actions_when_forcing_hold():
    """analyze() 는 무거워 통째로 못 태운다 — 배선만 AST 로 잰다(#19: 값은 위
    `strip_pm_actions` 동작 테스트가 본다). 강제 HOLD 분기 안에서 decision 을
    갈아끼워야 요약·전체 리포트 둘 다 할 일을 잃는다(리뷰 H1)."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("bot/analyzer.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "analyze")
    hits = []
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) \
                and node.test.id == "_override_note":
            for st in node.body:
                if (isinstance(st, ast.Assign) and isinstance(st.value, ast.Call)
                        and getattr(st.value.func, "id", "") == "strip_pm_actions"
                        and [t.id for t in st.targets if isinstance(t, ast.Name)] == ["decision"]):
                    hits.append(st)
    assert hits, "강제 HOLD 분기에서 decision = strip_pm_actions(decision) 배선이 빠졌다"
