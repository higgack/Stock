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


# Taiwan market — Yuanta + Fubon sector ETFs that track TW industry
# baskets. TW market dominated by tech supply chain so granular
# semi / EMS / financial ETFs available. Broad fallback is 0050.TW
# (元大台灣50 ETF, tracks TAIEX 50).
#
# yfinance industry strings for TW tickers mostly match the US/global
# vocabulary (Semiconductors, Banks - Regional, Marine Shipping,
# Telecom Services etc.) since yfinance uses GICS-style labels. We
# match against those same strings here; needle-based partial match
# also handles 'semiconductor equipment' / 'semiconductor' overlap.
_TW_INDUSTRY_OVERRIDES = [
    # ─── Semiconductor cluster (TW's dominant sector)
    ("semiconductor", ("00891.TW", "반도체 (元大台灣ESG永續半導體)")),
    ("semiconductor equipment", ("00891.TW", "반도체 (元大台灣ESG永續半導體)")),
    # ─── Banks / financial
    ("bank", ("0055.TW", "MSCI 금융 (元大MSCI金融)")),
    ("regional bank", ("0055.TW", "MSCI 금융 (元大MSCI金融)")),
    ("insurance", ("0055.TW", "MSCI 금융 (元大MSCI金融)")),
    ("asset management", ("0055.TW", "MSCI 금융 (元大MSCI金融)")),
    # ─── Tech / electronics
    ("electronic equipment", ("0053.TW", "전자 (元大電子)")),
    ("electronic components", ("0053.TW", "전자 (元大電子)")),
    ("consumer electronics", ("0053.TW", "전자 (元大電子)")),
    ("computer hardware", ("0053.TW", "전자 (元大電子)")),
    # ─── Telecom
    ("telecom services", ("0055.TW", "MSCI 금융 (元大MSCI金融)")),
    # ─── Shipping
    ("marine shipping", ("0050.TW", "TAIEX 50 (0050)")),
    ("shipping", ("0050.TW", "TAIEX 50 (0050)")),
    # ─── Petrochemical / steel — no narrow ETF, use broad fallback
    # via _TW_BROAD_FALLBACK below.
]

_TW_BROAD_FALLBACK = ("0050.TW", "TAIEX 50 (0050)")


# CN A-share market — mainland 上海 + 深圳 sector ETFs. Mostly 易方达
# (yifang da) + 华夏 (huaxia) + 国泰 issuers. Industry strings match
# the same yfinance vocabulary as the US/JP/TW maps. Broad fallback
# 510300.SS (CSI 300 — 易方达 沪深300 ETF).
_CN_A_INDUSTRY_OVERRIDES = [
    # ─── 半導體 / 반도체
    ("semiconductor", ("512760.SS", "반도체 (国泰CES半导体芯片 ETF)")),
    ("semiconductor equipment", ("512760.SS", "반도체 (国泰CES半导体芯片 ETF)")),
    # ─── 银行 (4大 SOE 은행 추적)
    ("bank", ("512800.SS", "은행 (华宝中证银行 ETF)")),
    ("regional bank", ("512800.SS", "은행 (华宝中证银行 ETF)")),
    # ─── 保险 / 金融 broad
    ("insurance", ("512070.SS", "보험증권 (易方达 中证非银金融 ETF)")),
    ("capital markets", ("512070.SS", "보험증권 (易方达 中证非银金融 ETF)")),
    ("asset management", ("512070.SS", "보험증권 (易方达 中证非银金融 ETF)")),
    # ─── 의료 / 의약
    ("drug manufacturers", ("512170.SS", "의료 (华宝中证医疗 ETF)")),
    ("pharmaceutical", ("512170.SS", "의료 (华宝中证医疗 ETF)")),
    ("biotech", ("512170.SS", "의료 (华宝中证医疗 ETF)")),
    ("medical devices", ("512170.SS", "의료 (华宝中证医疗 ETF)")),
    # ─── 백주 / 소비
    ("beverages - wineries", ("512690.SS", "주류 (鹏华中证酒 ETF)")),
    ("beverages", ("512690.SS", "주류 (鹏华中证酒 ETF)")),
    ("packaged foods", ("159928.SZ", "소비 (汇添富中证主要消费 ETF)")),
    # ─── 자동차 / 신에너지차
    ("auto manufacturers", ("159805.SZ", "자동차 (国泰中证新能源汽车 ETF)")),
    ("auto parts", ("159805.SZ", "자동차 (国泰中证新能源汽车 ETF)")),
    # ─── 锂电池 / 신에너지
    ("electrical equipment", ("159755.SZ", "신에너지 (易方达 中证新能源 ETF)")),
    ("solar", ("515790.SS", "광전지 (华泰柏瑞中证光伏产业 ETF)")),
    # ─── 통신
    ("telecom services", ("515980.SS", "통신 (华夏中证5G 通信主题 ETF)")),
    # ─── 부동산
    ("real estate", ("512200.SS", "부동산 (南方中证全指房地产 ETF)")),
    # ─── 항공
    ("airlines", ("510300.SS", "CSI 300 (510300)")),  # 항공 ETF 미약 — 광역 fallback
    # ─── 석유 / 가스 / 강
    ("oil & gas", ("159930.SZ", "에너지 (汇添富中证能源 ETF)")),
    ("steel", ("515210.SS", "강철 (国泰中证钢铁 ETF)")),
    # ─── 가전 / consumer
    ("furnishings, fixtures & appliances", ("159996.SZ", "가전 (国泰中证家电 ETF)")),
    ("consumer electronics", ("159996.SZ", "가전 (国泰中证家电 ETF)")),
    # ─── Internet (small A-share roster; HK better coverage)
    ("internet content", ("510300.SS", "CSI 300 (510300)")),
]

_CN_A_BROAD_FALLBACK = ("510300.SS", "CSI 300 (510300)")


# HK market — Hang Seng-aligned + Hang Seng China Enterprise + KraneShares
# (KWEB) for Internet. Cross-listed China sector ETFs (3033 HSCEI etc.)
# track 港股 only — they sit cleanly with HK-quoted peers in Comps tables.
_HK_INDUSTRY_OVERRIDES = [
    # ─── Internet (KWEB tracks China internet, mostly HK + ADR)
    ("internet content", ("3033.HK", "Internet (恒生科技 ETF)")),
    ("internet retail", ("3033.HK", "Internet (恒生科技 ETF)")),
    # ─── 银行 / 은행
    ("bank", ("2828.HK", "HSCEI (盈富香港H股 ETF)")),
    ("regional bank", ("2828.HK", "HSCEI (盈富香港H股 ETF)")),
    # ─── 保险
    ("insurance", ("2828.HK", "HSCEI (盈富香港H股 ETF)")),
    # ─── 부동산 (HK developers + China property)
    ("real estate", ("2778.HK", "HK 부동산 (沛富恒生地产 ETF)")),
    # ─── 통신 / 유틸 (HK SOE)
    ("telecom services", ("2828.HK", "HSCEI (盈富香港H股 ETF)")),
    ("utilities - regulated electric", ("2800.HK", "Tracker Fund HK (2800)")),
    # ─── 항공 / 석유 (HK H-shares dominate)
    ("airlines", ("2828.HK", "HSCEI (盈富香港H股 ETF)")),
    ("oil & gas", ("2828.HK", "HSCEI (盈富香港H股 ETF)")),
    # ─── 자동차 (BYD H + NIO + XPeng + LiAuto)
    ("auto manufacturers", ("3033.HK", "Internet+EV (恒生科技 ETF)")),
    # ─── 半导体 (SMIC H + Hua Hong, small HK roster)
    ("semiconductor", ("3033.HK", "Internet+Semi (恒生科技 ETF)")),
]

_HK_BROAD_FALLBACK = ("2800.HK", "Tracker Fund HK (Hang Seng, 2800)")


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

    if market == "TW":
        for needle, etf in _TW_INDUSTRY_OVERRIDES:
            if needle in industry:
                return etf
        # No specific Yuanta sector match — use TAIEX 50 broad ETF.
        return _TW_BROAD_FALLBACK

    if market == "CN_A":
        for needle, etf in _CN_A_INDUSTRY_OVERRIDES:
            if needle in industry:
                return etf
        return _CN_A_BROAD_FALLBACK

    if market == "HK":
        for needle, etf in _HK_INDUSTRY_OVERRIDES:
            if needle in industry:
                return etf
        return _HK_BROAD_FALLBACK

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
        "\n\nTABLE PRESERVATION RULE (mandatory — 茅台 600519.SS 2026-"
        "05-19 surfaced this): 위 마크다운 표를 본문에 인용할 때 반드시"
        " **헤더 행 + 구분선 + 데이터 행** 전체 구조를 그대로 유지하라."
        " 표를 '• 30D: -6.13% — -7.71% — +8.40% — +1.58%p — -14.53%p'"
        " 식의 dash-separated 한 줄 bullet 로 압축하면 reader 가 어느"
        " 컬럼이 무엇인지 식별 불가 (subject / 벤치마크 / 광역 / vs섹터 /"
        " vs광역 5개 컬럼이 dash 만으로 구분되어 의미 소실). 헤더 그대로"
        " 또는 inline prose 시 'subject -6.13% / 섹터 -7.71% / CSI 300"
        " +8.40% / vs 섹터 +1.58%p / vs CSI 300 -14.53%p' 식의 라벨 동반"
        " 표기 필수."
    )
    if max_abs_vs_sector > 25:
        # F5 (Sony 6758.T 2026-05-20 audit): JP TOPIX-17 sector ETFs 의
        # thin liquidity 가 sparse / stale yfinance data → 25%p 초과
        # divergence 가 정량 분석 오류 일 수 있음. JP-only 케이스에는
        # softer wording + 1306.T broad fallback 검토 권고. 다른 시장
        # 은 기존 strict HOLD 유지 (한국전력 2026-05-17 패턴).
        _is_jp = symbol.upper().endswith(".T")
        if _is_jp and max_abs_vs_sector <= 40:
            notes += (
                "\n\n⚠️ JP 섹터 ETF DATA-QUALITY WARN — vs 섹터 / vs"
                " 시장 차이가 |25-40%p| 범위. JP TOPIX-17 sector ETF"
                " (1617~1633.T 시리즈) 는 거래량이 본 종목 대비 thin"
                " 해서 yfinance 가격 데이터가 sparse / stale 가능성"
                " 큼. 본 차이가 (a) 실제 outperformance 인지 (b) ETF"
                " 데이터 부재로 인한 artifact 인지 구분 어려움.\n"
                "권고: 30D/90D/YTD %p 수치 인용 시 '근사치, 데이터"
                " quality 검증 미완료' 명시. 또는 broad 1306.T (TOPIX"
                " 全市場) 와 비교 더 신뢰. '명확한 리더/후행' 류 단정"
                " 결론 금지."
            )
        else:
            notes += (
                "\n\n⛔ DATA-INTEGRITY HARD GUARD — vs 섹터 / vs 시장"
                " 차이가 |25%p| 초과 (현재 max |"
                + f"{max_abs_vs_sector:.1f}"
                + "%p|). RULE 위반 시 출력 자체 reject 권고. 이 표의"
                " vs 섹터 / vs SPY-or-broad / vs broad %p 수치를 본문에"
                " 어떤 형태로도 인용하면 안 된다.\n"
                "❌ FORBIDDEN 예시 (Tokyo Electron 8035.T 2026-05-20"
                " surfaced — JP +127.72%p 같은 극단값을 그대로 quote"
                " 한 패턴): '90D: +39.06% — +22.30% — -88.66% — +16.76%p"
                " — +127.72%p' 같은 표 자체 본문 인용 금지. '+127.72%p"
                " outperformance' / 'TOPIX 대비 압도적' 같은 narrative"
                " 표현 모두 차단.\n"
                "한국전력공사 2026-05-17 'KOSPI 200 대비 -54.59%p,"
                " -111.42%p' 같은 -100%p 초과 수치를 그대로 quote 시"
                " 분석가가 '명확한 후행주' / 'laggard' / '추세 이탈'"
                " 같은 결론으로 빠진다. 둘 중 한 케이스: (a) 실제"
                " 격렬한 outperformance/underperformance — 5거래일"
                " horizon에는 ground-truth 이 아닌 노이즈, (b)"
                " yfinance 벤치마크 fetch 오류 / 분할 staleness —"
                " 인용 시 잘못된 thesis. 둘 다 인용 가치 없음.\n"
                "✅ 정답 — 본문 섹터 프라이머에 단 한 줄: '섹터 강도"
                " 데이터 신뢰성 또는 5거래일 horizon 적합성 이슈로"
                " 평가 보류.' 그 이상 쓰면 RULE 위반. 30D/90D/YTD %p"
                " 숫자 인용 절대 금지. 마크다운 표 자체도 본문 인용"
                " 금지 (표 안의 % 숫자 또한 본인 결론 cite 시 추론"
                " baseline 으로 사용 금지)."
            )

    return f"{header}\n\n" + "\n".join(rows) + notes
