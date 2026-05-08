"""Macro snapshot tool — pulls a small set of headline indicators from the
yfinance macro tickers so the news/market analysts have current rate /
risk / commodity context without depending on the model's training cutoff.

All tickers go through yfinance, no API key required. Each fetch is a
short, cached call; if any single series fails we still return what we
have so a flaky upstream doesn't black out the whole analysis.

Indicators:
  ^TNX  — 10Y treasury yield
  ^FVX  — 5Y treasury yield
  ^IRX  — 13W T-bill yield
  ^VIX  — volatility index
  DX-Y.NYB — US dollar index
  CL=F  — WTI crude oil
  GC=F  — gold futures
  HG=F  — copper futures
  BTC-USD — bitcoin (risk-on/off proxy)
"""

from __future__ import annotations

import logging
from typing import Annotated

import pandas as pd
from langchain_core.tools import tool

from tradingagents.dataflows.stockstats_utils import yf_retry

logger = logging.getLogger(__name__)


_MACRO_SERIES = [
    ("^TNX", "10Y 국채금리", "%"),
    ("^FVX", "5Y 국채금리", "%"),
    ("^IRX", "13W T-bill", "%"),
    ("^VIX", "VIX 지수", ""),
    ("DX-Y.NYB", "달러 인덱스 (DXY)", ""),
    ("CL=F", "WTI 원유", "$"),
    ("GC=F", "금 (선물)", "$"),
    ("HG=F", "구리 (선물)", "$"),
    ("BTC-USD", "비트코인", "$"),
]


def _fetch_one(ticker: str, curr_date: str) -> tuple[float | None, float | None]:
    """Return (latest_close, pct_change_30d) or (None, None) on failure.

    Uses a 35-trading-day lookback window so we always have enough rows
    for a 30-day comparison, with a small buffer for weekends/holidays.
    """
    import yfinance as yf  # local import — yfinance is heavyweight

    end = pd.Timestamp(curr_date)
    start = end - pd.Timedelta(days=60)
    try:
        df = yf_retry(
            lambda: yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=False,
                multi_level_index=False,
            )
        )
    except Exception as exc:
        logger.warning("macro: failed to fetch %s: %s", ticker, exc)
        return None, None

    if df is None or df.empty or "Close" not in df.columns:
        return None, None

    closes = df["Close"].dropna()
    if len(closes) < 2:
        return None, None
    latest = float(closes.iloc[-1])
    # Use ~21 trading days back for a "month ago" reference; fall back to
    # earliest available row if the window is shorter (newly-listed series).
    ref_idx = -22 if len(closes) > 22 else 0
    ref = float(closes.iloc[ref_idx])
    pct = (latest - ref) / ref * 100 if ref != 0 else None
    return latest, pct


def _format_value(value: float, suffix: str) -> str:
    if suffix == "%":
        # ^TNX, ^FVX, ^IRX are quoted in tenths of a percent in yfinance
        # (e.g. 4.42 means 4.42%, but the raw close is e.g. 44.20). Normalize.
        return f"{value / 10:.2f}%" if value > 20 else f"{value:.2f}%"
    if suffix == "$":
        return f"${value:,.2f}"
    return f"{value:,.2f}"


@tool
def get_macro_context(
    curr_date: Annotated[str, "current trading date, YYYY-mm-dd"],
) -> str:
    """Snapshot of headline macro indicators with 30-day percent change.

    Use this once per analysis to ground the news / market commentary in
    actual current rate / commodity / risk levels — especially important
    for rate-sensitive sectors (REITs, utilities, banks), commodity-tied
    names (energy, miners), and any risk-on/off positioning context.
    """
    rows = []
    for ticker, label, suffix in _MACRO_SERIES:
        latest, pct = _fetch_one(ticker, curr_date)
        if latest is None:
            continue
        change = "n/a" if pct is None else f"{pct:+.2f}%"
        rows.append(f"- {label} ({ticker}): {_format_value(latest, suffix)} (30D {change})")

    if not rows:
        return "거시 지표 데이터를 가져오지 못했습니다."

    return (
        "## 거시 지표 스냅샷\n\n"
        + "\n".join(rows)
        + "\n\n※ 30D 변동률은 영업일 기준 약 21일 전 대비. 금리 민감주 / "
        "원자재 노출주 / 위험자산 포지션 분석 시 참고."
    )
