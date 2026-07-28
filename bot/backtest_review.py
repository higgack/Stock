"""백테스트/트레이드 이력 품질 평가 — Deploy/Refine/Abandon (2026-07-26).

claude-trading-skills 저장소(tradermonty/claude-trading-skills) backtest-expert
스킬의 5축(20점×5=100점) 스코어링 프레임워크
(skills/backtest-expert/scripts/evaluate_backtest.py)를 이식. 순수 스코어링
함수는 그 스크립트의 정확한 공식·임계값을 그대로 보존(재현성 위해 임의 변형 금지).

우리 시스템엔 "커브핏 파라미터 백테스트"가 아니라 실시간 페이퍼트레이딩 씨시스
이력(bot/trade_thesis.py CLOSED)이 있으므로 5축 중 2개 입력을 다음처럼 적응:
- num_parameters: 튜닝 파라미터가 있는 전략이 아니라 실거래 이력이므로 기본
  0(만점 5) — 필요 시 실제 전략 백테스트 평가에도 그대로 재사용 가능(인자 override).
- slippage_tested: 추정치가 아니라 실제 시장가 체결 기록이므로 기본 True(만점 20).
나머지 3축(Sample Size/Expectancy/Risk Management/Robustness)은 CLOSED 씨시스의
realized_pct 시계열에서 직접 계산 — 어느 시장(US/KR/JP 등)이든 동일 로직(universal).
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# 스코어링 함수(각 0-20) — evaluate_backtest.py 공식 그대로 이식
# ---------------------------------------------------------------------------


def score_sample_size(total_trades: int) -> int:
    """거래수 기반 점수. <30→0, 30→8, 100→15, 200+→20 (구간 선형보간)."""
    if total_trades < 30:
        return 0
    if total_trades < 100:
        return 8 + int((total_trades - 30) / 70 * 7)
    if total_trades < 200:
        return 15 + int((total_trades - 100) / 100 * 5)
    return 20


def calc_profit_factor(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """손익비 = (승률×평균승) / (패률×평균패). 손실쪽 0이면 inf."""
    wr = win_rate / 100.0
    loss_component = (1 - wr) * avg_loss_pct
    if loss_component == 0:
        return float("inf")
    return (wr * avg_win_pct) / loss_component


def calc_expectancy(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """건당 기대값(%) = 승률×평균승 - 패률×평균패."""
    wr = win_rate / 100.0
    return wr * avg_win_pct - (1 - wr) * avg_loss_pct


def score_expectancy(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> int:
    """기대값 기반 점수. <=0→0, 0..0.5→5..10, 0.5..1.5→10..18, >=1.5→20."""
    exp = calc_expectancy(win_rate, avg_win_pct, avg_loss_pct)
    if exp <= 0:
        return 0
    if exp < 0.5:
        return 5 + int(exp / 0.5 * 5)
    if exp < 1.5:
        return 10 + int((exp - 0.5) / 1.0 * 8)
    return 20


def score_risk_management(max_drawdown_pct: float, win_rate: float,
                           avg_win_pct: float, avg_loss_pct: float) -> int:
    """MDD(0-12) + 손익비(0-8). MDD 50%+ 는 전체 0점(치명적)."""
    if max_drawdown_pct >= 50:
        return 0
    if max_drawdown_pct < 20:
        dd_score = 12
    else:
        dd_score = int(12 * (50 - max_drawdown_pct) / 30)

    pf = calc_profit_factor(win_rate, avg_win_pct, avg_loss_pct)
    if pf < 1.0:
        pf_score = 0
    elif pf >= 3.0:
        pf_score = 8
    else:
        pf_score = int((pf - 1.0) / 2.0 * 8)

    return min(20, dd_score + pf_score)


def score_robustness(years_tested: int, num_parameters: int) -> int:
    """검증기간(0-15) + 파라미터수(0-5). <5년→0, 10년+→15 / ≤4개 파라미터 만점."""
    if years_tested < 5:
        years_score = 0
    elif years_tested >= 10:
        years_score = 15
    else:
        years_score = 5 + int((years_tested - 5) / 5 * 10)

    if num_parameters <= 4:
        param_score = 5
    elif num_parameters <= 6:
        param_score = 3
    elif num_parameters == 7:
        param_score = 1
    else:
        param_score = 0

    return min(20, years_score + param_score)


def score_execution_realism(slippage_tested: bool) -> int:
    """슬리피지/체결마찰 검증 여부. 검증됨→20, 아니면→0."""
    return 20 if slippage_tested else 0


def get_verdict(total_score: int) -> str:
    """총점 → Deploy(70+) / Refine(40-69) / Abandon(<40)."""
    if total_score >= 70:
        return "Deploy"
    if total_score >= 40:
        return "Refine"
    return "Abandon"


def detect_red_flags(total_trades: int, win_rate: float, avg_win_pct: float,
                      avg_loss_pct: float, max_drawdown_pct: float,
                      years_tested: int, num_parameters: int,
                      slippage_tested: bool) -> list:
    """methodology 레드플래그 체크리스트 기반 경고 목록."""
    flags = []
    if total_trades < 30:
        flags.append({"id": "small_sample", "severity": "high",
                       "message": f"거래 {total_trades}건뿐 — 통계적 신뢰엔 최소 30건 필요."})
    if not slippage_tested:
        flags.append({"id": "no_slippage_test", "severity": "high",
                       "message": "슬리피지/체결마찰 미검증 — 실전 체결 시 결과가 달라질 수 있음."})
    if max_drawdown_pct > 50:
        flags.append({"id": "excessive_drawdown", "severity": "high",
                       "message": f"최대낙폭 {max_drawdown_pct:.1f}% — 50% 초과, 치명적 리스크."})
    if num_parameters >= 7:
        flags.append({"id": "over_optimized", "severity": "medium",
                       "message": f"파라미터 {num_parameters}개 — 과최적화(커브핏) 의심."})
    if years_tested < 5:
        flags.append({"id": "short_test_period", "severity": "medium",
                       "message": f"검증기간 {years_tested}년 — 국면변화 놓칠 수 있음(권장 5년+)."})
    exp = calc_expectancy(win_rate, avg_win_pct, avg_loss_pct)
    if exp < 0:
        flags.append({"id": "negative_expectancy", "severity": "high",
                       "message": f"기대값 음수({exp:.3f}%) — 평균적으로 손실."})
    if win_rate > 90 and max_drawdown_pct < 5:
        flags.append({"id": "too_good", "severity": "medium",
                       "message": "결과가 지나치게 좋음 — look-ahead bias/데이터 이슈 점검 필요."})
    return flags


def validate_inputs(total_trades: int, win_rate: float, avg_win_pct: float,
                     avg_loss_pct: float, max_drawdown_pct: float,
                     years_tested: int, num_parameters: int) -> None:
    """시스템 경계 입력 검증. 위반 시 ValueError."""
    if total_trades < 0:
        raise ValueError("total_trades must be >= 0")
    if not (0 <= win_rate <= 100):
        raise ValueError("win_rate must be between 0 and 100")
    if avg_win_pct < 0:
        raise ValueError("avg_win_pct must be >= 0")
    if avg_loss_pct < 0:
        raise ValueError("avg_loss_pct must be >= 0")
    if max_drawdown_pct < 0:
        raise ValueError("max_drawdown_pct must be >= 0")
    if years_tested < 0:
        raise ValueError("years_tested must be >= 0")
    if num_parameters < 0:
        raise ValueError("num_parameters must be >= 0")


def evaluate(total_trades: int, win_rate: float, avg_win_pct: float,
             avg_loss_pct: float, max_drawdown_pct: float, years_tested: int,
             num_parameters: int, slippage_tested: bool) -> dict:
    """5축 평가 실행 + 구조화 결과 반환."""
    validate_inputs(total_trades, win_rate, avg_win_pct, avg_loss_pct,
                     max_drawdown_pct, years_tested, num_parameters)
    d1 = score_sample_size(total_trades)
    d2 = score_expectancy(win_rate, avg_win_pct, avg_loss_pct)
    d3 = score_risk_management(max_drawdown_pct, win_rate, avg_win_pct, avg_loss_pct)
    d4 = score_robustness(years_tested, num_parameters)
    d5 = score_execution_realism(slippage_tested)
    total = max(0, min(100, d1 + d2 + d3 + d4 + d5))
    return {
        "total_score": total,
        "verdict": get_verdict(total),
        "dimensions": [
            {"name": "Sample Size", "score": d1, "max_score": 20},
            {"name": "Expectancy", "score": d2, "max_score": 20},
            {"name": "Risk Management", "score": d3, "max_score": 20},
            {"name": "Robustness", "score": d4, "max_score": 20},
            {"name": "Execution Realism", "score": d5, "max_score": 20},
        ],
        "red_flags": detect_red_flags(total_trades, win_rate, avg_win_pct, avg_loss_pct,
                                       max_drawdown_pct, years_tested, num_parameters,
                                       slippage_tested),
        "profit_factor": calc_profit_factor(win_rate, avg_win_pct, avg_loss_pct),
        "expectancy": calc_expectancy(win_rate, avg_win_pct, avg_loss_pct),
        "inputs": {
            "total_trades": total_trades, "win_rate": win_rate,
            "avg_win_pct": avg_win_pct, "avg_loss_pct": avg_loss_pct,
            "max_drawdown_pct": max_drawdown_pct, "years_tested": years_tested,
            "num_parameters": num_parameters, "slippage_tested": slippage_tested,
        },
    }


# ---------------------------------------------------------------------------
# 씨시스 이력(bot/trade_thesis.py) → 평가 입력 변환
# ---------------------------------------------------------------------------


def max_drawdown_pct_from_returns(pcts: list) -> float:
    """건별 realized_pct 시계열(시간순, %p 단위 단순누적)의 최대낙폭(%p).
    digest_stats() 와 동일하게 복리가 아닌 단순합산 방식(일관성)."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pcts:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def review_trade_history(theses: Optional[list] = None, *,
                          num_parameters: int = 0,
                          slippage_tested: bool = True) -> dict:
    """CLOSED 씨시스 이력을 5축 프레임워크로 평가. theses 미지정 시
    trade_thesis.list_theses(status="CLOSED") 전체 조회(테스트 시 주입 가능).
    청산 0건이면 {"n": 0, ...} 로 조기 반환(평가 무의미 — Abandon 오탐 방지)."""
    if theses is None:
        from bot import trade_thesis
        theses = trade_thesis.list_theses(status="CLOSED")

    closed = [t for t in theses if t.get("realized_pct") is not None
              and t.get("exit_ts") is not None]
    closed.sort(key=lambda t: t["exit_ts"])
    pcts = [t["realized_pct"] for t in closed]
    n = len(pcts)
    if n == 0:
        return {"n": 0, "message": "청산 이력 없음 — 평가 불가"}

    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p < 0]
    win_rate = len(wins) / n * 100
    avg_win_pct = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss_pct = (-sum(losses) / len(losses)) if losses else 0.0
    max_drawdown_pct = max_drawdown_pct_from_returns(pcts)

    entry_tss = [t["entry_ts"] for t in closed if t.get("entry_ts") is not None]
    span_days = ((closed[-1]["exit_ts"] - min(entry_tss)) / 86400) if entry_tss else 0.0
    years_tested = int(span_days / 365.25)

    result = evaluate(
        total_trades=n, win_rate=win_rate, avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct, max_drawdown_pct=max_drawdown_pct,
        years_tested=years_tested, num_parameters=num_parameters,
        slippage_tested=slippage_tested,
    )
    result["n"] = n
    result["span_days"] = round(span_days, 1)
    return result


def review_text(result: dict) -> str:
    """텔레그램용 한 줄+표 요약 텍스트."""
    if result.get("n") == 0:
        return f"📊 백테스트 리뷰: {result['message']}"
    lines = [
        f"📊 트레이드 이력 백테스트 리뷰 ({result['n']}건, {result['span_days']:.0f}일간)",
        f"총점 {result['total_score']}/100 — 판정: {result['verdict']}",
    ]
    for dim in result["dimensions"]:
        lines.append(f"  {dim['name']}: {dim['score']}/{dim['max_score']}")
    pf = result["profit_factor"]
    pf_text = "Inf(무손실)" if pf == float("inf") else f"{pf:.2f}"
    lines.append(f"손익비 {pf_text} · 기대값 {result['expectancy']:+.3f}%/건")
    if result["red_flags"]:
        for flag in result["red_flags"]:
            icon = "🔴" if flag["severity"] == "high" else "🟡"
            lines.append(f"{icon} {flag['message']}")
    else:
        lines.append("레드플래그 없음")
    return "\n".join(lines)
