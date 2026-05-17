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


# Korean market — KODEX sector ETFs. yfinance returns the same English
# sector/industry vocabulary for KRX-listed names (Samsung Electronics
# is sector='Technology' / industry='Consumer Electronics', Kakao is
# sector='Communication Services' / industry='Internet Content &
# Information'), so we can reuse substring matching on `industry` and
# fall back to KODEX 200 as the broad benchmark when no specific KODEX
# sector fits. Maintained as a list (not dict) so KODEX 반도체 wins over
# the generic KODEX 200 default. Source: KRX ETF listings as of 2026.
_KR_INDUSTRY_OVERRIDES = [
    ("semiconductor", ("091160.KS", "반도체 (KODEX 반도체)")),
    ("software", ("266370.KS", "IT (KODEX IT)")),
    ("internet content", ("266370.KS", "IT (KODEX IT)")),
    ("communication equipment", ("266370.KS", "IT (KODEX IT)")),
    ("auto", ("091180.KS", "자동차 (KODEX 자동차)")),
    ("bank", ("091170.KS", "은행 (KODEX 은행)")),
    ("insurance", ("140700.KS", "보험 (KODEX 보험)")),
    ("capital markets", ("102970.KS", "증권 (KODEX 증권)")),
    ("biotechnology", ("244580.KS", "바이오 (KODEX 바이오)")),
    ("drug manufacturers", ("266420.KS", "헬스케어 (KODEX 헬스케어)")),
    ("medical", ("266420.KS", "헬스케어 (KODEX 헬스케어)")),
    ("construction", ("117700.KS", "건설 (KODEX 건설)")),
    ("steel", ("117680.KS", "철강 (KODEX 철강)")),
    ("chemical", ("102710.KS", "화학 (KODEX 화학)")),
    ("battery", ("305720.KS", "2차전지 (KODEX 2차전지산업)")),
    ("electronic gaming", ("300950.KS", "게임 (KODEX 게임산업)")),
    ("entertainment", ("266360.KS", "미디어 (KODEX 미디어&엔터)")),
    ("broadcasting", ("266360.KS", "미디어 (KODEX 미디어&엔터)")),
    ("airlines", ("140710.KS", "운송 (KODEX 운송)")),
    ("marine shipping", ("140710.KS", "운송 (KODEX 운송)")),
    ("trucking", ("140710.KS", "운송 (KODEX 운송)")),
    ("machinery", ("102960.KS", "기계 (KODEX 기계장비)")),
    ("oil & gas", ("117460.KS", "에너지화학 (KODEX 에너지화학)")),
]

# Broad-market fallback when KR sector mapping doesn't hit anything
# specific. KOSPI 200 is the closest KR analogue to SPY for US.
_KR_BROAD_FALLBACK = ("069500.KS", "KOSPI 200 (KODEX 200)")


# Japanese sector ETFs — NEXT FUNDS TOPIX-17 series (Nomura).
# The TOPIX-33 series exists but liquidity for the narrow buckets is
# too thin for benchmark relative-strength calculations. TOPIX-17 is
# the right granularity for sector strength: broad enough to be
# tradable, narrow enough to isolate sub-industry performance.
# Source: https://nextfunds.jp/lineup/ (NEXT FUNDS official lineup).
_JP_INDUSTRY_OVERRIDES = [
    # 食品
    ("packaged foods", ("1617.T", "식품 (NEXT FUNDS TOPIX-17 식품)")),
    ("beverages", ("1617.T", "식품 (NEXT FUNDS TOPIX-17 식품)")),
    ("food distribution", ("1617.T", "식품 (NEXT FUNDS TOPIX-17 식품)")),
    # エネルギー資源
    ("oil & gas", ("1618.T", "에너지 (NEXT FUNDS TOPIX-17 에너지)")),
    ("oil and gas", ("1618.T", "에너지 (NEXT FUNDS TOPIX-17 에너지)")),
    # 建設・資材
    ("construction", ("1619.T", "건설·자재 (NEXT FUNDS TOPIX-17 건설)")),
    ("building materials", ("1619.T", "건설·자재 (NEXT FUNDS TOPIX-17 건설)")),
    # 素材・化学
    ("chemical", ("1620.T", "소재·화학 (NEXT FUNDS TOPIX-17 화학)")),
    ("specialty chemical", ("1620.T", "소재·화학 (NEXT FUNDS TOPIX-17 화학)")),
    # 医薬品
    ("drug manufacturers", ("1621.T", "제약 (NEXT FUNDS TOPIX-17 제약)")),
    ("pharma", ("1621.T", "제약 (NEXT FUNDS TOPIX-17 제약)")),
    ("biotech", ("1621.T", "제약 (NEXT FUNDS TOPIX-17 제약)")),
    # 自動車・輸送機
    ("auto", ("1622.T", "자동차 (NEXT FUNDS TOPIX-17 자동차)")),
    # 鉄鋼・非鉄
    ("steel", ("1623.T", "철강·비철 (NEXT FUNDS TOPIX-17 철강)")),
    ("metals & mining", ("1623.T", "철강·비철 (NEXT FUNDS TOPIX-17 철강)")),
    # 機械
    ("industrial machinery", ("1624.T", "기계 (NEXT FUNDS TOPIX-17 기계)")),
    ("farm & heavy machinery", ("1624.T", "기계 (NEXT FUNDS TOPIX-17 기계)")),
    # 電機・精密
    ("electronic components", ("1625.T", "전기·정밀 (NEXT FUNDS TOPIX-17 전기)")),
    ("semiconductor", ("1625.T", "전기·정밀 (NEXT FUNDS TOPIX-17 전기)")),
    ("consumer electronics", ("1625.T", "전기·정밀 (NEXT FUNDS TOPIX-17 전기)")),
    ("scientific & technical instruments", ("1625.T", "전기·정밀 (NEXT FUNDS TOPIX-17 전기)")),
    # 情報通信・サービスその他
    ("software", ("1626.T", "IT·서비스 (NEXT FUNDS TOPIX-17 IT)")),
    ("internet content", ("1626.T", "IT·서비스 (NEXT FUNDS TOPIX-17 IT)")),
    ("communication services", ("1626.T", "IT·서비스 (NEXT FUNDS TOPIX-17 IT)")),
    ("information technology services", ("1626.T", "IT·서비스 (NEXT FUNDS TOPIX-17 IT)")),
    # 電気・ガス
    ("utilities - regulated electric", ("1627.T", "전력·가스 (NEXT FUNDS TOPIX-17 전력)")),
    ("utilities - regulated gas", ("1627.T", "전력·가스 (NEXT FUNDS TOPIX-17 전력)")),
    # 運輸・物流
    ("railroads", ("1628.T", "운수·물류 (NEXT FUNDS TOPIX-17 운수)")),
    ("trucking", ("1628.T", "운수·물류 (NEXT FUNDS TOPIX-17 운수)")),
    ("airlines", ("1628.T", "운수·물류 (NEXT FUNDS TOPIX-17 운수)")),
    ("marine shipping", ("1628.T", "운수·물류 (NEXT FUNDS TOPIX-17 운수)")),
    # 商社・卸売
    ("conglomerates", ("1629.T", "상사·도매 (NEXT FUNDS TOPIX-17 상사)")),
    ("trading companies", ("1629.T", "상사·도매 (NEXT FUNDS TOPIX-17 상사)")),
    # 小売
    ("specialty retail", ("1630.T", "소매 (NEXT FUNDS TOPIX-17 소매)")),
    ("department stores", ("1630.T", "소매 (NEXT FUNDS TOPIX-17 소매)")),
    ("apparel retail", ("1630.T", "소매 (NEXT FUNDS TOPIX-17 소매)")),
    # 銀行
    ("banks", ("1631.T", "은행 (NEXT FUNDS TOPIX-17 은행)")),
    # 金融（除く銀行）
    ("insurance", ("1632.T", "금융 (NEXT FUNDS TOPIX-17 금융)")),
    ("capital markets", ("1632.T", "금융 (NEXT FUNDS TOPIX-17 금융)")),
    # 不動産
    ("real estate", ("1633.T", "부동산 (NEXT FUNDS TOPIX-17 부동산)")),
    ("reit", ("1633.T", "부동산 (NEXT FUNDS TOPIX-17 부동산)")),
]

_JP_BROAD_FALLBACK = ("1306.T", "TOPIX (1306)")


def _resolve_benchmark(ticker: str) -> tuple[str, str] | None:
    """Pick the most specific sector/industry ETF for `ticker`.

    Returns (etf_symbol, korean_label) or None if yfinance refuses to
    give us a sector. Industry overrides win when they match; otherwise
    fall back to the broad sector ETF. Market detection (US/KR/JP/CN)
    determines which ETF family to use — KODEX for Korean tickers,
    SPDR Select Sector for US.
    """
    try:
        info = yf_retry(lambda: yf.Ticker(ticker).info) or {}
    except Exception as exc:
        logger.warning("sector lookup failed for %s: %s", ticker, exc)
        return None

    industry = (info.get("industry") or "").lower()
    sector = info.get("sector") or ""

    # Import here to avoid a circular dep — bot.market is part of the
    # bot package while this file lives under TradingAgents. The cost
    # is one extra import on the cold path; the lookup itself is O(1).
    try:
        from bot.market import detect_market
        market = detect_market(ticker)
    except Exception:
        market = "US"

    if market == "KR":
        for needle, etf in _KR_INDUSTRY_OVERRIDES:
            if needle in industry:
                return etf
        # No specific KODEX sector — use KOSPI 200 as broad fallback.
        return _KR_BROAD_FALLBACK

    if market == "JP":
        for needle, etf in _JP_INDUSTRY_OVERRIDES:
            if needle in industry:
                return etf
        # No specific TOPIX-17 sector match — use TOPIX broad ETF.
        return _JP_BROAD_FALLBACK

    # CN coverage to come in Phase 3. For now CN falls through to US
    # logic which probably won't have a useful mapping, returning None.
    for needle, etf in _INDUSTRY_OVERRIDES:
        if needle in industry:
            return etf
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

    # Broad-market benchmark is market-dependent. For Korean tickers we
    # compare against KOSPI 200 (KODEX 200), not SPY — SPY would tell
    # us the company is up/down relative to the US market, which isn't
    # the right frame for a KRX-listed name. Same logic will extend to
    # TOPIX for JP, CSI 300 for CN in later phases.
    try:
        from bot.market import get_market_config
        broad = get_market_config(symbol)["broad_benchmark"]
        broad_label = get_market_config(symbol)["broad_label"]
    except Exception:
        broad, broad_label = "SPY", "SPY"

    horizons = [(30, "30D"), (90, "90D")]
    rows = []
    rows.append("| 기간 | " + symbol + " | " + (bench_label or "섹터 ETF n/a") + " | " + broad_label + " | vs 섹터 | vs " + broad_label.split(" ")[0] + " |")
    rows.append("|---|---|---|---|---|---|")

    # Track relative diffs so we can flag implausible spreads. AAPL
    # 2026-05-13 surfaced an XLK relative-strength of -17.77%p over
    # 30 days, which is impossible because AAPL itself is a ~6% weight
    # of XLK — the two move within a few %p of each other. The
    # underlying yfinance fetch on the benchmark must have grabbed a
    # bad price. Flag anything beyond ±25%p so the LLM doesn't quote
    # the broken number verbatim.
    max_abs_vs_sector = 0.0
    stock_returns_collected = 0
    for days, label in horizons:
        stock_pct = _pct_return(symbol, curr_date, days)
        if stock_pct is not None:
            stock_returns_collected += 1
        bench_pct = _pct_return(bench_etf, curr_date, days) if bench_etf else None
        spy_pct = _pct_return(broad, curr_date, days)
        if stock_pct is not None and bench_pct is not None:
            max_abs_vs_sector = max(max_abs_vs_sector, abs(stock_pct - bench_pct))
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
    spy_ytd = _ytd_return(broad, curr_date)
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
    if max_abs_vs_sector > 25:
        notes += (
            "\n\n⚠️ DATA-INTEGRITY HOLD — vs 섹터 / vs 시장 차이가"
            " |25%p| 초과. 이 표의 vs 섹터 / vs SPY-or-broad 수치를"
            " 본문에 어떤 형태로도 인용하면 안 된다. 한국전력공사"
            " 2026-05-17 'KOSPI 200 대비 -54.59%p, -111.42%p' 같은"
            " 수치를 그대로 quote하면 분석가가 '명확한 후행주' /"
            " 'laggard' / '추세 이탈' 같은 결론으로 빠진다. 둘 중"
            " 한 케이스: (a) 실제 격렬한 outperformance/underperformance"
            " — 5거래일 horizon에는 ground-truth이 아닌 노이즈,"
            " (b) yfinance 벤치마크 fetch 오류 / 분할 staleness —"
            " 인용 시 잘못된 thesis. 둘 다 인용 가치 없음. 본문"
            " 섹터 프라이머에는 단 한 줄만 적어라: '섹터 강도 데이터"
            " 신뢰성 또는 5거래일 horizon 적합성 이슈로 평가 보류.'"
            " 더 길게 쓰지 말 것. 30D/90D/YTD %p 숫자 인용 금지."
        )

    return f"{header}\n\n" + "\n".join(rows) + notes
