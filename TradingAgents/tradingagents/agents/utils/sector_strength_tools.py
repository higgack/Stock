"""Sector ETF relative-strength tool.

Looks up the ticker's GICS sector via yfinance, maps it to the matching
SPDR sector ETF (XLK, XLF, ...), and computes 30D / 90D / YTD relative
returns vs both the sector and the broad market (SPY).

Why this exists: the market analyst's "sector primer" was a qualitative
LLM read of where the name sits in its segment. With this tool we can
ground that paragraph in actual relative-strength numbers, so 'leader
vs laggard' isn't a guess — it's '+8.4% vs sector over 90 days'.

All data flows through the same OHLCV cache the indicators use, so this
adds at most one yfinance.info round-trip per ticker (the sector lookup).
"""

from __future__ import annotations

import logging
from typing import Annotated

import numpy as np
import pandas as pd
import yfinance as yf
from langchain_core.tools import tool

from tradingagents.dataflows.stockstats_utils import load_ohlcv, yf_retry

logger = logging.getLogger(__name__)


# yfinance returns sector strings from a small fixed vocabulary; map each
# to the SPDR sector ETF that tracks it. SPY is added separately as the
# broad-market benchmark.
_SECTOR_TO_ETF = {
    "Technology": ("XLK", "기술 (XLK)"),
    "Financial Services": ("XLF", "금융 (XLF)"),
    "Healthcare": ("XLV", "헬스케어 (XLV)"),
    "Consumer Cyclical": ("XLY", "경기소비재 (XLY)"),
    "Consumer Defensive": ("XLP", "필수소비재 (XLP)"),
    "Industrials": ("XLI", "산업재 (XLI)"),
    "Energy": ("XLE", "에너지 (XLE)"),
    "Utilities": ("XLU", "유틸리티 (XLU)"),
    "Real Estate": ("XLRE", "부동산 (XLRE)"),
    "Basic Materials": ("XLB", "소재 (XLB)"),
    "Communication Services": ("XLC", "커뮤니케이션 (XLC)"),
}

# Tighter sub-sector overrides for industries where the sector ETF is
# too broad. Keyed on the yfinance industry string (case-insensitive
# substring match). The sector-level mapping is still used as a fallback.
_INDUSTRY_OVERRIDES = [
    ("semiconductor", ("SOXX", "반도체 (SOXX)")),
    ("software", ("IGV", "소프트웨어 (IGV)")),
    ("biotechnology", ("IBB", "바이오테크 (IBB)")),
    ("oil & gas e&p", ("XOP", "에너지 E&P (XOP)")),
    ("oil & gas exploration", ("XOP", "에너지 E&P (XOP)")),
    ("regional bank", ("KRE", "지역은행 (KRE)")),
    ("solar", ("TAN", "태양광 (TAN)")),
    ("airlines", ("JETS", "항공 (JETS)")),
]


def _resolve_benchmark(ticker: str) -> tuple[str, str] | None:
    """Pick the most specific sector/industry ETF for `ticker`.

    Returns (etf_symbol, korean_label) or None if yfinance refuses to
    give us a sector. Industry overrides win when they match; otherwise
    fall back to the broad sector ETF.
    """
    try:
        info = yf_retry(lambda: yf.Ticker(ticker).info) or {}
    except Exception as exc:
        logger.warning("sector lookup failed for %s: %s", ticker, exc)
        return None

    industry = (info.get("industry") or "").lower()
    for needle, etf in _INDUSTRY_OVERRIDES:
        if needle in industry:
            return etf

    sector = info.get("sector") or ""
    return _SECTOR_TO_ETF.get(sector)


def _pct_return(symbol: str, curr_date: str, lookback_days: int) -> float | None:
    """Simple percent return over the last `lookback_days` trading rows.

    Pulls from the OHLCV cache so we don't pay the yfinance round-trip
    for benchmarks we look up repeatedly during the same analysis.
    """
    try:
        df = load_ohlcv(symbol, curr_date)
    except Exception as exc:
        logger.warning("ohlcv load failed for %s: %s", symbol, exc)
        return None
    if df.empty:
        return None
    df = df.sort_values("Date").tail(lookback_days + 1)
    if len(df) < 2:
        return None
    closes = df["Close"].astype(float).to_numpy()
    return float((closes[-1] - closes[0]) / closes[0] * 100)


def _ytd_return(symbol: str, curr_date: str) -> float | None:
    """Year-to-date return measured from the first trading day of the year
    that contains `curr_date`."""
    try:
        df = load_ohlcv(symbol, curr_date)
    except Exception:
        return None
    if df.empty:
        return None
    cutoff = pd.Timestamp(curr_date)
    year_start = pd.Timestamp(year=cutoff.year, month=1, day=1)
    in_year = df[(df["Date"] >= year_start) & (df["Date"] <= cutoff)]
    if len(in_year) < 2:
        return None
    closes = in_year["Close"].astype(float).to_numpy()
    return float((closes[-1] - closes[0]) / closes[0] * 100)


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def _fmt_relative(stock_pct: float | None, bench_pct: float | None) -> str:
    if stock_pct is None or bench_pct is None:
        return "n/a"
    diff = stock_pct - bench_pct
    return f"{diff:+.2f}%p"


@tool
def get_sector_relative_strength(
    symbol: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current trading date, YYYY-mm-dd"],
) -> str:
    """Compare a ticker's recent performance against its sector ETF and SPY.

    Returns 30-day / 90-day / YTD percent returns for the ticker, the
    matched sector/industry ETF, and SPY, plus the relative-strength
    differential (positive = ticker is leading, negative = lagging).
    Surface this in the sector primer so 'leader vs laggard' becomes a
    quantified claim instead of a guess.
    """
    logger.info("get_sector_relative_strength: called symbol=%s curr_date=%s",
                symbol, curr_date)
    bench = _resolve_benchmark(symbol)
    bench_etf, bench_label = bench if bench else (None, None)
    logger.info("get_sector_relative_strength: %s → benchmark=%s",
                symbol, bench_etf or "(none)")

    horizons = [(30, "30D"), (90, "90D")]
    rows = []
    rows.append("| 기간 | " + symbol + " | " + (bench_label or "섹터 ETF n/a") + " | SPY | vs 섹터 | vs SPY |")
    rows.append("|---|---|---|---|---|---|")

    stock_returns_collected = 0
    for days, label in horizons:
        stock_pct = _pct_return(symbol, curr_date, days)
        if stock_pct is not None:
            stock_returns_collected += 1
        bench_pct = _pct_return(bench_etf, curr_date, days) if bench_etf else None
        spy_pct = _pct_return("SPY", curr_date, days)
        rows.append(
            "| "
            + " | ".join(
                [
                    label,
                    _fmt_pct(stock_pct),
                    _fmt_pct(bench_pct),
                    _fmt_pct(spy_pct),
                    _fmt_relative(stock_pct, bench_pct),
                    _fmt_relative(stock_pct, spy_pct),
                ]
            )
            + " |"
        )

    stock_ytd = _ytd_return(symbol, curr_date)
    if stock_ytd is not None:
        stock_returns_collected += 1
    bench_ytd = _ytd_return(bench_etf, curr_date) if bench_etf else None
    spy_ytd = _ytd_return("SPY", curr_date)
    # If we couldn't pull a single stock-side return across all 3 horizons,
    # the underlying yfinance fetch is dead for this ticker. Surface that
    # to the telemetry log so persistent breakage is visible in aggregate.
    if stock_returns_collected == 0:
        try:
            from bot.usage_tracker import log_tool_failure
            log_tool_failure(
                "get_sector_relative_strength",
                f"{symbol}: all 3 horizons (30D/90D/YTD) returned no data",
            )
        except Exception:
            pass
    rows.append(
        "| YTD | "
        + " | ".join(
            [
                _fmt_pct(stock_ytd),
                _fmt_pct(bench_ytd),
                _fmt_pct(spy_ytd),
                _fmt_relative(stock_ytd, bench_ytd),
                _fmt_relative(stock_ytd, spy_ytd),
            ]
        )
        + " |"
    )

    header = f"## {symbol} 섹터 상대 강도"
    if bench_label:
        header += f" — 벤치마크: {bench_label}"
    else:
        header += " — 벤치마크 매핑 실패 (SPY만 비교)"

    notes = (
        "\n※ '+%p'는 해당 기간 동안 벤치마크 대비 초과 수익률 (퍼센트 포인트). "
        "지속적으로 양수면 섹터/시장 리더, 음수면 후행. "
        "30D/90D 둘 다 양수이고 차이가 5%p 이상이면 명확한 '리더', "
        "둘 다 음수이고 -5%p 이하면 '후행'."
    )

    return f"{header}\n\n" + "\n".join(rows) + notes
