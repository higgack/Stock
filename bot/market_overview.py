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
# 스냅샷(지수/환율 yfinance 배치 1콜 + FRED 일캐시) — 2분이면 시간당 30콜
# 수준이라 무위험, 헤드라인 데이터 신선도 ↑ (사용자 2026-06-11). Finviz/
# Naver 업종 TTL 은 각 클라이언트가 별도 보유(안티봇 경계 — 건드리지 말 것).
_CACHE_TTL_SEC = 120  # 2 min

# ── Market Snapshot Ticker Groups ────────────────────────────────────

CARD_ASIA = [
    ("KR 코스피", "^KS11"),
    ("KR 코스닥", "^KQ11"),
    ("TW 대만 가권", "^TWII"),
    ("JP 니케이 225", "^N225"),
    ("CN CSI 300", "000300.SS"),
    ("CN 상해 종합", "000001.SS"),
    ("HK 홍콩 항셍", "^HSI"),
    ("HK 항셍테크", "3033.HK"),  # ^HSTECH 지수는 yfinance 무데이터 → 추종 ETF(CSOP)로 대체
    ("IN Nifty 50", "^NSEI"),
]

CARD_FX = [
    ("KR 원/달러", "USDKRW=X"),
    ("JP 엔/원 (100엔)", "JPYKRW=X"),
    ("EU 유로/달러", "EURUSD=X"),
    ("GB 파운드/달러", "GBPUSD=X"),
    ("CN 달러/위안", "USDCNY=X"),
    ("HK 달러/미국달러", "USDHKD=X"),
    ("TW 대만달러/미국달러", "USDTWD=X"),
    ("IN 루피/달러", "USDINR=X"),
    ("AU 호주달러/달러", "AUDUSD=X"),
    ("CH 스위스프랑/달러", "USDCHF=X"),
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
    ("다우 운송", "^DJT"),
    ("KBW 은행", "^BKX"),
    ("나스닥 바이오", "^NBI"),
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
    ("FR 프랑스 CAC 40", "^FCHI"),
    ("CH 스위스 SMI", "^SSMI"),
]

CARD_AMERICAS = [
    ("CA 캐나다 TSX", "^GSPTSE"),
    ("BR 브라질 Bovespa", "^BVSP"),
    ("MX 멕시코 IPC", "^MXX"),
    ("VN 베트남 (VNM ETF)", "VNM"),  # ^VNINDEX 는 yfinance 무데이터 → VanEck 베트남 ETF 로 대체
    ("ID 인도네시아 JCI", "^JKSE"),
    ("SA 사우디 Tadawul", "^TASI.SR"),
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
    ("아메리카 & 이머징", CARD_AMERICAS),
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
    """{ticker: {close, prev_close, change, pct}} — '오늘' 값 기준.

    ⚠️ 일봉(yf.download period=5d, interval=1d)의 iloc[-1]은 '확정된 마지막
    일봉'이라, 아시아 지수(코스피/대만/니케이 등)는 Yahoo 일봉 갱신 지연으로
    하루 늦게(어제 종가) 나오는 버그가 있었다. → fast_info(last_price +
    previous_close, 견적 기반 near-real-time)를 1차로 써 '오늘 값 + 오늘 등락'
    을 정확히 잡고, 실패 종목만 일봉으로 폴백(회귀 0)."""
    tickers = _all_yf_tickers()
    result: dict[str, dict] = {}

    # 1) 일봉 batch — 폴백용 (fast_info 없는 종목)
    daily: dict[str, tuple[float, float]] = {}
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
                        daily[tk] = (cur, prev)
                except Exception:
                    continue
    except Exception as exc:
        log.warning("market_overview: yf daily batch error: %s", exc)

    # 2) fast_info live — 오늘 값(last_price) + 어제 종가(previous_close)
    def _live(tk: str):
        try:
            fi = yf.Ticker(tk).fast_info
            lp = getattr(fi, "last_price", None)
            pc = getattr(fi, "previous_close", None)
            if lp is not None and pc is not None and float(pc) != 0:
                return tk, (float(lp), float(pc))
        except Exception:
            pass
        return tk, None

    live: dict[str, tuple[float, float]] = {}
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for tk, v in pool.map(_live, tickers):
                if v:
                    live[tk] = v
    except Exception as exc:
        log.warning("market_overview: fast_info live error: %s", exc)

    # 3) merge — live(오늘) 우선, 없으면 daily 폴백
    for tk in tickers:
        if tk in live:
            cur, prev = live[tk]
        elif tk in daily:
            cur, prev = daily[tk]
        else:
            continue
        chg = cur - prev
        pct = (chg / prev * 100) if prev != 0 else 0.0
        result[tk] = {"close": cur, "prev_close": prev,
                      "change": chg, "pct": pct}
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
            "name": "",
        })

    # Batch-resolve company names from yfinance (best-effort)
    try:
        import yfinance as yf
        syms = list({r["symbol"] for r in result})[:30]
        tickers = yf.Tickers(" ".join(syms))
        name_map: dict[str, str] = {}
        for s in syms:
            try:
                info = tickers.tickers[s].info or {}
                name_map[s] = info.get("shortName") or info.get("longName") or ""
            except Exception:
                pass
        for r in result:
            r["name"] = name_map.get(r["symbol"], "")
    except Exception:
        pass

    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False))
    except Exception:
        pass
    return result


# 한국 주요 종목(KOSPI/KOSDAQ 대형주) — yfinance .calendar 로 예정 실적일.
# Finnhub 무료 티어가 KR 미커버 → 종목별 .calendar(무료)로 직접 산출.
_KR_EARNINGS_UNIVERSE = [
    ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"),
    ("373220.KS", "LG에너지솔루션"), ("207940.KS", "삼성바이오로직스"),
    ("005380.KS", "현대차"), ("000270.KS", "기아"),
    ("005490.KS", "POSCO홀딩스"), ("035420.KS", "NAVER"),
    ("035720.KS", "카카오"), ("051910.KS", "LG화학"),
    ("006400.KS", "삼성SDI"), ("068270.KS", "셀트리온"),
    ("105560.KS", "KB금융"), ("055550.KS", "신한지주"),
    ("012330.KS", "현대모비스"), ("028260.KS", "삼성물산"),
    ("066570.KS", "LG전자"), ("015760.KS", "한국전력"),
    ("034730.KS", "SK"), ("096770.KS", "SK이노베이션"),
    ("017670.KS", "SK텔레콤"), ("030200.KS", "KT"),
    ("086790.KS", "하나금융지주"), ("010130.KS", "고려아연"),
    ("009150.KS", "삼성전기"), ("011070.KS", "LG이노텍"),
    ("003670.KS", "포스코퓨처엠"), ("247540.KQ", "에코프로비엠"),
    ("086520.KQ", "에코프로"), ("196170.KQ", "알테오젠"),
    ("058470.KQ", "리노공업"), ("042700.KQ", "한미반도체"),
    ("091990.KQ", "셀트리온헬스케어"),
]


def fetch_earnings_calendar_kr(days_ahead: int = 90) -> list[dict]:
    """KR 주요 종목 예정 실적일 (yfinance .calendar, 무료). 12h 캐시.

    KR 실적일은 미국보다 드물게 분포 → 윈도 90일로 넓게. 추정치(EPS/매출)는
    yfinance 가 KR 에 대해 종종 None → 그대로 '—' 표시(정직)."""
    cache_dir = _CACHE_DIR / "finnhub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"earnings_kr_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < 12:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    cutoff = today + timedelta(days=days_ahead)

    def _one(item):
        tk, name = item
        try:
            cal = yf.Ticker(tk).calendar
            if not isinstance(cal, dict):
                return None
            eds = cal.get("Earnings Date") or []
            if not eds:
                return None
            d0 = eds[0]
            ds = d0.strftime("%Y-%m-%d") if hasattr(d0, "strftime") else str(d0)[:10]
            try:
                dd = datetime.strptime(ds, "%Y-%m-%d").date()
            except ValueError:
                return None
            if dd < today or dd > cutoff:
                return None
            return {
                "symbol": tk, "name": name, "date": ds, "hour": "",
                "eps_estimate": cal.get("Earnings Average"),
                "revenue_estimate": cal.get("Revenue Average"),
                "quarter": None, "year": None,
            }
        except Exception:
            return None

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_one, _KR_EARNINGS_UNIVERSE):
            if r:
                results.append(r)
    results.sort(key=lambda x: x.get("date", ""))
    try:
        cache_file.write_text(json.dumps(results, ensure_ascii=False))
    except Exception:
        pass
    return results


# ── Recent KR Research (Naver Finance 리서치 목록) ──────────────────
# 한경 컨센서스는 2026 초 JS 렌더링 전환으로 정적 scrape 불가 →
# Naver Finance 전체 시장 리서치 목록(naver_research_client)으로 대체.

def fetch_recent_research_kr(limit: int = 150) -> list[dict]:
    """Fetch latest KR 종목(기업) 리서치 리포트 — 일주일치(Naver Finance).

    개별 종목 분석이 Naver 리서치를 쓰는 것과 동일 소스. 사용자 정책
    2026-06-09: 최근 7일(일주일치) 윈도. Returns
    [{code, name, broker, rating, target, title, date}]."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"kr_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < (10 / 60):  # 10분 — naver 1h 갱신을 빠르게 반영
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    results: list[dict] = []
    try:
        from bot.naver_research_client import fetch_recent_research_market
        results = fetch_recent_research_market(limit=limit, days_back=7,
                                               max_pages=20)
    except Exception as exc:
        log.warning("naver research market fetch error: %s", exc)

    if results:  # truthy-only — 빈 결과(일시 실패) 캐시 안 함('또 갑자기 없음' 방지)
        try:
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception:
            pass
    return results[:limit]


def fetch_recent_research_kr_industry(limit: int = 80) -> list[dict]:
    """Fetch latest KR 산업(업종) 리서치 리포트 — 일주일치(Naver Finance).

    종목 리포트와 동일 7일 윈도. Returns [{category, broker, title, date, link}]."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"kr_industry_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < (10 / 60):  # 10분 — naver 1h 갱신을 빠르게 반영
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    results: list[dict] = []
    try:
        from bot.naver_research_client import fetch_recent_research_industry
        results = fetch_recent_research_industry(limit=limit, days_back=7,
                                                 max_pages=12)
    except Exception as exc:
        log.warning("naver research industry fetch error: %s", exc)

    if results:  # truthy-only — 빈 결과 캐시 안 함
        try:
            cache_file.write_text(json.dumps(results, ensure_ascii=False))
        except Exception:
            pass
    return results[:limit]


# ── US Research (yfinance upgrades aggregated) ──────────────────────

def fetch_recent_research_us(limit: int = 25) -> list[dict]:
    """Fetch recent US upgrades/downgrades from top stocks."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"us_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < 1:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    top_us = ["AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
              "AVGO", "JPM", "V", "MA", "UNH", "HD", "PG", "JNJ",
              "NFLX", "CRM", "AMD", "INTC", "BA", "LLY", "BRK-B",
              "WMT", "COST", "ORCL", "ADBE", "MRK", "ABBV", "PEP", "KO"]
    results = []

    def _fetch_one(tk):
        try:
            t = yf.Ticker(tk)
            ud = t.upgrades_downgrades
            if ud is None or ud.empty:
                return []
            # yfinance 등급변경엔 시점별 목표가가 없음 → 현재 컨센서스 평균
            # 목표가(targetMeanPrice)를 종목 단위로 부착(같은 종목 행은 동일값).
            tp = None
            try:
                inf = t.info or {}
                tp = inf.get("targetMeanPrice")
            except Exception:
                pass
            # KR 탭과 동일 7일 윈도(사용자 2026-06-11) — 과거분 제외.
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            items = []
            for idx, row in ud.head(15).iterrows():
                d = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
                if d < cutoff:
                    continue
                items.append({
                    "symbol": tk,
                    "firm": row.get("Firm", ""),
                    "to_grade": row.get("ToGrade", ""),
                    "from_grade": row.get("FromGrade", ""),
                    "target": tp,
                    "date": d,
                })
            # 종목당 최대 3건 — 일주일치 전체에서 최신순 3건, 초과분은
            # 기록하지 않음(사용자 2026-06-11 룰 확정). yfinance 정렬에
            # 기대지 않고 명시 정렬.
            items.sort(key=lambda x: x.get("date", ""), reverse=True)
            return items[:3]
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


def _fetch_macro_safe() -> dict:
    """Macro snapshot wrapper — never raises (page must render without it)."""
    try:
        from bot.macro_snapshot import fetch_macro_snapshot
        return fetch_macro_snapshot()
    except Exception as exc:
        log.warning("market_overview: macro snapshot failed: %s", exc)
        return {}


def _cache_ts(p: Path) -> str:
    """캐시 파일 mtime → 'YYYY-MM-DD HH:MM' (VM 로컬=KST). 부재 시 ''."""
    try:
        if p.exists():
            return datetime.fromtimestamp(p.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M")
    except Exception:
        pass
    return ""


def _widget_data_ts() -> dict:
    """위젯별 '실제 적용' 데이터 시각 — 각 fetch 의 캐시 파일 mtime
    (사용자 2026-06-11: 업종등락처럼 'ts · 소스' 를 실적/리서치에도).
    fetch_all_market_data 의 futures 가 resolve 된 '뒤' 호출 — refetch 가
    일어났으면 mtime 이 방금 시각으로 갱신돼 있음."""
    today = date.today().isoformat()
    return {
        "earn_us": _cache_ts(_CACHE_DIR / "finnhub" / f"earnings_{today}.json"),
        "earn_kr": _cache_ts(_CACHE_DIR / "finnhub" / f"earnings_kr_{today}.json"),
        "res_kr": _cache_ts(_CACHE_DIR / "research" / f"kr_{today}.json"),
        "res_us": _cache_ts(_CACHE_DIR / "research" / f"us_{today}.json"),
    }


def fetch_all_market_data() -> dict[str, Any]:
    """Fetch everything needed for market.html."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        snap_fut = pool.submit(fetch_market_snapshot)
        # 다가오는 실적 윈도 최대화 (사용자 2026-06-10 '최대한 가져오기'):
        # 미국 14→60일(Finnhub 가 확정 발표일 ~4-8주 채움, 표는 100행 cap),
        # 한국 90일(DART IR + yfinance) 유지.
        earn_fut = pool.submit(fetch_earnings_calendar, 60)
        earn_kr_fut = pool.submit(fetch_earnings_calendar_kr, 90)
        # limit 은 7일치 전부 커버용 상한(사용자 2026-06-11 "limit 80?" —
        # 한국 종목 리포트는 주당 200+ 가능 → 넉넉히).
        kr_fut = pool.submit(fetch_recent_research_kr, 300)
        kr_ind_fut = pool.submit(fetch_recent_research_kr_industry, 150)
        us_fut = pool.submit(fetch_recent_research_us, 80)
        macro_fut = pool.submit(_fetch_macro_safe)
        sector_fut = pool.submit(_fetch_sector_movers_safe)
        us_sector_fut = pool.submit(_fetch_us_sector_movers_safe)
        deposit_fut = pool.submit(_fetch_deposit_safe)

        # 실적 병합 — 한국(yfinance) 먼저, 미국(Finnhub) 다음. 각 그룹 날짜순.
        # 사용자 정책: 한국이 되면 한국을 앞으로.
        _kr_e = sorted(earn_kr_fut.result() or [], key=lambda e: e.get("date", ""))
        _us_e = sorted(earn_fut.result() or [], key=lambda e: e.get("date", ""))
        earnings = _kr_e + _us_e

        return {
            "snapshot": snap_fut.result(),
            "earnings": earnings,
            "research_kr": kr_fut.result(),
            "research_kr_industry": kr_ind_fut.result(),
            "research_us": us_fut.result(),
            "macro": macro_fut.result(),
            "sector_movers": sector_fut.result(),
            "us_sector_movers": us_sector_fut.result(),
            "deposit": deposit_fut.result(),
            # futures resolve 후 → refetch 분 mtime 반영된 '실제 적용' 시각
            "widget_ts": _widget_data_ts(),
        }


def _fetch_sector_movers_safe() -> dict:
    try:
        from bot.naver_sector_client import fetch_sector_movers
        return fetch_sector_movers(top_n=10)
    except Exception as exc:
        log.warning("sector movers fetch error: %s", exc)
        return {"up": [], "down": [], "ts": ""}


def _fetch_us_sector_movers_safe() -> dict:
    """미국 업종 등락 — 메인 위젯은 **우리 L3 ~48 업종 단위**(사용자 2026-06-10
    'L3 버전은 메인, Finviz 세밀 144는 개별 페이지'). Finviz 144 를 L3 버킷
    으로 묶어 평균. 세부 전체 144는 /usindustry 페이지(top_movers→Finviz)."""
    try:
        from bot.finviz_client import top_l3_movers
        return top_l3_movers(top_n=10)
    except Exception as exc:
        log.warning("us sector movers fetch error: %s", exc)
        return {"up": [], "down": [], "ts": ""}


def _fetch_deposit_safe() -> dict:
    try:
        from bot.naver_sector_client import fetch_deposit
        return fetch_deposit()
    except Exception as exc:
        log.warning("deposit fetch error: %s", exc)
        return {}
