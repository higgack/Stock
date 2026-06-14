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
# 스냅샷 — download 일봉 배치는 1분(사용자 2026-06-14 '데이터 주기 1분', 1콜/분
# 이라 가벼움), 라이브 fast_info 는 별도 10분 throttle(_LIVE_TTL — rate-limit
# 회피). YF_PAUSE 시 둘 다 skip. Finviz/Naver 업종 TTL 은 각 클라이언트가 별도
# 보유(안티봇 경계 — 건드리지 말 것).
_CACHE_TTL_SEC = 60  # 1 min

# fast_info(라이브 quote API) 호출 throttle — yahoo 가 fast_info 만 쉽게 rate-limit
# (사용자 2026-06-14 /health: 재개 즉시 재차단, download 는 OK). 홈 스냅샷이 1분마다
# ~20 fast_info 병렬 호출하던 게 주범 → 라이브가는 _LIVE_TTL(10분)마다만 갱신,
# 그 사이엔 download 일봉(1콜) + 직전 라이브 캐시 사용. fast_info 부하 ~10x↓.
# (download 는 아시아 지수 하루 지연 이슈가 있어 fast_info 를 아예 끄진 않고 throttle.)
_LIVE_TTL = 600
_LAST_LIVE: dict = {}
_LAST_LIVE_TS = 0.0

# 배포-인지 캐시 솔트 (사용자 2026-06-12 '대시보드 반영 너무 느려') —
# git reset --hard 배포가 이 모듈을 갱신하면 mtime 이 바뀜 → 솔트 포함
# 캐시 키가 즉시 무효화 → 위젯 로직/universe 변경이 같은 날 day-key
# 캐시(12h TTL)에 막혀 수 시간 안 보이던 클래스(#281 실적 450 확장이
# 12:01 옛 캐시에 가려진 케이스) 영구 차단. 코드 무변경 배포면 mtime
# 그대로 = 캐시 보존(불필요 refetch 0).
try:
    _CODE_SALT = str(int(os.path.getmtime(__file__)))[-6:]
except OSError:
    _CODE_SALT = "0"

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

# fast_info 라이브 화이트리스트 (사용자 2026-06-14 '(A) 스냅샷 라이브 축소') —
# fast_info(quote 엔드포인트, 데이터센터 IP rate-limit 빈발)를 헤드라인 ~15 지수로
# 제한. Asia 지수는 yfinance 일봉이 하루 지연(문서화 버그)이라 fast_info 필수, US/EU
# 주요는 장중 intraday. 나머지(롱테일 환율/원자재/코인/이머징/선물)는 daily download
# 로 충분(24h 시장이거나 종가만 보면 됨) → fast_info 버스트 ~60→~15 로 줄여 rate-
# limit 재트립 빈도 급감. 화이트리스트 밖은 merge 단계에서 daily 봉으로 폴백.
_LIVE_WHITELIST = {
    "^KS11", "^KQ11", "^TWII", "^N225", "000300.SS", "000001.SS",
    "^HSI", "^NSEI", "3033.HK",                       # Asia 지수 (일봉 지연 → live 필수)
    "^GSPC", "^IXIC", "^DJI", "^GDAXI", "^FTSE",      # US/EU 주요 지수
    "USDKRW=X",                                       # 헤드라인 환율
}


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
    # YF_PAUSE(사용자 2026-06-14 '정지게이트 모두 추가') → yfinance skip, 직전 성공
    # 배치를 디스크에서 반환(홈 지수 stale 유지·블랭크 방지). 네이버·FRED 는 무관.
    _bc = _CACHE_DIR / "yf_batch_snapshot.json"
    try:
        from bot.finviz_client import yf_paused
        if yf_paused():
            try:
                return json.loads(_bc.read_text())
            except Exception:
                return {}
    except Exception:
        pass

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
        except Exception as exc:
            try:
                from bot.finviz_client import fast_info_trip, is_rate_limit_error
                if is_rate_limit_error(exc):
                    fast_info_trip("snapshot")
            except Exception:
                pass
        return tk, None

    live: dict[str, tuple[float, float]] = {}
    global _LAST_LIVE, _LAST_LIVE_TS
    try:
        from bot.finviz_client import fast_info_ok
        _fi_ok = fast_info_ok()
    except Exception:
        _fi_ok = True
    # throttle(10분) + 회로차단(쿨다운 중이면 download 만 사용)
    if _fi_ok and time.time() - _LAST_LIVE_TS >= _LIVE_TTL:
        try:
            # 라이브는 헤드라인 화이트리스트만 (사용자 2026-06-14 '(A) 라이브 축소')
            # — fast_info 버스트 ~60→~15. 나머지는 아래 merge 가 daily 봉으로 채움.
            _live_set = [tk for tk in tickers if tk in _LIVE_WHITELIST]
            with ThreadPoolExecutor(max_workers=8) as pool:
                for tk, v in pool.map(_live, _live_set):
                    if v:
                        live[tk] = v
            if live:
                _LAST_LIVE = dict(live)
                _LAST_LIVE_TS = time.time()
        except Exception as exc:
            log.warning("market_overview: fast_info live error: %s", exc)
    else:
        live = dict(_LAST_LIVE)        # throttle 중 — 직전 라이브가 재사용(download 보완)

    # 3) merge — live(오늘) 우선, 없으면 daily 폴백. live 가 글리치(KLAC
    # 클래스: fast_info 분할 미조정 → ±75% 초과 phantom 등락)면 daily 봉
    # 폴백, 그것도 없으면 직전 종가 교체 (교체 우선 정책 2026-06-04).
    try:
        from bot.price_sanity import quote_glitch_gap as _qgg
    except Exception:
        _qgg = None
    for tk in tickers:
        if tk in live:
            cur, prev = live[tk]
            if _qgg and _qgg(cur, prev):
                cur, prev = daily.get(tk, (prev, prev))
            elif _qgg and tk in daily and _qgg(cur, daily[tk][0]):
                # 2차 (KLAC 클래스): live 의 last·prev 가 둘 다 같은 미조정
                # 기준이면 1차가 장님 — 조정 batch 일봉 종가와 교차해
                # ±75% 초과면 batch 쌍으로 교체 (추가 호출 0).
                cur, prev = daily[tk]
        elif tk in daily:
            cur, prev = daily[tk]
        else:
            continue
        chg = cur - prev
        pct = (chg / prev * 100) if prev != 0 else 0.0
        result[tk] = {"close": cur, "prev_close": prev,
                      "change": chg, "pct": pct}
    if result:                       # YF_PAUSE 시 폴백용 직전 배치 디스크 캐시
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _bc.write_text(json.dumps(result, ensure_ascii=False))
        except Exception:
            pass
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
            if age_h < 24:
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
            if age_h < 24:
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
    cache_file = cache_dir / f"earnings_{today.isoformat()}_{_CODE_SALT}.json"
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


def _kr_earnings_universe() -> list[tuple[str, str]]:
    """KR 실적 캘린더 universe — pykrx 시총 상위(KOSPI 300 + KOSDAQ 150)
    동적 산출, 실패 시 하드코딩 33 폴백 (사용자 2026-06-12 '한국 21개가
    최선인가' — 옛 고정 33종목이 병목이었음). 7일 디스크 캐시(KRX 4콜).

    이름은 get_market_price_change(1콜/시장, 종목명 컬럼 포함) — per-ticker
    이름 조회 450콜 회피. KRX creds 부재/pykrx 미설치면 graceful 폴백.
    ⚠️ 커버리지 한계(정직): yfinance .calendar 는 KR 중소형주 대부분 빈값 —
    universe 를 늘려도 '확정 실적일'이 있는 종목만 표에 추가된다."""
    cache_file = _CACHE_DIR / "finnhub" / f"kr_earnings_universe_{_CODE_SALT}.json"
    try:
        if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < 7 * 86400:
            cached = json.loads(cache_file.read_text())
            if isinstance(cached, list) and len(cached) > 50:
                return [tuple(x) for x in cached]
    except Exception:
        pass
    out: list[tuple[str, str]] = []
    try:
        from bot.pykrx_client import krx_login_ready, _quiet_pykrx_logging
        if krx_login_ready():
            _quiet_pykrx_logging()
            from pykrx import stock as _pk
            from datetime import datetime as _dt2, timedelta as _td2
            d = _dt2.now()
            ds = d.strftime("%Y%m%d")
            ds_prev = (d - _td2(days=7)).strftime("%Y%m%d")
            for mkt, suffix, cap in (("KOSPI", ".KS", 300),
                                     ("KOSDAQ", ".KQ", 150)):
                mc = _pk.get_market_cap(ds, market=mkt)
                names_df = _pk.get_market_price_change(ds_prev, ds, market=mkt)
                names = (names_df["종목명"].to_dict()
                         if names_df is not None and "종목명" in names_df else {})
                top = mc.sort_values("시가총액", ascending=False).head(cap)
                for code in top.index:
                    nm = str(names.get(code) or code)
                    out.append((f"{code}{suffix}", nm))
    except Exception as exc:
        log.warning("kr earnings universe via pykrx failed: %s", exc)
        out = []
    if len(out) > 50:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(out, ensure_ascii=False))
        except Exception:
            pass
        return out
    return list(_KR_EARNINGS_UNIVERSE)


def _earning_row_from_cal(cal, tk: str, name: str, today: date,
                          cutoff: date) -> Optional[dict]:
    """yfinance .calendar dict → 예정 실적 행 또는 None. 순수(테스트 가능).

    KR + JP/TW/CN/HK 가 공유하는 파싱·윈도 필터. 추정치(EPS/매출)는
    yfinance 가 비미국 종목에 종종 None → 그대로 둠('—' 표시는 렌더층)."""
    if not isinstance(cal, dict):
        return None
    eds = cal.get("Earnings Date") or []
    if not eds:
        return None
    d0 = eds[0]
    ds = d0.strftime("%Y-%m-%d") if hasattr(d0, "strftime") else str(d0)[:10]
    try:
        dd = datetime.strptime(ds, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    if dd < today or dd > cutoff:
        return None
    return {
        "symbol": tk, "name": name, "date": ds, "hour": "",
        "eps_estimate": cal.get("Earnings Average"),
        "revenue_estimate": cal.get("Revenue Average"),
        "quarter": None, "year": None,
    }


def fetch_earnings_calendar_kr(days_ahead: int = 90) -> list[dict]:
    """KR 종목 예정 실적일 (yfinance .calendar, 무료). 12h 캐시.

    KR 실적일은 미국보다 드물게 분포 → 윈도 90일로 넓게. universe 는
    pykrx 시총 상위 450(_kr_earnings_universe, 폴백 33). 추정치(EPS/매출)는
    yfinance 가 KR 에 대해 종종 None → 그대로 '—' 표시(정직)."""
    cache_dir = _CACHE_DIR / "finnhub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"earnings_kr_{today.isoformat()}_{_CODE_SALT}.json"
    if cache_file.exists():
        try:
            # 12h→6h (사용자 2026-06-12 '느려') — US 와 동일, 일 4회 갱신.
            # 450 yf .calendar threadpool ~1분, 무료.
            if (time.time() - cache_file.stat().st_mtime) / 3600 < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    cutoff = today + timedelta(days=days_ahead)

    def _one(item):
        tk, name = item
        try:
            return _earning_row_from_cal(yf.Ticker(tk).calendar, tk, name,
                                         today, cutoff)
        except Exception:
            return None

    results: list[dict] = []
    try:
        from bot.finviz_client import yf_paused
        _paused = yf_paused()
    except Exception:
        _paused = False
    if _paused:                         # YF_PAUSE → .calendar 스캔 skip, 직전 캐시
        try:
            for p in sorted(cache_dir.glob("earnings_kr_*.json"),
                            key=lambda q: q.stat().st_mtime, reverse=True):
                if p == cache_file:
                    continue
                prior = json.loads(p.read_text())
                if prior:
                    return prior
        except Exception:
            pass
        return []
    universe = _kr_earnings_universe()
    with ThreadPoolExecutor(max_workers=12) as pool:
        for r in pool.map(_one, universe):
            if r:
                results.append(r)
    results.sort(key=lambda x: x.get("date", ""))
    try:
        cache_file.write_text(json.dumps(results, ensure_ascii=False))
    except Exception:
        pass
    return results


# JP/TW/CN/HK 실적 캘린더 — KR 패턴 일반화 (사용자 2026-06-13 '실적빌드 다국가').
# 유니버스 = bot.market 의 산업 peer 맵(검증된 주요종목 ~50-100/시장,
# 신고저 스캔과 동일 소스). yfinance .calendar(무료·무키)로 확정 실적일만.
_INTL_EARN_PEERS = {
    "JP": "_JP_INDUSTRY_PEERS",
    "TW": "_TW_INDUSTRY_PEERS",
    "CN_A": "_CN_A_INDUSTRY_PEERS",
    "HK": "_HK_INDUSTRY_PEERS",
}


def _intl_earnings_universe(market: str) -> list[tuple[str, str]]:
    """peer 맵의 unique 티커(주요종목). 이름은 ticker 기본(맵에 명칭 없음)."""
    attr = _INTL_EARN_PEERS.get(market)
    if not attr:
        return []
    try:
        from bot import market as _mkt
        peers = getattr(_mkt, attr, {}) or {}
    except Exception as exc:
        log.warning("intl earnings universe error (%s): %s", market, exc)
        return []
    seen: set = set()
    out: list[tuple[str, str]] = []
    for vals in peers.values():
        for x in (vals if isinstance(vals, (list, tuple)) else [vals]):
            t = str(x[0] if isinstance(x, (list, tuple)) else x).strip()
            if t and t not in seen:
                seen.add(t)
                out.append((t, t))
    return out


def fetch_earnings_calendar_intl(market: str, days_ahead: int = 90,
                                 cache_only: bool = False) -> list[dict]:
    """JP/TW/CN/HK 예정 실적일 (yfinance .calendar, 무료·무키). 6h 캐시.

    KR(fetch_earnings_calendar_kr) 패턴 일반화 — 산업 peer 맵 주요종목
    universe(시장당 ~50-100). 추정치(EPS/매출)는 yfinance 가 비미국에
    종종 None → '—'(정직). 미지원 시장/유니버스 부재면 빈 리스트.
    ⚠️ 커버리지 한계: yfinance .calendar 는 비미국 종목 상당수 빈값 —
    '확정 실적일' 있는 종목만 표에 추가된다(universe 크기와 무관).

    cache_only=True (실적 캘린더 페이지 on-request 경로) — **동기 스캔 금지**:
    캐시 있으면(나이 무관) 반환, 없으면 [] (페이지 hang 방지). 캐시는
    market.html 백그라운드 갱신 + 아침 pre-warm 이 데움."""
    if market not in _INTL_EARN_PEERS:
        return []
    cache_dir = _CACHE_DIR / "finnhub"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"earnings_{market}_{today.isoformat()}_{_CODE_SALT}.json"
    if cache_file.exists():
        try:
            if cache_only or (time.time() - cache_file.stat().st_mtime) / 3600 < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass
    if cache_only:
        return []                       # on-request 경로 — 스캔 안 함
    try:
        from bot.finviz_client import yf_paused
    except Exception:
        yf_paused = lambda: False
    if yf_paused():                     # YF_PAUSE → .calendar 스캔 skip, 직전 캐시
        try:
            for p in sorted(cache_dir.glob(f"earnings_{market}_*.json"),
                            key=lambda q: q.stat().st_mtime, reverse=True):
                prior = json.loads(p.read_text())
                if prior:
                    return prior
        except Exception:
            pass
        return []
    universe = _intl_earnings_universe(market)
    if not universe:
        return []

    cutoff = today + timedelta(days=days_ahead)

    def _one(item):
        tk, name = item
        try:
            return _earning_row_from_cal(yf.Ticker(tk).calendar, tk, name,
                                         today, cutoff)
        except Exception:
            return None

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for r in pool.map(_one, universe):
            if r:
                results.append(r)
    results.sort(key=lambda x: x.get("date", ""))
    # 한글 종목명 (사용자 2026-06-13 '일본부터 티커말고 번역 한국종목명') — peer
    # 맵엔 이름 없어 name==ticker. 확정 실적일 있는 결과(희소)만 yfinance
    # longName → chart_translate(Flash·영구캐시) 번역. 6h 캐시에 이름까지 저장.
    # graceful — 실패 시 ticker 유지(렌더가 '회사명 없으면 티커' 폴백).
    if results:
        # 한글명 3-레이어 (사용자 2026-06-14 'TW·HK 실적도 한국어'):
        # (1) 네이버 worldstock 이름맵(JP/CN/HK — HK 는 5↔4자리 zfill 정규화)
        # (2) TW = TWSE 中文명 → 한국어 번역(네이버 worldstock 미지원)
        # (3) 잔여 미스 = yfinance longName → 번역. 전부 영구/7d 캐시라 ₩~0.
        try:
            from bot.naver_ranking_client import world_name_map
            nmap = world_name_map(market)
            if nmap:
                norm = {}
                if market == "HK":     # 5자리↔4자리 zfill 정수 정규화
                    for k, v in nmap.items():
                        c = str(k).split(".")[0]
                        if c.isdigit():
                            norm.setdefault(str(int(c)), v)
                for r in results:
                    sym = r["symbol"]
                    nm = nmap.get(sym)
                    if not nm and market == "HK":
                        c = str(sym).split(".")[0]
                        if c.isdigit():
                            nm = norm.get(str(int(c)))
                    if nm:
                        r["name"] = nm
        except Exception as exc:
            log.warning("intl earnings 네이버 이름 (%s): %s", market, exc)
        if market == "TW":             # TWSE 中文명 → 한국어 번역(네이버 미지원)
            try:
                from bot.chart_translate import translate_titles_kr
                from bot.twse_client import fetch_stock_day_all
                tw_names = {f"{s.get('code')}.TW": s.get("name")
                            for s in fetch_stock_day_all().get("rows", [])
                            if s.get("code") and s.get("name")}
                nat = {r["symbol"]: tw_names[r["symbol"]] for r in results
                       if tw_names.get(r["symbol"])
                       and (not r.get("name") or r["name"] == r["symbol"])}
                uniq = sorted({v for v in nat.values() if v})
                kr = translate_titles_kr(uniq) if uniq else {}
                for r in results:
                    nm = nat.get(r["symbol"])
                    if nm and kr.get(nm):
                        r["name"] = kr[nm]
            except Exception as exc:
                log.warning("TW earnings 中文→한글: %s", exc)
        miss = [r for r in results
                if not r.get("name") or r["name"] == r["symbol"]]
        if miss:
            try:
                from bot.chart_translate import translate_titles_kr
                from bot.finviz_client import _fetch_display_names
                en = _fetch_display_names([r["symbol"] for r in miss])
                uniq = sorted({n for n in en.values() if n})
                kr = translate_titles_kr(uniq) if uniq else {}
                for r in miss:
                    e = en.get(r["symbol"], "")
                    if e:
                        r["name"] = kr.get(e) or e
            except Exception as exc:
                log.warning("intl earnings 한글명 폴백 (%s): %s", market, exc)
    # 결과 빈 경우(yfinance .calendar 일시 장애·배포 _CODE_SALT 리셋) — 최근
    # 비어있지 않은 캐시로 폴백(사용자 2026-06-14 '다가오는 실적 미국만 나옴' —
    # intl 휘발 방지). 빈 결과는 캐시에 안 써 prior 를 가리지 않음.
    if not results:
        try:
            for p in sorted(cache_dir.glob(f"earnings_{market}_*.json"),
                            key=lambda q: q.stat().st_mtime, reverse=True):
                if p == cache_file:
                    continue
                prior = json.loads(p.read_text())
                if prior:
                    return prior
        except Exception:
            pass
        return results
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


def fetch_recent_research_kr_strategy(limit: int = 80) -> list[dict]:
    """Fetch latest KR 투자전략(투자정보) 리서치 리포트 — 일주일치(Naver).

    종목·산업 리포트와 동일 7일 윈도. Returns [{broker, title, date, link}]
    (분류·목표가 없음). 사용자 2026-06-12 '네이버 투자전략 → 한국 전략 탭'."""
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"kr_strategy_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < (10 / 60):  # 10분 — naver 1h 갱신을 빠르게 반영
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    results: list[dict] = []
    try:
        from bot.naver_research_client import fetch_recent_research_strategy
        results = fetch_recent_research_strategy(limit=limit, days_back=7,
                                                 max_pages=12)
    except Exception as exc:
        log.warning("naver research strategy fetch error: %s", exc)

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


def fetch_recent_research_intl(market: str, limit: int = 25) -> list[dict]:
    """JP/TW/CN/HK 최근 등급변경 — US 패턴(yfinance upgrades_downgrades) 일반화
    (사용자 2026-06-13 '다른나라들도 같은 조건 리서치액션, 보고 판단'). 산업
    peer 주요종목 universe(부하 bound 60). 한글명 번역(결과만). 6h 캐시.

    ⚠️ 커버리지 한계: yfinance 해외 애널리스트 등급변경 데이터가 얇아 결과가
    희소(빈 시장 가능)할 수 있음 — 이 trial 의 '판단' 대상. 그래서 윈도는 US
    7일 대신 30일로 넓힘. graceful — 실패/없음 시 빈 리스트."""
    if market not in _INTL_EARN_PEERS:
        return []
    cache_dir = _CACHE_DIR / "research"
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    cache_file = cache_dir / f"intl_{market}_{today.isoformat()}.json"
    if cache_file.exists():
        try:
            if (time.time() - cache_file.stat().st_mtime) / 3600 < 6:
                return json.loads(cache_file.read_text())
        except Exception:
            pass
    universe = _intl_earnings_universe(market)[:60]   # 부하 bound
    if not universe:
        return []
    cutoff = (today - timedelta(days=30)).isoformat()

    def _one(item):
        tk = item[0]
        try:
            ud = yf.Ticker(tk).upgrades_downgrades
            if ud is None or ud.empty:
                return []
            items = []
            for idx, row in ud.head(10).iterrows():
                d = str(idx.date()) if hasattr(idx, "date") else str(idx)[:10]
                if d < cutoff:
                    continue
                items.append({"symbol": tk, "firm": row.get("Firm", ""),
                              "to_grade": row.get("ToGrade", ""),
                              "from_grade": row.get("FromGrade", ""),
                              "target": None, "date": d})
            items.sort(key=lambda x: x["date"], reverse=True)
            return items[:3]
        except Exception:
            return []

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_one, universe):
            results.extend(r)
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    results = results[:limit]
    # 한글명 — 결과만(희소) yfinance longName → chart_translate(Flash·영구캐시).
    if results:
        try:
            from bot.chart_translate import translate_titles_kr
            from bot.finviz_client import _fetch_display_names
            en = _fetch_display_names([r["symbol"] for r in results])
            uniq = sorted({n for n in en.values() if n})
            kr = translate_titles_kr(uniq) if uniq else {}
            for r in results:
                e = en.get(r["symbol"], "")
                r["name"] = (kr.get(e) or e) if e else r["symbol"]
        except Exception as exc:
            log.warning("intl research 한글명 (%s): %s", market, exc)
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
    """캐시 파일 mtime → 'YYYY-MM-DD HH:MM' **명시적 KST** (서버 로컬타임
    의존 제거 — 사용자 정책: 모든 표기 한국시간). 부재 시 ''."""
    try:
        if p.exists():
            from datetime import timezone as _tz
            kst = _tz(timedelta(hours=9))
            return datetime.fromtimestamp(p.stat().st_mtime, tz=kst).strftime(
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
        # JP/TW/CN/HK 실적 — KR 패턴 일반화(사용자 2026-06-13 '실적빌드 다국가').
        # 산업 peer 맵 주요종목 universe, yfinance .calendar 6h 캐시(소스 TTL).
        earn_jp_fut = pool.submit(fetch_earnings_calendar_intl, "JP", 90)
        earn_tw_fut = pool.submit(fetch_earnings_calendar_intl, "TW", 90)
        earn_cn_fut = pool.submit(fetch_earnings_calendar_intl, "CN_A", 90)
        earn_hk_fut = pool.submit(fetch_earnings_calendar_intl, "HK", 90)
        # limit 은 7일치 전부 커버용 상한(사용자 2026-06-11 "limit 80?" —
        # 한국 종목 리포트는 주당 200+ 가능 → 넉넉히).
        kr_fut = pool.submit(fetch_recent_research_kr, 300)
        kr_ind_fut = pool.submit(fetch_recent_research_kr_industry, 150)
        kr_strat_fut = pool.submit(fetch_recent_research_kr_strategy, 150)
        us_fut = pool.submit(fetch_recent_research_us, 80)
        # JP/TW/CN/HK 리서치 액션 — yfinance 등급변경(사용자 2026-06-13 '보고
        # 판단'). 6h 캐시·peer 60 bound. 커버리지 얇으면 빈 결과(trial 판단용).
        res_jp_fut = pool.submit(fetch_recent_research_intl, "JP", 25)
        res_tw_fut = pool.submit(fetch_recent_research_intl, "TW", 25)
        res_cn_fut = pool.submit(fetch_recent_research_intl, "CN_A", 25)
        res_hk_fut = pool.submit(fetch_recent_research_intl, "HK", 25)
        macro_fut = pool.submit(_fetch_macro_safe)
        sector_fut = pool.submit(_fetch_sector_movers_safe)
        us_sector_fut = pool.submit(_fetch_us_sector_movers_safe)
        # JP/TW/CN/HK 업종 등락 — 섹터 ETF 합성(사용자 2026-06-13 Phase 1)
        jp_sector_fut = pool.submit(_fetch_etf_sector_safe, "JP")
        tw_sector_fut = pool.submit(_fetch_tw_sector_safe)   # TWSE 類股 우선
        cn_sector_fut = pool.submit(_fetch_etf_sector_safe, "CN_A")
        hk_sector_fut = pool.submit(_fetch_etf_sector_safe, "HK")
        deposit_fut = pool.submit(_fetch_deposit_safe)

        # 실적 병합 — 한국(yfinance) 먼저, 미국(Finnhub) 다음, JP/TW/CN/HK.
        # 각 그룹 날짜순. 대시보드가 접미사로 재필터해 탭 분리(병합 순서는
        # cosmetic). 사용자 정책: 한국이 되면 한국을 앞으로.
        _key = lambda e: e.get("date", "")
        _kr_e = sorted(earn_kr_fut.result() or [], key=_key)
        _us_e = sorted(earn_fut.result() or [], key=_key)
        _jp_e = sorted(earn_jp_fut.result() or [], key=_key)
        _tw_e = sorted(earn_tw_fut.result() or [], key=_key)
        _cn_e = sorted(earn_cn_fut.result() or [], key=_key)
        _hk_e = sorted(earn_hk_fut.result() or [], key=_key)
        earnings = _kr_e + _us_e + _jp_e + _tw_e + _cn_e + _hk_e

        return {
            "snapshot": snap_fut.result(),
            "earnings": earnings,
            "research_kr": kr_fut.result(),
            "research_kr_industry": kr_ind_fut.result(),
            "research_kr_strategy": kr_strat_fut.result(),
            "research_us": us_fut.result(),
            "research_jp": res_jp_fut.result(),
            "research_tw": res_tw_fut.result(),
            "research_cn": res_cn_fut.result(),
            "research_hk": res_hk_fut.result(),
            "macro": macro_fut.result(),
            "sector_movers": sector_fut.result(),
            "us_sector_movers": us_sector_fut.result(),
            "jp_sector_movers": jp_sector_fut.result(),
            "tw_sector_movers": tw_sector_fut.result(),
            "cn_sector_movers": cn_sector_fut.result(),
            "hk_sector_movers": hk_sector_fut.result(),
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


def _fetch_etf_sector_safe(market: str) -> dict:
    """JP/CN_A/HK 업종 등락 — **네이버 업종 우선**(시총가중 등락, 사용자 2026-06-14
    '업종등락 네이버'), 빈/실패 시 섹터 ETF 합성(yfinance) 폴백. graceful."""
    if market in ("JP", "CN_A", "HK"):
        try:
            from bot.naver_ranking_client import fetch_intl_sector_movers_naver
            nv = fetch_intl_sector_movers_naver(market, top_n=10)
            if nv.get("up") or nv.get("down"):
                return nv
        except Exception as exc:
            log.warning("naver sector movers (%s) → ETF 폴백: %s", market, exc)
    try:
        from bot.etf_sector_client import fetch_sector_movers_etf
        return fetch_sector_movers_etf(market, top_n=10)
    except Exception as exc:
        log.warning("etf sector movers fetch error (%s): %s", market, exc)
        return {"up": [], "down": [], "ts": "", "source": ""}


def _fetch_tw_sector_safe() -> dict:
    """TW 업종 등락 — TWSE 類股 지수(~30 업종, 풍부) 우선, 실패 시 ETF 합성
    폴백(사용자 2026-06-13 Phase 2, TWSE 200 검증)."""
    try:
        from bot.twse_client import fetch_tw_sector_movers
        r = fetch_tw_sector_movers(top_n=10)
        if r.get("up") or r.get("down"):
            return r
    except Exception as exc:
        log.warning("twse sector fetch error: %s", exc)
    return _fetch_etf_sector_safe("TW")


def _fetch_deposit_safe() -> dict:
    try:
        from bot.naver_sector_client import fetch_deposit
        return fetch_deposit()
    except Exception as exc:
        log.warning("deposit fetch error: %s", exc)
        return {}
