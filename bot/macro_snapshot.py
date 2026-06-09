"""Macro snapshot data — domestic (BoK ECOS) + global (FRED + yfinance).

Ports the Standard View "Macro Snapshot" panel to the NOAH market.html
page: a grid of macro indicator cards (각 카드에 현재값 + 변동 + 12개월
스파크라인) plus three derived charts (금리·환율 추이 / 물가·경기 모멘텀 /
시장 센티먼트 게이지).

All sources free, LLM 0, ₩0:
  - 국내: 한국은행 ECOS (기준금리·국고채·CPI·경상수지) — BOK_ECOS_API_KEY
  - 글로벌: FRED (FFR·10Y·CPI·실업률·GDP) — FRED_API_KEY
           yfinance (달러인덱스·S&P·NASDAQ·VIX·원자재·USD/KRW)

Graceful: a missing key or a failed series just drops that card. Cards
render only when data is present, so the panel always looks clean.
12h disk cache (macro series move slowly).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

log = logging.getLogger("bot.macro_snapshot")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "macro_snapshot"
# 5분 — 글로벌 스냅샷(5분)과 값을 일치시킴(사용자 요청). FRED/ECOS 는 자체
# 12h 캐시라 재fetch 안 함 → 추가 비용은 fast_info(~26) + yf batch 2회/5분(저위험).
_CACHE_TTL_SEC = 300  # 5min

# ── Indicator definitions ───────────────────────────────────────────
# (key, label, unit, source, source_id, decimals)
#   source: "ecos" | "fred" | "fred_yoy" | "yf"
DOMESTIC = [
    ("kr_rate", "한국 기준금리", "%", "ecos", "base_rate", 2),
    ("kr_3y", "국고채 3년", "%", "ecos", "kr3y", 2),
    ("kr_10y", "국고채 10년", "%", "ecos", "kr10y", 2),
    ("usdkrw", "USD/KRW", "", "yf", "USDKRW=X", 1),
    ("kr_cpi", "한국 CPI", "", "ecos", "cpi_idx", 2),
    ("kr_ca", "경상수지", "억$", "ecos", "current_account", 0),
    ("kr_export", "한국 수출", "억$", "ecos", "export_amt", 0),
]

GLOBAL = [
    ("us_ffr", "미국 FFR", "%", "fred", "FEDFUNDS", 2),
    ("us_2y", "미국 2Y", "%", "fred", "DGS2", 2),
    ("us_10y", "미국 10Y", "%", "fred", "DGS10", 2),
    ("us_cpi", "미국 CPI", "", "fred", "CPIAUCSL", 2),
    ("us_unemploy", "미국 실업률", "%", "fred", "UNRATE", 1),
    ("us_ism", "미국 ISM PMI", "", "fred", "NAPM", 1),
    ("us_gdp", "미국 GDP", "%", "fred", "A191RL1Q225SBEA", 1),
    ("dxy", "달러인덱스", "", "yf", "DX-Y.NYB", 2),
    ("sp500", "S&P 500", "", "yf", "^GSPC", 2),
    ("nasdaq", "NASDAQ", "", "yf", "^IXIC", 2),
    ("vix", "VIX", "", "yf", "^VIX", 2),
    ("wti", "WTI", "$", "yf", "CL=F", 1),
    ("brent", "브렌트유", "$", "yf", "BZ=F", 1),
    ("copper", "구리", "$", "yf", "HG=F", 2),
    ("aluminum", "알루미늄", "$", "yf", "ALI=F", 2),
    ("gold", "금", "$", "yf", "GC=F", 0),
    ("silver", "은", "$", "yf", "SI=F", 2),
    ("natgas", "천연가스", "$", "yf", "NG=F", 2),
    ("platinum", "백금", "$", "yf", "PL=F", 0),
    ("corn", "옥수수", "$", "yf", "ZC=F", 0),
    ("wheat", "밀", "$", "yf", "ZW=F", 0),
    ("btc", "비트코인", "$", "yf", "BTC-USD", 0),
    ("eth", "이더리움", "$", "yf", "ETH-USD", 0),
    ("sol", "솔라나", "$", "yf", "SOL-USD", 2),
    ("doge", "도지코인", "$", "yf", "DOGE-USD", 4),
    ("xrp", "리플", "$", "yf", "XRP-USD", 4),
    ("bnb", "BNB", "$", "yf", "BNB-USD", 2),
]

# 지표 정의가 바뀌면(예: 은·알루미늄 추가) 디스크 캐시를 즉시 무효화하기
# 위한 버전 해시. 키/심볼 목록이 달라지면 2h TTL 과 무관하게 재빌드 →
# 새 지표가 stale 캐시에 묻혀 안 보이던 문제 방지.
import hashlib as _hashlib  # noqa: E402
_DEFS_VERSION = _hashlib.md5(
    repr([(k, sid) for k, _, _, _, sid, _ in (DOMESTIC + GLOBAL)]).encode()
).hexdigest()[:12]

_SPARK_N = 12  # months in sparkline


# ── FRED monthly fetch ──────────────────────────────────────────────
def _fred_monthly(series_id: str, months: int = _SPARK_N) -> list[float]:
    """FRED monthly observations, oldest→newest, last `months` values."""
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return []
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": months * 2,
                "frequency": "m",
            },
            timeout=12,
        )
        r.raise_for_status()
        obs = r.json().get("observations") or []
    except Exception as exc:
        log.warning("macro: FRED %s monthly failed: %s", series_id, exc)
        return []
    vals: list[float] = []
    for o in obs:
        v = o.get("value", "")
        if not v or v == ".":
            continue
        try:
            vals.append(float(v))
        except ValueError:
            continue
        if len(vals) >= months:
            break
    return list(reversed(vals))


# ── yfinance monthly batch ──────────────────────────────────────────
def _yf_monthly_batch(tickers: list[str]) -> dict[str, list[float]]:
    """Batch monthly close (13mo) for all tickers → {ticker: [floats]}."""
    out: dict[str, list[float]] = {}
    if not tickers:
        return out
    try:
        import yfinance as yf
        df = yf.download(
            " ".join(tickers),
            period="14mo",
            interval="1mo",
            progress=False,
            threads=True,
            timeout=20,
        )
        if df is None or df.empty:
            return out
        for tk in tickers:
            try:
                if len(tickers) > 1:
                    closes = df["Close"][tk].dropna()
                else:
                    closes = df["Close"].dropna()
                vals = [round(float(c), 4) for c in closes.tolist()][-_SPARK_N:]
                if vals:
                    out[tk] = vals
            except Exception:
                continue
    except Exception as exc:
        log.warning("macro: yf monthly batch failed: %s", exc)
    return out


def _yf_daily_change(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {value, change}} — '오늘' 값 기준.

    ⚠️ 일봉 iloc[-1]은 확정된 마지막 일봉이라 아시아 지수가 하루 늦게 나옴
    (Yahoo 일봉 갱신 지연). → fast_info(last_price+previous_close, 견적 기반)
    를 1차로 써 오늘 값+오늘 등락을 잡고, 실패 종목만 일봉 폴백."""
    out: dict[str, dict] = {}
    if not tickers:
        return out
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    # 1) 일봉 폴백
    daily: dict[str, dict] = {}
    try:
        df = yf.download(" ".join(tickers), period="5d", progress=False,
                         threads=True, timeout=20)
        if df is not None and not df.empty:
            for tk in tickers:
                try:
                    closes = (df["Close"][tk] if len(tickers) > 1
                              else df["Close"]).dropna()
                    if len(closes) >= 1:
                        cur = float(closes.iloc[-1])
                        prev = float(closes.iloc[-2]) if len(closes) >= 2 else cur
                        daily[tk] = {"value": cur, "change": cur - prev}
                except Exception:
                    continue
    except Exception as exc:
        log.warning("macro: yf daily batch failed: %s", exc)

    # 2) fast_info live — 오늘 값 + 오늘 등락
    def _live(tk: str):
        try:
            fi = yf.Ticker(tk).fast_info
            lp = getattr(fi, "last_price", None)
            pc = getattr(fi, "previous_close", None)
            if lp is not None and pc is not None:
                return tk, {"value": float(lp), "change": float(lp) - float(pc)}
        except Exception:
            pass
        return tk, None

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for tk, v in pool.map(_live, tickers):
                if v:
                    out[tk] = v
    except Exception as exc:
        log.warning("macro: fast_info live failed: %s", exc)

    # 3) 폴백 채우기 (fast_info 없는 종목)
    for tk in tickers:
        if tk not in out and tk in daily:
            out[tk] = daily[tk]
    return out


# ── ECOS monthly downsample ─────────────────────────────────────────
def _ecos_series(key: str) -> list[tuple[str, float]]:
    try:
        from bot.bok_ecos_client import fetch_series_points
        return fetch_series_points(key)
    except Exception as exc:
        log.info("macro: ECOS series %s failed: %s", key, exc)
        return []


def _downsample_monthly(points: list[tuple[str, float]], n: int = _SPARK_N) -> list[float]:
    """Collapse points to one-per-month (last value of each month), last n."""
    if not points:
        return []
    by_month: dict[str, float] = {}
    for t, v in points:
        # TIME like YYYYMMDD / YYYYMM / YYYYQn
        m = t[:6] if len(t) >= 6 else t
        by_month[m] = v  # points are sorted asc → last wins
    months = sorted(by_month.keys())[-n:]
    return [by_month[m] for m in months]


# ── Main ────────────────────────────────────────────────────────────
def fetch_macro_snapshot() -> dict[str, Any]:
    """Assemble the full macro snapshot. 12h disk cache.

    Returns {"domestic": [...], "global": [...], "charts": {...}, "ts": str}
    where each indicator is {key,label,unit,value,change,decimals,spark}.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / "snapshot.json"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < _CACHE_TTL_SEC:
                _cached = json.loads(cache_file.read_text())
                # 지표 정의가 그대로일 때만 캐시 사용 — 은/알루미늄 등 추가 시
                # 버전 불일치로 즉시 재빌드(stale 캐시에 새 지표 묻힘 방지).
                if _cached.get("version") == _DEFS_VERSION:
                    return _cached
        except Exception:
            pass

    log.info("macro_snapshot: fetching fresh macro data")

    # Collect yf tickers once.
    yf_tickers = [sid for _, _, _, src, sid, _ in (DOMESTIC + GLOBAL) if src == "yf"]
    yf_monthly = _yf_monthly_batch(yf_tickers)
    yf_daily = _yf_daily_change(yf_tickers)

    spark_cache: dict[str, list[float]] = {}

    def _build(defs: list) -> list[dict]:
        rows: list[dict] = []
        for key, label, unit, src, sid, dec in defs:
            value: Optional[float] = None
            change: Optional[float] = None
            spark: list[float] = []
            if src == "yf":
                d = yf_daily.get(sid)
                if d:
                    value, change = d["value"], d["change"]
                spark = yf_monthly.get(sid, [])
                if value is None and spark:
                    value = spark[-1]
            elif src == "fred":
                spark = _fred_monthly(sid)
                if spark:
                    value = spark[-1]
                    if len(spark) >= 2:
                        change = spark[-1] - spark[-2]
            elif src == "ecos":
                pts = _ecos_series(sid)
                if pts:
                    value = pts[-1][1]
                    if len(pts) >= 2:
                        change = pts[-1][1] - pts[-2][1]
                    spark = _downsample_monthly(pts)
            if value is None:
                continue  # graceful: drop empty cards
            spark_cache[key] = spark
            rows.append({
                "key": key, "label": label, "unit": unit,
                "value": value, "change": change, "decimals": dec,
                "spark": spark,
            })
        return rows

    domestic = _build(DOMESTIC)
    glob = _build(GLOBAL)

    # ── Derived charts (reuse the sparklines we already fetched) ──
    charts = _build_charts(spark_cache)

    kst = datetime.utcnow() + timedelta(hours=9)
    result = {
        "domestic": domestic,
        "global": glob,
        "charts": charts,
        "ts": kst.strftime("%m.%d. %H:%M KST"),
        "version": _DEFS_VERSION,
    }
    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


def _month_labels(n: int) -> list[str]:
    """Last n month labels (YYYY-MM), oldest→newest, anchored to today."""
    today = date.today()
    out: list[str] = []
    y, m = today.year, today.month
    seq = []
    for _ in range(n):
        seq.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(seq))


def _build_charts(spark: dict[str, list[float]]) -> dict[str, Any]:
    """Build the 3 chart payloads from cached sparklines.

    rates_fx: 미국 10Y + 한국 기준금리 (좌, %) + USD/KRW (우, 원)
    inflation: 미국 CPI + 한국 CPI (지수)
    sentiment: VIX 최신값 → 0-100 점수
    """
    def _align(series: list[list[float]]) -> int:
        lens = [len(s) for s in series if s]
        return min(lens) if lens else 0

    charts: dict[str, Any] = {}

    # rates_fx
    us10 = spark.get("us_10y", [])
    krr = spark.get("kr_rate", [])
    fx = spark.get("usdkrw", [])
    n = _align([us10, krr, fx])
    if n >= 2:
        charts["rates_fx"] = {
            "labels": _month_labels(n),
            "us_10y": us10[-n:],
            "kr_rate": krr[-n:],
            "usdkrw": fx[-n:],
        }

    # inflation
    uscpi = spark.get("us_cpi", [])
    krcpi = spark.get("kr_cpi", [])
    n2 = _align([uscpi, krcpi])
    if n2 >= 2:
        charts["inflation"] = {
            "labels": _month_labels(n2),
            "us_cpi": uscpi[-n2:],
            "kr_cpi": krcpi[-n2:],
        }

    # sentiment (VIX → 0-100, higher score = greed)
    vix_spark = spark.get("vix", [])
    if vix_spark:
        charts["sentiment"] = {"score": _vix_to_score(vix_spark[-1]), "vix": vix_spark[-1]}

    return charts


def _vix_to_score(vix: float) -> int:
    """Map VIX level → 0-100 sentiment score (greed high, fear low).
    Mirrors the Standard View piecewise mapping."""
    if vix <= 12:
        return 90
    if vix <= 18:
        return int(round(75 - (vix - 12) / 6 * 15))   # 75→60
    if vix <= 25:
        return int(round(60 - (vix - 18) / 7 * 20))   # 60→40
    if vix <= 35:
        return int(round(40 - (vix - 25) / 10 * 20))  # 40→20
    return 12


def sentiment_label(score: int) -> str:
    if score >= 75:
        return "탐욕"
    if score >= 55:
        return "낙관"
    if score >= 45:
        return "중립"
    if score >= 25:
        return "불안"
    return "공포"
