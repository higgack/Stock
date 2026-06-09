"""Market overview data collection — yfinance batch + FRED + Finnhub.

Provides data for the market.html landing page:
  - Global market snapshot (indices, FX, commodities, crypto, FRED indicators)
  - Upcoming earnings calendar (Finnhub)
  - Recent KR research actions (한경 컨센서스 list page)

All free, LLM 0, ₩0.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests
import yfinance as yf

log = logging.getLogger("bot.market_overview")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "market_overview"
_CACHE_TTL_SEC = 900  # 15 min

# ── Market Snapshot Ticker Groups ────────────────────────────────────

CARD_ASIA = [
    ("KR 코스피", "^KS11"),
    ("KR 코스닥", "^KQ11"),
    ("TW 대만 가권", "^TWII"),
    ("JP 니케이 225", "^N225"),
    ("CN CSI 300", "000300.SS"),
    ("CN 상해 종합", "000001.SS"),
    ("HK 홍콩 항셍", "^HSI"),
    ("HK 항셍테크", "^HSTECH"),
    ("IN Nifty 50", "^NSEI"),
]

CARD_FX = [
    ("KR 원/달러", "USDKRW=X"),
    ("JP 엔/원 (100엔)", "JPYKRW=X"),
    ("EU 유로/달러", "EURUSD=X"),
    ("GB 파운드/달러", "GBPUSD=X"),
    ("CN 달러/위안", "USDCNY=X"),
]

CARD_COMMODITIES = [
    ("WTI 유가", "CL=F"),
    ("브렌트유", "BZ=F"),
    ("천연가스", "NG=F"),
    ("구리", "HG=F"),
    ("국제 금", "GC=F"),
    ("국제 은", "SI=F"),
    ("대두", "ZS=F"),
]

CARD_SENTIMENT = [
    ("VIX (공포지수)", "^VIX"),
    ("비트코인", "BTC-USD"),
    ("이더리움", "ETH-USD"),
    ("솔라나", "SOL-USD"),
    ("리플", "XRP-USD"),
    ("BNB", "BNB-USD"),
    ("도지코인", "DOGE-USD"),
]

CARD_US = [
    ("us S&P 500", "^GSPC"),
    ("us 나스닥 종합", "^IXIC"),
    ("us 다우 존스", "^DJI"),
    ("us 러셀 2000", "^RUT"),
    ("필라델피아 반도체", "^SOX"),
]

CARD_FUTURES = [
    ("us S&P 500 선물", "ES=F"),
    ("us 다우 존스 선물", "YM=F"),
    ("us 나스닥 100 선물", "NQ=F"),
    ("us 러셀 2000 선물", "RTY=F"),
]

CARD_EU = [
    ("EU 유로 스톡스 50", "^STOXX50E"),
    ("DE 독일 DAX", "^GDAXI"),
    ("GB 영국 FTSE 100", "^FTSE"),
]

ALL_CARDS = [
    ("한국 & 아시아", CARD_ASIA),
    ("주요 환율 (FX)", CARD_FX),
    ("핵심 지표 (금리/달러)", None),  # FRED — handled separately
    ("원자재 & 귀금속", CARD_COMMODITIES),
    ("시장 심리 & 코인", CARD_SENTIMENT),
    ("미국 지수", CARD_US),
    ("미국 지수 선물 (Futures)", CARD_FUTURES),
    ("유럽 지수", CARD_EU),
]

# ── FRED Economic Indicators ────────────────────────────────────────

FRED_INDICATORS = [
    ("US 미국채 10년 (금리)", "DGS10", "%", 90),
    ("US 미국채 30년 (금리)", "DGS30", "%", 90),
    ("달러 인덱스", None, "", 0),  # yfinance DX-Y.NYB
    ("미국 기준금리 (FFR)", "FEDFUNDS", "%", 365),
    ("JOLTS (비농업 구인)", "JTSJOL", "M", 365),
    ("비농업 고용 (월간)", "PAYEMS", "K", 365),
    ("실업수당 청구 (신규)", "ICSA", "", 90),
    ("실업률", "UNRATE", "%", 365),
    ("CPI (YoY)", "CPIAUCSL", "%", 730),
    ("PPI (YoY)", "PPIACO", "%", 730),
]

_DOLLAR_INDEX_TICKER = "DX-Y.NYB"


# ── yfinance batch fetch ────────────────────────────────────────────

def _all_yf_tickers() -> list[str]:
    """Collect all yfinance tickers needed."""
    tickers = []
    for _, items in ALL_CARDS:
        if items is None:
            continue
        for _, tk in items:
            tickers.append(tk)
    tickers.append(_DOLLAR_INDEX_TICKER)
    return tickers


def _fetch_yf_batch() -> dict[str, dict]:
    """Batch-fetch last 5 days for all tickers. Returns {ticker: {close, prev_close, change, pct}}."""
    tickers = _all_yf_tickers()
    result: dict[str, dict] = {}
    try:
        df = yf.download(
            " ".join(tickers),
            period="5d",
            progress=False,
            threads=True,
            timeout=20,
        )
        if df is None or df.empty:
            return result

        for tk in tickers:
            try:
                if len(tickers) > 1:
                    closes = df["Close"][tk].dropna()
                else:
                    closes = df["Close"].dropna()
                if len(closes) < 1:
                    continue
                cur = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else cur
                chg = cur - prev
                pct = (chg / prev * 100) if prev != 0 else 0.0
                result[tk] = {
                    "close": cur,
                    "prev_close": prev,
                    "change": chg,
                    "pct": pct,
                }
            except Exception:
                continue
    except Exception as exc:
        log.warning("market_overview: yfinance batch fetch error: %s", exc)
    return result


# ── FRED fetch ──────────────────────────────────────────────────────

def _fred_fetch_series(series_id: str, lookback_days: int) -> Optional[dict]:
    """Minimal FRED series fetch (reuses fred_client pattern)."""
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return None
    cache_dir = _CACHE_DIR / "fred"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    cache_file = cache_dir / f"{series_id}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 12:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}&file_type=json"
        f"&observation_start={start}&sort_order=desc&limit=15"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
    except Exception as exc:
        log.warning("fred: fetch %s failed: %s", series_id, exc)
        return None

    clean = []
    for row in obs:
        v, d = row.get("value", ""), row.get("date", "")
        if not v or v == "." or not d:
            continue
        try:
            clean.append((d, float(v)))
        except ValueError:
            continue
    if not clean:
        return None

    latest_date, latest_val = clean[0]
    prev_val = clean[1][1] if len(clean) >= 2 else None
    change = (latest_val - prev_val) if prev_val is not None else None

    result = {"value": latest_val, "time": latest_date, "prev_value": prev_val, "change": change}
    try:
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def _fetch_fred_yoy(series_id: str) -> Optional[dict]:
    """Fetch index-level FRED series and compute YoY %."""
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return None
    cache_dir = _CACHE_DIR / "fred"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    cache_file = cache_dir / f"{series_id}_yoy_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 12:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    start = (date.today() - timedelta(days=730)).isoformat()
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={api_key}&file_type=json"
        f"&observation_start={start}&sort_order=desc&limit=30"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
    except Exception:
        return None

    clean = []
    for row in obs:
        v, d = row.get("value", ""), row.get("date", "")
        if not v or v == "." or not d:
            continue
        try:
            clean.append((d, float(v)))
        except ValueError:
            continue
    if len(clean) < 2:
        return None

    latest_date, latest_val = clean[0]
    # Find value ~12 months ago
    try:
        latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
        target_dt = latest_dt - timedelta(days=365)
        yoy_base = None
        for d, v in clean:
            dt = datetime.strptime(d, "%Y-%m-%d")
            if dt <= target_dt:
                yoy_base = v
                break
        if yoy_base and yoy_base != 0:
            yoy = (latest_val - yoy_base) / yoy_base * 100
        else:
            yoy = None
    except Exception:
        yoy = None

    prev_val = clean[1][1] if len(clean) >= 2 else None
    prev_change = None
    if prev_val is not None:
        # Find prev's YoY base
        try:
            prev_date = clean[1][0]
            prev_dt = datetime.strptime(prev_date, "%Y-%m-%d")
            prev_target = prev_dt - timedelta(days=365)
            for d, v in clean:
                dt = datetime.strptime(d, "%Y-%m-%d")
                if dt <= prev_target:
                    if v != 0:
                        prev_change = (prev_val - v) / v * 100
                    break
        except Exception:
            pass

    change = (yoy - prev_change) if yoy is not None and prev_change is not None else None
    result = {"value": round(yoy, 2) if yoy is not None else None,
              "time": latest_date, "prev_value": round(prev_change, 2) if prev_change else None,
              "change": round(change, 2) if change is not None else None}
    try:
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def _fetch_all_fred() -> list[dict]:
    """Fetch all FRED indicators. Returns list of dicts."""
    results = []
    for label, series_id, unit, lookback in FRED_INDICATORS:
        if series_id is None:
            continue
        if series_id in ("CPIAUCSL", "PPIACO"):
            data = _fetch_fred_yoy(series_id)
        else:
            data = _fred_fetch_series(series_id, lookback)
        if data:
            data["label"] = label
            data["unit"] = unit
        results.append({"label": label, "unit": unit, "data": data})
    return results


# ── Finnhub Earnings Calendar ───────────────────────────────────────

def fetch_earnings_calendar(days_ahead: int = 14) -> list[dict]:
    """Fetch upcoming earnings from Finnhub. Returns list of earnings events."""
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return []

    cache_dir = _CACHE_DIR / "finnhub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"earnings_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    from_date = today.isoformat()
    to_date = (today + timedelta(days=days_ahead)).isoformat()
    url = f"https://finnhub.io/api/v1/calendar/earnings?from={from_date}&to={to_date}&token={api_key}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("earningsCalendar", [])
    except Exception as exc:
        log.warning("finnhub: earnings calendar fetch error: %s", exc)
        return []

    result = []
    for e in events:
        symbol = e.get("symbol", "")
        if not symbol:
            continue
        result.append({
            "symbol": symbol,
            "date": e.get("date", ""),
            "hour": e.get("hour", ""),
            "eps_estimate": e.get("epsEstimate"),
            "revenue_estimate": e.get("revenueEstimate"),
            "quarter": e.get("quarter"),
            "year": e.get("year"),
        })

    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


# ── 한경 Recent Research (list page) ────────────────────────────────

def fetch_recent_research_kr(limit: int = 15) -> list[dict]:
    """Fetch latest KR broker research reports from 한경 컨센서스 list page."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"kr_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 1:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    url = "https://consensus.hankyung.com/apps.analysis/analysis.list"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://consensus.hankyung.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        log.warning("hankyung: research list fetch error: %s", exc)
        return []

    results = []
    try:
        row_re = re.compile(
            r'<tr[^>]*>.*?class="[^"]*code[^"]*"[^>]*>(\d{6})</.*?'
            r'class="[^"]*name[^"]*"[^>]*>(.*?)</.*?'
            r'class="[^"]*broker[^"]*"[^>]*>(.*?)</.*?'
            r'class="[^"]*rating[^"]*"[^>]*>(.*?)</.*?'
            r'class="[^"]*title[^"]*"[^>]*>(.*?)</.*?'
            r'class="[^"]*date[^"]*"[^>]*>(.*?)</.*?</tr>',
            re.DOTALL,
        )
        for m in row_re.finditer(html):
            code, name, broker, rating, title, dt = (
                m.group(1).strip(),
                re.sub(r"<[^>]+>", "", m.group(2)).strip(),
                re.sub(r"<[^>]+>", "", m.group(3)).strip(),
                re.sub(r"<[^>]+>", "", m.group(4)).strip(),
                re.sub(r"<[^>]+>", "", m.group(5)).strip(),
                re.sub(r"<[^>]+>", "", m.group(6)).strip(),
            )
            results.append({
                "code": code,
                "name": name,
                "broker": broker,
                "rating": rating,
                "title": title,
                "date": dt,
            })
            if len(results) >= limit:
                break
    except Exception as exc:
        log.warning("hankyung: research list parse error: %s", exc)

    if not results:
        try:
            from bot.hk_consensus_client import fetch_consensus
            for tk in ["005930", "000660", "035420", "005380", "035720"]:
                c = fetch_consensus(tk, days_back=7)
                if c and c.get("reports"):
                    for rp in c["reports"][:3]:
                        results.append({
                            "code": tk,
                            "name": "",
                            "broker": rp.get("broker", ""),
                            "rating": rp.get("rating", ""),
                            "title": rp.get("title", ""),
                            "date": rp.get("date", ""),
                        })
        except Exception:
            pass

    try:
        cache_file.write_text(json.dumps(results, ensure_ascii=False))
    except Exception:
        pass
    return results[:limit]


# ── US Research (yfinance upgrades aggregated) ──────────────────────

def fetch_recent_research_us(limit: int = 15) -> list[dict]:
    """Fetch recent US upgrades/downgrades from top stocks."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"us_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 3:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    top_us = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
              "AVGO", "JPM", "V", "MA", "UNH", "HD", "PG", "JNJ",
              "NFLX", "CRM", "AMD", "INTC", "BA"]
    results = []

    def _fetch_one(tk):
        try:
            t = yf.Ticker(tk)
            ud = t.upgrades_downgrades
            if ud is None or ud.empty:
                return []
            items = []
            for idx, row in ud.head(3).iterrows():
                d = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
                items.append({
                    "symbol": tk,
                    "firm": row.get("Firm", ""),
                    "to_grade": row.get("ToGrade", ""),
                    "from_grade": row.get("FromGrade", ""),
                    "action": row.get("Action", ""),
                    "date": d,
                })
            return items
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch_one, tk): tk for tk in top_us}
        for fut in as_completed(futs):
            results.extend(fut.result())

    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    results = results[:limit]

    try:
        cache_file.write_text(json.dumps(results, ensure_ascii=False))
    except Exception:
        pass
    return results


# ── Main assembly ───────────────────────────────────────────────────

def fetch_market_snapshot() -> dict[str, Any]:
    """Fetch complete market snapshot for the landing page.
    Returns dict with keys: yf_data, fred_data, dollar_index, timestamp."""
    cache_dir = _CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "snapshot.json"
    if cache_file.exists():
        try:
            age = time.time() - cache_file.stat().st_mtime
            if age < _CACHE_TTL_SEC:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    log.info("market_overview: fetching fresh market snapshot")

    with ThreadPoolExecutor(max_workers=3) as pool:
        yf_fut = pool.submit(_fetch_yf_batch)
        fred_fut = pool.submit(_fetch_all_fred)
        yf_data = yf_fut.result()
        fred_data = fred_fut.result()

    dollar_idx = yf_data.pop(_DOLLAR_INDEX_TICKER, None)

    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    kst = now_utc + timedelta(hours=9)
    ts = kst.strftime("%m.%d. %H:%M KST")

    result = {
        "yf": {k: v for k, v in yf_data.items()},
        "fred": fred_data,
        "dollar_index": dollar_idx,
        "ts": ts,
    }

    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


def fetch_all_market_data() -> dict[str, Any]:
    """Fetch everything needed for market.html."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        snap_fut = pool.submit(fetch_market_snapshot)
        earn_fut = pool.submit(fetch_earnings_calendar, 14)
        kr_fut = pool.submit(fetch_recent_research_kr, 15)
        us_fut = pool.submit(fetch_recent_research_us, 15)

        return {
            "snapshot": snap_fut.result(),
            "earnings": earn_fut.result(),
            "research_kr": kr_fut.result(),
            "research_us": us_fut.result(),
        }
