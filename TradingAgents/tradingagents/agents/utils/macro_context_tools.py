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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Annotated

import pandas as pd
from langchain_core.tools import tool

from tradingagents.dataflows.stockstats_utils import yf_retry

logger = logging.getLogger(__name__)

# Per-fetch wall-clock budget. yfinance occasionally hangs; we want partial
# results delivered within a few seconds rather than blocking the analyst
# behind one slow series.
_PER_FETCH_TIMEOUT_S = 8.0
# Total budget across all 9 series. Set generously; the parallel fan-out
# means 9 successful fetches usually finish in ~1-2 s.
_TOTAL_TIMEOUT_S = 25.0


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


# Korean-market-specific macro snapshot. Same 9-slot shape as the US
# series but tilted toward what actually moves KRX-listed names:
#   - USD/KRW: export-driven economy, FX directly hits margins
#   - KOSPI / KOSDAQ: domestic market direction + small/mid-cap proxy
#   - US 10Y: KR growth stocks correlate with US yields (the standalone
#     KR yield curve via yfinance is too flaky to depend on — KR10YT=RR
#     returns empty for many days)
#   - VIX: global risk-on/off proxy still dominates EM equities
#   - WTI: KR is net oil importer
#   - Copper: heavy industrial economy (semis, autos, shipbuilding)
#   - CNY/USD: China is KR's #1 trade partner, RMB weakens KR competitively
#   - JPY/USD: KR/JP head-on in autos + electronics; weak yen pressures KR
_MACRO_SERIES_KR = [
    ("KRW=X", "원/달러 (USD/KRW)", ""),
    ("^KS11", "KOSPI 종합", ""),
    ("^KQ11", "KOSDAQ 종합", ""),
    ("^TNX", "美 10Y 국채금리", "%"),
    ("^VIX", "VIX 지수", ""),
    ("CL=F", "WTI 원유", "$"),
    ("HG=F", "구리 (선물)", "$"),
    ("CNY=X", "위안/달러 (USD/CNY)", ""),
    ("JPY=X", "엔/달러 (USD/JPY)", ""),
]


# JP-tilted macro 9-series. Same shape and intent as the KR set:
# pull the indicators that actually move .T-listed stocks on a short
# horizon, drop the US-specific ones that aren't relevant.
#   - USD/JPY: every ¥1 weaker = +¥40-50bn operating profit for
#     Toyota — the dominant single variable for the entire export
#     cohort.
#   - Nikkei 225 / TOPIX: domestic broad market + style overlay.
#   - 美 10Y: JP growth multiples co-move with US yields (esp.
#     semis/software when global risk-off hits).
#   - JGB 10Y: BoJ YCC stance proxy, dominant variable for banks
#     / insurers / REITs. Yfinance exposes via ^TNX-like? Actually
#     yfinance is unreliable for ^N225 yield series; we'll add a
#     FRED-backed JGB later. For now use ^TNX as the rate proxy.
#   - VIX: global risk-off carries over directly to JP.
#   - WTI: JP is a net energy importer, oil = inflation passthrough.
#   - 구리: industrial demand bellwether (semis, machinery exposure).
#   - USD/CNY: China is JP's #1 export market — RMB weakness hits
#     JP exporters' relative competitiveness.
#   - 비트코인: same risk-on/risk-off proxy as the US set.
_MACRO_SERIES_JP = [
    ("JPY=X", "엔/달러 (USD/JPY)", ""),
    ("^N225", "Nikkei 225", ""),
    ("1306.T", "TOPIX (1306 ETF)", "¥"),
    ("^TNX", "美 10Y 국채금리", "%"),
    ("^VIX", "VIX 지수", ""),
    ("CL=F", "WTI 원유", "$"),
    ("HG=F", "구리 (선물)", "$"),
    ("CNY=X", "위안/달러 (USD/CNY)", ""),
    ("KRW=X", "원/달러 (USD/KRW)", ""),
]


# TW-tilted macro 9-series. TW market dominated by tech supply chain,
# so the picks tilt heavily toward variables that move semiconductor
# / EMS / shipping cohorts:
#   - USD/TWD: TSMC + EMS exporters report in NT$, USD weakness =
#     translation hit; TW central bank actively manages this currency
#   - TAIEX (^TWII): domestic broad index, primary TW signal
#   - 美 10Y: TW growth stocks correlate with US yields (esp. AI semis)
#   - VIX: global risk-on/off carries over directly
#   - WTI: TW is net oil importer; shipping fuel cost
#   - Copper: semiconductor manufacturing demand + tech cycle
#   - USD/JPY: JP competes with TW in semis + autos; weak yen
#     pressures TW exporters' competitiveness
#   - USD/CNY: China = TW's largest export market; CNY weakness hits
#     TW exporters' relative competitiveness same way it hits JP
#   - 호선 (SOX index ^SOX or proxy 통과 to SOXX): semi cycle directly
#     drives ~60% of TW market cap via TSMC / MTK / OSAT chain
_MACRO_SERIES_TW = [
    ("TWD=X", "대만달러/달러 (USD/TWD)", ""),
    ("^TWII", "TAIEX 가중지수", ""),
    ("^TNX", "美 10Y 국채금리", "%"),
    ("^VIX", "VIX 지수", ""),
    ("CL=F", "WTI 원유", "$"),
    ("HG=F", "구리 (선물)", "$"),
    ("JPY=X", "엔/달러 (USD/JPY)", ""),
    ("CNY=X", "위안/달러 (USD/CNY)", ""),
    ("SOXX", "美 반도체 ETF (SOXX)", "$"),
]


# CN A-share macro 9-series (Phase 4-CN). Drivers of mainland equity
# in 5거래일 horizon: USD/CNY 환율 (수출 / 외국인 자금 흐름), CSI 300
# 자체, Hang Seng (HSCEI 港股通 자금 sentiment 동조), 美 10Y (글로벌
# 위험자산 비중), VIX, WTI (중국 = 최대 원유 수입국), 구리 (중국 수요
# 대표), USD/JPY (수출 경쟁), USD/HKD peg (역외 자금 흐름).
_MACRO_SERIES_CN_A = [
    ("CNY=X", "위안/달러 (USD/CNY)", ""),
    ("000300.SS", "CSI 300 지수", ""),
    ("^HSI", "항생 지수 (Hang Seng)", ""),
    ("^TNX", "美 10Y 국채금리", "%"),
    ("^VIX", "VIX 지수", ""),
    ("CL=F", "WTI 원유", "$"),
    ("HG=F", "구리 (선물)", "$"),
    ("JPY=X", "엔/달러 (USD/JPY)", ""),
    ("HKD=X", "홍콩달러/달러 (USD/HKD)", ""),
]


# HK macro 9-series — overlaps with CN_A but emphasises 港股 specific
# drivers: HSI + HSCEI (H-shares), USD/HKD peg, USD/CNY (Internet VIE
# 본토 사업 영향), 美 10Y (HK 금리는 USD peg 따라 Fed 동조), VIX,
# WTI, 구리, CSI 300 (Southbound 자금 reaction).
_MACRO_SERIES_HK = [
    ("HKD=X", "홍콩달러/달러 (USD/HKD, peg 7.75-7.85)", ""),
    ("^HSI", "항생 지수 (Hang Seng)", ""),
    ("^HSCE", "HSCEI 國企指數", ""),
    ("^TNX", "美 10Y 국채금리 (HK 금리 동조)", "%"),
    ("^VIX", "VIX 지수", ""),
    ("CL=F", "WTI 원유", "$"),
    ("HG=F", "구리 (선물)", "$"),
    ("CNY=X", "위안/달러 (USD/CNY)", ""),
    ("000300.SS", "CSI 300 (본토 reference)", ""),
]


def _series_for_market(market: str):
    """Return the macro series list for the given market code. Falls
    back to the US series for unknown markets so CN tickers still
    get *some* macro context until Phase 4-CN ships its own set."""
    if market == "KR":
        return _MACRO_SERIES_KR
    if market == "JP":
        return _MACRO_SERIES_JP
    if market == "TW":
        return _MACRO_SERIES_TW
    if market == "CN_A":
        return _MACRO_SERIES_CN_A
    if market == "HK":
        return _MACRO_SERIES_HK
    return _MACRO_SERIES


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


# Sanity-check ranges per ticker. yfinance occasionally returns stale /
# wrong macro snapshots (currency lookup glitch, wrong-day close, etc.)
# and analysts pattern-match the number into their narrative without
# verification. 茅台 600519.SS 2026-05-19: WTI $103 + 30D +22.84%
# surfaced across 3 analysts when WTI was actually ~$60. Range tuples
# are intentionally wide (cover 90%+ of historical regimes); only
# clearly-impossible outliers trigger the warning.
_MACRO_SANITY_RANGES: dict[str, tuple[float, float]] = {
    "CL=F": (20.0, 200.0),       # WTI: $20-$200 covers 2008-2025 range
    "BZ=F": (20.0, 200.0),       # Brent
    "HG=F": (1.5, 8.0),          # Copper futures: $1.5-$8/lb
    "GC=F": (1000.0, 5000.0),    # Gold futures: broad
    "^TNX": (3.0, 80.0),         # US 10Y (raw tenths-of-percent): 0.3%-8%
    "^FVX": (3.0, 80.0),         # US 5Y
    "^IRX": (0.0, 80.0),         # US 13W bill (can be 0)
    "^VIX": (5.0, 100.0),        # VIX: 5-100
    "DX-Y.NYB": (60.0, 130.0),   # DXY
    "KRW=X": (800.0, 2200.0),    # USD/KRW
    "JPY=X": (60.0, 200.0),      # USD/JPY
    "CNY=X": (5.0, 9.0),         # USD/CNY
    "TWD=X": (25.0, 40.0),       # USD/TWD
    "HKD=X": (7.6, 8.0),         # USD/HKD (peg band)
    "^KS11": (1500.0, 4500.0),   # KOSPI
    "^KQ11": (500.0, 1500.0),    # KOSDAQ
    "^N225": (15000.0, 50000.0), # Nikkei
    "^HSI": (15000.0, 35000.0),  # Hang Seng
    "^TWII": (10000.0, 30000.0), # TAIEX
    "000300.SS": (3000.0, 6500.0),  # CSI 300
    "^HSCE": (5000.0, 12000.0),  # HSCEI
}


def _value_is_suspect(ticker: str, value: float) -> bool:
    """Return True when `value` is outside the conservative sanity range
    for this ticker. False (no warning) when ticker isn't in the map —
    don't false-flag tickers without a curated range."""
    rng = _MACRO_SANITY_RANGES.get(ticker)
    if not rng:
        return False
    lo, hi = rng
    return value < lo or value > hi


# 30D % change beyond these magnitudes is statistically rare for the
# series and warrants a sanity warning even when the absolute value
# stays within the wide _MACRO_SANITY_RANGES band. VIX moves wildly
# by nature (50%+ swings normal during crises) so it stays out of
# this list. Equity indices can move 20%+ in fast markets too.
_MACRO_PCT_CHANGE_30D_LIMIT: dict[str, float] = {
    # 2026-05-19 tightened after 茅台 + SMIC 모두 WTI +21~22% 데이터를
    # 분석가들이 무비판적으로 인용. 이전 25% threshold 가 너무 wide —
    # +22% 도 통과해 ⚠️ 마커 안 붙음. 15% = oil shock 진입 임계값으로
    # 더 보수적, false-positive 감수 가능 (real oil shock 은 흔히
    # >15% 이고 sanity warning 이 도움 됨).
    "CL=F": 15.0,        # WTI: 25 → 15 (oil shock zone 더 좁게)
    "BZ=F": 15.0,        # Brent
    "HG=F": 12.0,        # Copper: 20 → 12 (구리 1개월 ±12% 도 큰 move)
    "GC=F": 10.0,        # Gold: 15 → 10 (gold 매월 ±10% = 위기 신호)
    "^TNX": 25.0,        # 10Y: 30 → 25 (raw 25% = 4%→5% 정도 rate shock)
    "^FVX": 25.0,
    "DX-Y.NYB": 6.0,     # DXY: 8 → 6
    "KRW=X": 6.0,        # USD/KRW: 8 → 6
    "JPY=X": 6.0,        # USD/JPY: 8 → 6
    "CNY=X": 4.0,        # CNY: 5 → 4 (tightly managed)
    "TWD=X": 5.0,        # TWD: 6 → 5
    "HKD=X": 1.0,        # HKD peg: 1.5 → 1.0 (peg band 깨지면 즉시 의심)
}


def _pct_change_is_suspect(ticker: str, pct: float | None) -> bool:
    """Return True when 30D % change exceeds the per-series sanity limit.
    Catches the 茅台 600519.SS 2026-05-19 case where WTI returned
    $103 + 30D +22.84% — absolute value within range but the 30D move
    is in oil-shock territory and almost certainly a yfinance data
    glitch (close-of-different-day ticker swap)."""
    if pct is None:
        return False
    limit = _MACRO_PCT_CHANGE_30D_LIMIT.get(ticker)
    if limit is None:
        return False
    return abs(pct) > limit


@tool
def get_macro_context(
    curr_date: Annotated[str, "current trading date, YYYY-mm-dd"],
    market: Annotated[str, "market code: 'US' (default) or 'KR'"] = "US",
) -> str:
    """Snapshot of headline macro indicators with 30-day percent change.

    Use this once per analysis to ground the news / market commentary in
    actual current rate / commodity / risk levels — especially important
    for rate-sensitive sectors (REITs, utilities, banks), commodity-tied
    names (energy, miners), and any risk-on/off positioning context.

    The series fetched depends on `market`: US gets the original 9-set
    (US treasuries / VIX / DXY / oil / metals / BTC), KR gets a KR-tilted
    9-set (USD/KRW / KOSPI / KOSDAQ / US 10Y / VIX / oil / copper / CNY /
    JPY) since the macro drivers of KRX-listed names differ. Default
    stays US for backward compatibility with any cached @tool call sites.
    """
    series = _series_for_market(market)
    logger.info("get_macro_context: called curr_date=%s market=%s series=%d",
                curr_date, market, len(series))

    # Fan out the N yfinance fetches in parallel — they're independent and
    # network-bound, so serial fetching wastes wall time and lets one slow
    # ticker delay the rest. ThreadPoolExecutor with a small pool keeps
    # yfinance's session reuse working.
    results: dict[str, tuple[float | None, float | None]] = {}
    with ThreadPoolExecutor(max_workers=len(series), thread_name_prefix="macro") as ex:
        future_to_meta = {
            ex.submit(_fetch_one, ticker, curr_date): (ticker, label, suffix)
            for ticker, label, suffix in series
        }
        try:
            for future in as_completed(future_to_meta, timeout=_TOTAL_TIMEOUT_S):
                ticker, *_ = future_to_meta[future]
                try:
                    results[ticker] = future.result(timeout=_PER_FETCH_TIMEOUT_S)
                except Exception as exc:
                    logger.warning("macro: %s future raised: %s", ticker, exc)
                    results[ticker] = (None, None)
        except TimeoutError:
            logger.warning(
                "macro: total %ss budget exhausted; %d/%d fetched",
                _TOTAL_TIMEOUT_S, len(results), len(series),
            )

    rows: list[str] = []
    missing: list[str] = []
    suspect: list[str] = []  # tickers whose values fell outside sanity range
    for ticker, label, suffix in series:
        latest, pct = results.get(ticker, (None, None))
        if latest is None:
            missing.append(label)
            continue
        change = "n/a" if pct is None else f"{pct:+.2f}%"
        # For %-suffixed series the displayed value differs from raw
        # (^TNX 44.20 → 4.42%). Sanity-check the raw close vs the raw
        # range we stored — _MACRO_SANITY_RANGES uses raw scale for
        # consistency (so ^TNX uses 3.0-80.0 = 0.3%-8%).
        value_suspect = _value_is_suspect(ticker, latest)
        pct_suspect = _pct_change_is_suspect(ticker, pct)
        suspect_flag = value_suspect or pct_suspect
        marker = " ⚠️" if suspect_flag else ""
        if suspect_flag:
            reasons = []
            if value_suspect:
                reasons.append("abs 값 범위 outside")
            if pct_suspect:
                reasons.append(f"30D {change} 변동 과대")
            suspect.append(
                f"{label} ({ticker}): {_format_value(latest, suffix)}"
                f" ({', '.join(reasons)})"
            )
        rows.append(
            f"- {label} ({ticker}): {_format_value(latest, suffix)}"
            f" (30D {change}){marker}"
        )

    fetched = len(rows)
    total = len(series)
    logger.info("get_macro_context: ok %d/%d series", fetched, total)

    # Even on total failure, return a neutral template the analyst can
    # acknowledge without slipping into apology mode (which previously
    # leaked "도구 오류" phrasing into the user-facing report). The
    # analyst's prompt sees "스냅샷" + "미수집 항목" and treats it as
    # data, not as a failure to apologize for.
    if not rows:
        try:
            from bot.usage_tracker import log_tool_failure
            log_tool_failure(
                "get_macro_context",
                f"all {total} series failed (yfinance unreachable?)",
            )
        except Exception:
            pass
        return (
            f"## 거시 지표 스냅샷 (0/{total} 수집)\n\n"
            "현재 거시 지표 시계열이 일시적으로 모두 미수집 상태입니다. "
            "본 분석에서는 거시 컨텍스트를 미반영하고, 회사 고유 펀더멘털과 "
            "기술적 흐름에 집중해 결론을 내려주십시오. 거시 도구 자체에 대한 "
            "사과나 오류 메시지는 보고서에 포함하지 마십시오."
        )

    out = f"## 거시 지표 스냅샷 ({fetched}/{total} 수집)\n\n" + "\n".join(rows)
    if missing:
        out += f"\n\n※ 미수집 (일시적 fetch 실패): {', '.join(missing)}"
        try:
            from bot.usage_tracker import log_tool_failure
            log_tool_failure(
                "get_macro_context",
                f"partial: {len(missing)}/{total} missing — {','.join(missing)}",
            )
        except Exception:
            pass
    if suspect:
        # 茅台 600519.SS 2026-05-19 첫 검증: WTI $103 (실제 ~$60) 같은
        # yfinance 일시 lookup glitch / 다른 시리즈와 ticker swap 의심
        # 데이터가 3 분석가 모두에 의해 무비판적으로 인용됨. ⚠️ 마커 옆
        # 명시적 경고 절을 추가해 LLM 이 그 값을 narrative 에 anchor
        # 하기 전에 검증 권고.
        out += (
            "\n\n⚠️ 의심 데이터 검증 권고: 다음 시리즈가 사전 정의된"
            " 합리 범위를 벗어났습니다 (yfinance 일시 lookup glitch /"
            " ticker swap / 단가 단위 오류 의심):\n"
            + "\n".join(f"  • {s}" for s in suspect)
            + "\n\n위 값들은 narrative 에 직접 인용하기 전에 별도 검증"
            " 필요. 분석에서 인용 시 'yfinance 일시 데이터, 검증 권고'"
            " 한 줄 명시 + 결론 anchor 로 사용 금지."
        )
        try:
            from bot.usage_tracker import log_tool_failure
            log_tool_failure(
                "get_macro_context",
                f"suspect-range: {len(suspect)} values flagged",
            )
        except Exception:
            pass
    out += (
        "\n\n※ 30D 변동률은 영업일 기준 약 21일 전 대비. "
        "금리 민감주 / 원자재 노출주 / 위험자산 포지션 분석 시 참고."
    )
    return out
