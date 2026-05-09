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
    logger.info("get_macro_context: called curr_date=%s", curr_date)

    # Fan out the 9 yfinance fetches in parallel — they're independent and
    # network-bound, so serial fetching wastes wall time and lets one slow
    # ticker delay the rest. ThreadPoolExecutor with a small pool keeps
    # yfinance's session reuse working.
    results: dict[str, tuple[float | None, float | None]] = {}
    with ThreadPoolExecutor(max_workers=9, thread_name_prefix="macro") as ex:
        future_to_meta = {
            ex.submit(_fetch_one, ticker, curr_date): (ticker, label, suffix)
            for ticker, label, suffix in _MACRO_SERIES
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
                _TOTAL_TIMEOUT_S, len(results), len(_MACRO_SERIES),
            )

    rows: list[str] = []
    missing: list[str] = []
    for ticker, label, suffix in _MACRO_SERIES:
        latest, pct = results.get(ticker, (None, None))
        if latest is None:
            missing.append(label)
            continue
        change = "n/a" if pct is None else f"{pct:+.2f}%"
        rows.append(f"- {label} ({ticker}): {_format_value(latest, suffix)} (30D {change})")

    fetched = len(rows)
    total = len(_MACRO_SERIES)
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
    out += (
        "\n\n※ 30D 변동률은 영업일 기준 약 21일 전 대비. "
        "금리 민감주 / 원자재 노출주 / 위험자산 포지션 분석 시 참고."
    )
    return out
