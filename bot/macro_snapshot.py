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
_CACHE_TTL_SEC = 60  # 1min (사용자 2026-06-14 — 글로벌 스냅샷과 동일 1분 주기)

# ── Indicator definitions ───────────────────────────────────────────
# (key, label, unit, source, source_id, decimals)
#   source: "ecos" | "fred" | "fred_yoy" | "yf"
DOMESTIC = [
    ("kr_rate", "한국 기준금리", "%", "ecos", "base_rate", 2),
    ("kr_3y", "국고채 3년", "%", "ecos", "kr3y", 2),
    ("kr_10y", "국고채 10년", "%", "ecos", "kr10y", 2),
    ("kr_cpi", "한국 CPI", "", "ecos", "cpi_idx", 2),
    ("usdkrw", "USD/KRW", "", "yf", "USDKRW=X", 1),
    ("kr_ca", "경상수지", "억$", "ecos", "current_account", 0),
    ("kr_export", "한국 수출", "억$", "ecos", "export_amt", 0),
]

# 사용자 2026-06-14 재정렬: FFR·달러인덱스 삭제 / 원자재 = WTI·브렌트·천연가스·
# 금·은·백금·구리·알루미늄·[니켈]·옥수수·대두·소맥·돈육·커피·면화 / 코인은
# 비트·이더·솔만(BNB·도지·리플 삭제). 니켈은 yfinance 무티커(네이버 metals 전용)
# 라 이름 확정 후 추가.
GLOBAL = [
    ("us_ffr", "미국 FFR", "%", "fred", "FEDFUNDS", 2),   # 사용자 2026-06-14 맨앞 재추가
    ("us_2y", "미국 2Y", "%", "fred", "DGS2", 2),
    ("us_10y", "미국 10Y", "%", "fred", "DGS10", 2),
    ("us_cpi", "미국 CPI", "", "fred", "CPIAUCSL", 2),
    ("us_unemploy", "미국 실업률", "%", "fred", "UNRATE", 1),
    ("us_ism", "미국 ISM PMI", "", "fred", "NAPM", 1),
    ("us_gdp", "미국 GDP", "%", "fred", "A191RL1Q225SBEA", 1),
    ("sp500", "S&P 500", "", "yf", "^GSPC", 2),
    ("nasdaq", "NASDAQ", "", "yf", "^IXIC", 2),
    ("vix", "VIX", "", "yf", "^VIX", 2),
    ("wti", "WTI", "$", "yf", "CL=F", 1),
    ("brent", "브렌트유", "$", "yf", "BZ=F", 1),
    ("natgas", "천연가스", "$", "yf", "NG=F", 2),
    ("gold", "금", "$", "yf", "GC=F", 0),
    ("silver", "은", "$", "yf", "SI=F", 2),
    ("platinum", "백금", "$", "yf", "PL=F", 0),
    ("copper", "구리", "$", "yf", "HG=F", 2),
    ("aluminum", "알루미늄", "$", "yf", "ALI=F", 2),
    ("nickel", "니켈", "$", "yf", "NI=F", 0),  # 사용자 2026-06-14 (네이버 NI, yf 무차트)
    ("corn", "옥수수", "$", "yf", "ZC=F", 0),
    ("soybean", "대두", "$", "yf", "ZS=F", 0),
    ("wheat", "소맥", "$", "yf", "ZW=F", 0),
    ("hogs", "돈육", "$", "yf", "HE=F", 2),
    ("coffee", "커피", "$", "yf", "KC=F", 2),
    ("cotton", "면화", "$", "yf", "CT=F", 2),
    ("btc", "비트코인", "$", "yf", "BTC-USD", 0),
    ("eth", "이더리움", "$", "yf", "ETH-USD", 0),
]

# 지표 정의가 바뀌면(예: 은·알루미늄 추가) 디스크 캐시를 즉시 무효화하기
# 위한 버전 해시. 키/심볼 목록이 달라지면 2h TTL 과 무관하게 재빌드 →
# 새 지표가 stale 캐시에 묻혀 안 보이던 문제 방지.
import hashlib as _hashlib  # noqa: E402
# salt 'spark1mo' = 카드 스파크라인을 1개월 일봉 + spark_dir 구조로 변경
# (2026-06-10). 옛 12개월 spark 캐시를 즉시 무효화.
# 1개월 카드 중 변화를 % 가 아닌 절대값으로 표시할 sid (사용자 2026-06-10):
# 환율(USD/KRW)만 절대값이 직관적(₩2.11). 달러인덱스는 % 로 표시(사용자 2026-06-10).
_ABS_CHANGE_SIDS = {"USDKRW=X"}

_DEFS_VERSION = _hashlib.md5(
    (repr([(k, sid) for k, _, _, _, sid, _ in (DOMESTIC + GLOBAL)])
     + "|spark1mo_span_pct_absfx_dxypct").encode()
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
    """Batch monthly close (13mo) for all tickers → {ticker: [floats]}.

    ⚠️ 차트 전용 1h 캐시 (사용자 2026-06-14 '매크로 차트 또 날아감'): 값은
    네이버(1분)인데 차트 download 를 매 1분 snapshot 재생성마다 돌리면 22종목
    yf.download 가 yahoo IP rate-limit 을 유발 → 차트가 throttle 로 빔. 12개월
    월간 라인은 1h 묵어도 무해 → download 트래픽 60배↓. 실패 시 스테일 폴백."""
    out: dict[str, list[float]] = {}
    if not tickers:
        return out
    try:
        from bot.finviz_client import _cache_write, _cached
    except Exception:
        _cache_write = _cached = None
    if _cached:
        c = _cached("macro_yf_monthly.json", ttl=3600)
        if isinstance(c, dict) and c:
            return c
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
            return _cached("macro_yf_monthly.json", ttl=86400) or out if _cached else out
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
        return _cached("macro_yf_monthly.json", ttl=86400) or out if _cached else out
    if out and _cache_write:
        try:
            _cache_write("macro_yf_monthly.json", out)
        except Exception:
            pass
    return out


def _yf_daily_1mo_batch(tickers: list[str]) -> dict[str, list[float]]:
    """Batch ~1개월 일봉 종가 → {ticker: [floats]} (카드 스파크라인 라인용).

    사용자 2026-06-10: 카드 미니 차트의 '라인'도 12개월이 아니라 최근 1개월
    이어야 색(1개월 방향)과 일치. 큰 차트(spark_cache)는 월간 그대로 유지."""
    out: dict[str, list[float]] = {}
    if not tickers:
        return out
    # 차트 전용 1h 캐시 (위 _yf_monthly_batch 와 동일 사유 — 1분 download 폭주 차단).
    try:
        from bot.finviz_client import _cache_write, _cached
    except Exception:
        _cache_write = _cached = None
    if _cached:
        c = _cached("macro_yf_daily1mo.json", ttl=3600)
        if isinstance(c, dict) and c:
            return c
    import yfinance as yf
    try:
        df = yf.download(
            " ".join(tickers), period="1mo", interval="1d",
            progress=False, threads=True, timeout=20,
        )
        if df is not None and not df.empty:
            for tk in tickers:
                try:
                    closes = (df["Close"][tk] if len(tickers) > 1
                              else df["Close"]).dropna()
                    vals = [round(float(c), 4) for c in closes.tolist()]
                    if vals:
                        out[tk] = vals
                except Exception:
                    continue
    except Exception as exc:
        log.warning("macro: yf daily 1mo batch failed: %s", exc)
        return _cached("macro_yf_daily1mo.json", ttl=86400) or out if _cached else out
    if out and _cache_write:
        try:
            _cache_write("macro_yf_daily1mo.json", out)
        except Exception:
            pass
    return out


def _spark_dir(series: list, baseline_idx: int) -> int:
    """1개월 방향 +1/-1/0 — series[-1] vs series[baseline_idx], 1% 데드밴드."""
    s = [v for v in (series or []) if v is not None]
    if len(s) < 2:
        return 0
    try:
        delta = s[-1] - s[baseline_idx]
    except IndexError:
        return 0
    rng = (max(s) - min(s)) or 1.0
    if abs(delta) < rng * 0.01:
        return 0
    return 1 if delta > 0 else -1


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


# ── 네이버 현재값 매핑 (사용자 2026-06-14 '값 네이버 + 차트 유지') ──────────
# Macro 가격 카드의 '현재값'을 네이버에서(=카드 안 사라짐, 야후 멈춤 영향 0).
# 차트(스파크라인)는 네이버가 시계열 미제공 → yfinance history 그대로 유지.
# 미매핑 항목(백금·곡물·돈육·커피·면화)은 네이버 코드 미확정 → yf 값 유지(폴백).
# kind: idx=worldstock/index · com=marketindex · coin=업비트 · fx=marketindex/exchange
_MACRO_NAVER = {
    "^GSPC": ("idx", ".INX"), "^IXIC": ("idx", ".IXIC"), "^VIX": ("idx", ".VIX"),
    "CL=F": ("com", "CL"), "BZ=F": ("com", "BRN"), "NG=F": ("com", "NG"),
    "GC=F": ("com", "GC"), "SI=F": ("com", "SI"), "HG=F": ("com", "HG"),
    "ALI=F": ("com", "AA"),
    # VM probe 2026-06-14 확정 — 백금·곡물·돈육·커피·면화·니켈 (marketindex metals/agri)
    "PL=F": ("com", "PL"), "ZC=F": ("com", "ZC"), "ZS=F": ("com", "ZS"),
    "ZW=F": ("com", "ZW"), "HE=F": ("com", "HE"), "KC=F": ("com", "KC"),
    "CT=F": ("com", "CT"), "NI=F": ("com", "NI"),
    "BTC-USD": ("coin", "BTC"), "ETH-USD": ("coin", "ETH"), "SOL-USD": ("coin", "SOL"),
    "USDKRW=X": ("fx", "FX_USDKRW"),
}


def _fetch_macro_naver_values(sids: list) -> dict:
    """{sid: {value, change}} — 매핑된 sid 의 현재값만 네이버에서. 소스별 1회 fetch,
    실패/미반환 sid 는 제외(호출부가 yf 값으로 폴백). 순수-ish(네트워크는 네이버 모듈)."""
    need = [(s, _MACRO_NAVER[s]) for s in sids if s in _MACRO_NAVER]
    if not need:
        return {}
    try:
        from bot import naver_marketindex as _nm
    except Exception:
        return {}
    idx_codes = tuple(c for _, (k, c) in need if k == "idx")
    pools = {
        "idx": (_nm.fetch_world_indices(idx_codes) if idx_codes else {}),
        "com": (_nm.fetch_commodities() if any(k == "com" for _, (k, _) in need) else {}),
        "coin": (_nm.fetch_naver_coins() if any(k == "coin" for _, (k, _) in need) else {}),
        "fx": (_nm.fetch_kr_fx() if any(k == "fx" for _, (k, _) in need) else {}),
    }
    out: dict = {}
    for sid, (k, code) in need:
        rec = (pools.get(k) or {}).get(code)
        if rec and rec.get("close") is not None:
            out[sid] = {"value": rec["close"], "change": rec.get("change", 0.0)}
    return out


# ── Main ────────────────────────────────────────────────────────────
def fetch_macro_snapshot() -> dict[str, Any]:
    """Assemble the full macro snapshot. 5min disk cache (글로벌 스냅샷과
    동일 주기) — FRED/ECOS 하위 시계열은 각자 12h 캐시(공식 통계라 일·월
    단위 갱신). _periodic_market_refresh 가 5분마다 market.html 재생성.

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

    # Collect yf tickers once. 값은 전체 yf sid(원자재 포함, macro_nv 네이버),
    # 차트는 **원자재 제외**(원자재 차트는 네이버 history 로 — 아래 com_spark).
    all_yf_sids = [sid for _, _, _, src, sid, _ in (DOMESTIC + GLOBAL) if src == "yf"]
    _is_com = lambda s: _MACRO_NAVER.get(s, (None,))[0] == "com"
    yf_tickers = [s for s in all_yf_sids if not _is_com(s)]
    # 원자재 차트 = 네이버 history (yfinance 무티커 LME 금속·철광석·throttle 무관,
    # 사용자 2026-06-14 '원자재 차트 다 네이버'). symbolCode→reutersCode·category 자동
    # 매핑 후 ~1년 일봉. 야후 chart 배치에서 원자재 제외 → 야후 부하도 동시 경감.
    com_spark: dict[str, list[float]] = {}
    _com_sids = [s for s in all_yf_sids if _is_com(s)]
    if _com_sids:
        try:
            from concurrent.futures import ThreadPoolExecutor
            from bot import naver_marketindex as _nmh

            def _cs(s):
                return s, _nmh.fetch_commodity_spark(_MACRO_NAVER[s][1], 260)
            with ThreadPoolExecutor(max_workers=8) as _pool:
                for _s, _ser in _pool.map(_cs, _com_sids):
                    com_spark[_s] = _ser
        except Exception as _exc:
            log.warning("macro: commodity spark(naver) batch failed: %s", _exc)
    # 차트(스파크라인)는 yf history(download) — fast_info 아님, rate-limit 무관.
    yf_monthly = _yf_monthly_batch(yf_tickers)
    yf_daily_1mo = _yf_daily_1mo_batch(yf_tickers)
    # ⛔ _yf_daily_change(fast_info ~24콜/갱신) 제거 (사용자 2026-06-14 '매크로카드
    # 맨날 없어져·뭐가 fast_info 트리거하냐'). 이게 1분마다 야후 quote 를 24회 때려
    # YFRateLimitError 유발 → 회로차단 → Macro value None → 카드 소실의 주범이었음.
    # 모든 yf 가격 sid 가 _MACRO_NAVER 에 매핑돼 값은 네이버로 충분, 네이버 결측 시
    # chart_spark[-1](yf_monthly=download/history) 폴백. fast_info 호출 0.
    yf_daily: dict[str, dict] = {}
    macro_nv = _fetch_macro_naver_values(all_yf_sids)   # 값=전체(원자재 포함)

    spark_cache: dict[str, list[float]] = {}  # 큰 차트용(월간 12개월)

    def _build(defs: list) -> list[dict]:
        rows: list[dict] = []
        for key, label, unit, src, sid, dec in defs:
            value: Optional[float] = None
            change: Optional[float] = None
            change_pct: Optional[float] = None  # yf(1개월) 카드 — 일일 % 변화
            chart_spark: list[float] = []   # 큰 차트(월간)
            card_spark: list[float] = []    # 카드 미니(1개월)
            spark_dir = 0
            spark_span = "12개월"           # 라인 기간 라벨(작게 표기)
            if src == "yf":
                # 현재값 = 네이버 우선(카드 안 사라짐), 미매핑/실패는 yf 폴백.
                nv = macro_nv.get(sid)
                d = yf_daily.get(sid)
                if nv:
                    value, change = nv["value"], nv["change"]
                elif d:
                    value, change = d["value"], d["change"]
                # 일일 % 변화 — 한달단위(1개월) 카드는 절대값 대신 %로
                # 표시(사용자 2026-06-10). 단 환율(USD/KRW)는 절대값. prev=value-change.
                if (value is not None and change is not None
                        and sid not in _ABS_CHANGE_SIDS):
                    _prev = value - change
                    if _prev not in (None, 0):
                        change_pct = change / _prev * 100
                if sid in com_spark:
                    # 원자재 — 네이버 history(yfinance 무티커 LME 금속·철광석·throttle
                    # 무관). 카드=최근 1개월(뒤 22 거래일), 큰 차트=~1년 일봉. 값과
                    # 같은 네이버 소스라 라인 끝이 현재가와 자연 싱크(사용자 2026-06-14).
                    _ser = com_spark.get(sid) or []
                    chart_spark = _ser
                    card_spark = _ser[-22:] if len(_ser) >= 22 else _ser
                    spark_span = "1개월"
                    if value is None and _ser:
                        value = _ser[-1]
                    spark_dir = _spark_dir(card_spark, 0)
                else:
                    chart_spark = yf_monthly.get(sid, [])
                    # 카드 그래프 = 항상 최근 1개월 일봉 (사용자 2026-06-14 '캡쳐한
                    # 가격카드는 전부 1개월기준'). 색 = 1개월 시작 대비 현재(첫↔끝):
                    # 하락 빨강 / 상승 녹색. 일봉 결측 시에만 월간 꼬리로 폴백(드묾,
                    # _yf_daily_1mo_batch 개별 재시도로 결측 최소화). FRED/ECOS(월간)는
                    # 이 분기 밖이라 12개월 유지(사용자 '모든게 아니고 캡쳐한 것만').
                    one_mo = yf_daily_1mo.get(sid) or []
                    card_spark = one_mo or (chart_spark[-2:] if len(chart_spark) >= 2
                                            else chart_spark)
                    spark_span = "1개월"
                    if value is None and chart_spark:
                        value = chart_spark[-1]
                    spark_dir = _spark_dir(card_spark, 0)
            elif src == "fred":
                chart_spark = _fred_monthly(sid)
                card_spark = chart_spark      # 월간 시계열(일봉 없음)
                if chart_spark:
                    value = chart_spark[-1]
                    if len(chart_spark) >= 2:
                        change = chart_spark[-1] - chart_spark[-2]
                spark_dir = _spark_dir(card_spark, -2)  # 직전 월 대비
            elif src == "ecos":
                pts = _ecos_series(sid)
                if pts:
                    value = pts[-1][1]
                    if len(pts) >= 2:
                        change = pts[-1][1] - pts[-2][1]
                    chart_spark = _downsample_monthly(pts)
                    card_spark = chart_spark
                spark_dir = _spark_dir(card_spark, -2)
            if value is None:
                continue  # graceful: drop empty cards
            spark_cache[key] = chart_spark   # 큰 차트는 월간 유지
            rows.append({
                "key": key, "label": label, "unit": unit,
                "value": value, "change": change, "change_pct": change_pct,
                "decimals": dec,
                "spark": card_spark, "spark_dir": spark_dir,
                "spark_span": spark_span,
            })
        return rows

    domestic = _build(DOMESTIC)
    glob = _build(GLOBAL)

    # ── Derived charts (reuse the sparklines we already fetched) ──
    charts = _build_charts(spark_cache, (macro_nv.get("^VIX") or {}).get("value"))

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


def _build_charts(spark: dict[str, list[float]],
                  vix_value: Optional[float] = None) -> dict[str, Any]:
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

    # sentiment (VIX → 0-100, higher score = greed). VIX 값 = 네이버 우선(macro_nv,
    # 야후 throttle 무관, 사용자 2026-06-14 '게이지도 네이버·빅스 역산이고 네이버에
    # 있으니까'), 없으면 yf 스파크 마지막값 폴백.
    vix_spark = spark.get("vix", [])
    vix = (vix_value if isinstance(vix_value, (int, float))
           else (vix_spark[-1] if vix_spark else None))
    if isinstance(vix, (int, float)):
        charts["sentiment"] = {"score": _vix_to_score(vix), "vix": vix}

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
