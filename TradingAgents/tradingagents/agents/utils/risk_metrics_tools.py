"""Deterministic risk metrics computed from price history.

Unlike the indicator tools that wrap stockstats, these are calculated
directly from the OHLCV cache so they always agree with what a human
would compute from the same data — useful for the market analyst's
'objective risk' bullet that supplements the qualitative read.

Coverage:
  * Annualized volatility (σ) of daily log returns
  * Sharpe and Sortino ratios (rf=0 for simplicity; the absolute number
    is less important than the cross-ticker comparison)
  * Historical 95% Value-at-Risk and Conditional VaR (CVaR / expected
    shortfall) on daily returns
  * Maximum drawdown over the lookback window
  * Beta vs. SPY computed from overlapping daily returns
"""

from __future__ import annotations

from typing import Annotated

import numpy as np

from langchain_core.tools import tool
from tradingagents.dataflows.stockstats_utils import load_ohlcv


_TRADING_DAYS = 252


def _format_pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _compute_metrics(symbol: str, curr_date: str, look_back_days: int) -> dict:
    """Compute the risk metric bundle. Returns a dict with strings ready
    for prose embedding so the LLM doesn't have to reformat numbers."""
    df = load_ohlcv(symbol, curr_date)
    if df.empty:
        raise ValueError(f"No price data for {symbol} up to {curr_date}")

    # Use the last `look_back_days` calendar days worth of trading rows.
    df = df.sort_values("Date").tail(look_back_days).reset_index(drop=True)
    if len(df) < 20:
        raise ValueError(
            f"Only {len(df)} trading rows for {symbol} — need ≥20 for reliable metrics"
        )

    # Daily simple returns are the canonical input for VaR / drawdown;
    # log returns are used for vol/Sharpe so they aggregate cleanly.
    closes = df["Close"].astype(float).to_numpy()
    simple_ret = np.diff(closes) / closes[:-1]
    log_ret = np.diff(np.log(closes))

    sigma_daily = log_ret.std(ddof=1)
    sigma_annual = sigma_daily * np.sqrt(_TRADING_DAYS)
    mean_daily = log_ret.mean()
    sharpe = (
        mean_daily / sigma_daily * np.sqrt(_TRADING_DAYS) if sigma_daily > 0 else 0.0
    )
    downside = log_ret[log_ret < 0]
    downside_sigma = downside.std(ddof=1) if len(downside) > 1 else float("nan")
    sortino = (
        mean_daily / downside_sigma * np.sqrt(_TRADING_DAYS)
        if downside_sigma > 0
        else float("nan")
    )

    # Historical 95% VaR / CVaR — empirical 5th percentile of simple returns.
    var_95 = np.percentile(simple_ret, 5)
    cvar_95 = simple_ret[simple_ret <= var_95].mean() if (simple_ret <= var_95).any() else var_95

    # Max drawdown over the window.
    running_peak = np.maximum.accumulate(closes)
    drawdown = (closes - running_peak) / running_peak
    max_dd = drawdown.min()

    # Beta vs SPY from overlapping daily returns.
    beta = float("nan")
    try:
        spy_df = load_ohlcv("SPY", curr_date).sort_values("Date").tail(look_back_days)
        merged = df.merge(spy_df, on="Date", suffixes=("", "_spy"))
        if len(merged) >= 20:
            r_t = np.diff(np.log(merged["Close"].astype(float).to_numpy()))
            r_m = np.diff(np.log(merged["Close_spy"].astype(float).to_numpy()))
            cov = np.cov(r_t, r_m, ddof=1)
            if cov[1, 1] > 0:
                beta = cov[0, 1] / cov[1, 1]
    except Exception:
        pass  # SPY missing or merge failed — leave beta as NaN

    return {
        "trading_days": len(df),
        "annual_vol": _format_pct(sigma_annual),
        "sharpe": f"{sharpe:.2f}",
        "sortino": "n/a" if np.isnan(sortino) else f"{sortino:.2f}",
        "var_95": _format_pct(var_95),
        "cvar_95": _format_pct(cvar_95),
        "max_drawdown": _format_pct(max_dd),
        "beta_spy": "n/a" if np.isnan(beta) else f"{beta:.2f}",
    }


@tool
def get_risk_metrics(
    symbol: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current trading date, YYYY-mm-dd"],
    look_back_days: Annotated[int, "trading-day window for the calculation"] = 90,
) -> str:
    """Compute risk metrics deterministically from the price cache.

    Returns annualized volatility, Sharpe (rf=0), Sortino, historical 95%
    VaR + CVaR, max drawdown over the window, and beta vs. SPY. These are
    objective numbers — surface them in the technical analysis report so
    the reader has a quantified risk view alongside the chart commentary.
    """
    try:
        m = _compute_metrics(symbol, curr_date, look_back_days)
    except Exception as exc:
        return f"리스크 지표 계산 실패 ({symbol}): {exc}"

    return (
        f"## {symbol} 리스크 지표 ({m['trading_days']} 거래일 기준)\n\n"
        f"- 연환산 변동성 (σ): {m['annual_vol']}\n"
        f"- Sharpe Ratio: {m['sharpe']}\n"
        f"- Sortino Ratio: {m['sortino']}\n"
        f"- 95% VaR (일간): {m['var_95']} — 최악 5% 일에 평균 이상 잃을 손실\n"
        f"- 95% CVaR (일간): {m['cvar_95']} — 그 5% 일들의 평균 손실 (꼬리 위험)\n"
        f"- 최대 낙폭 (Max Drawdown): {m['max_drawdown']}\n"
        f"- 베타 (SPY 대비): {m['beta_spy']}\n\n"
        f"※ Sharpe/Sortino는 무위험 수익률 0% 가정. 절대값보다 동종업종 비교용."
    )
