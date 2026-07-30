"""Price-chart payload builder for the per-analysis detail page.

Produces a compact JSON-serializable dict (parallel arrays) that the
dashboard embeds and lightweight-charts renders client-side. The close
series + 21 EMA / 55 SMA / 200 SMA are computed from the SAME yfinance
1y close series as `_compute_technical_snapshot` (agent_utils), so the
chart's moving-average lines agree with the text TECHNICAL SNAPSHOT
(SSoT). Re-fetched here (not threaded through the graph run) for
decoupling — the analysis just finished seconds ago, so the daily-close
series is identical to what the analysts saw.

MA periods 21/55/200 (2026-07-29, Credit Suisse technical_tutorial 방법론
사용자 요청 — Fibonacci 21/55 단기·중기 + 200 장기, Golden/Death Cross =
21↕55 크로스). 이전 10 EMA/50 SMA 에서 전환 — Yahoo `.info` 의
fiftyDayAverage/twoHundredDayAverage 기반 "Canonical 50일/200일 SMA"
(agent_utils.py 펀더멘털 컨텍스트, Minervini 추세템플릿, 시장폭·크립토
레짐 등 별도 지표)는 그 데이터소스 고유 기간이라 변경 대상 아님 — 이 파일
(개별종목 차트 + TECHNICAL SNAPSHOT)만 universal 적용.

Universal: works for US + KR + JP + TW + CN/HK + EU (any yfinance-
covered ticker). Currency symbol / decimals come from market.py so the
chart price scale renders ₩ / ¥ / $ / € correctly. Returns None on any
failure — the detail page then renders text-only (graceful).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


def build_price_chart(ticker: str, as_of: str | None = None) -> dict | None:
    """Return a compact chart payload for `ticker`, or None on failure.

    `as_of` (YYYY-MM-DD): when given, fetch the 1y window ENDING at that
    date (for backfilling old archive entries — no look-ahead, and the
    moving averages match the analysis-date TECHNICAL SNAPSHOT). When
    None (live path at analysis time), use period='1y' (ends today ≈
    analysis date).

    Shape (parallel arrays aligned to `times`):
        {
          "currency": "$",          # market currency symbol
          "decimals": 2,            # 0 for KRW/JPY, else 2
          "times":  ["2025-06-03", ...],   # daily (yyyy-mm-dd)
          "close":  [145.20, ...],
          "ema21":  [144.10, ...],         # full length
          "sma55":  [null, ..., 140.0, ...],   # null until 55th bar
          "sma200": [null, ..., 130.0, ...],   # omitted if <200 bars
        }
    """
    try:
        import yfinance as yf

        # Mirror _compute_technical_snapshot: 1y window, auto-adjusted.
        if as_of:
            from datetime import datetime, timedelta
            try:
                end_d = datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=1)
            except ValueError:
                end_d = None
            if end_d is not None:
                # ~400 calendar days ≈ 275 trading days — enough for a
                # trailing 200 SMA at the window's end + visible context.
                start_d = end_d - timedelta(days=400)
                hist = yf.Ticker(ticker).history(
                    start=start_d.strftime("%Y-%m-%d"),
                    end=end_d.strftime("%Y-%m-%d"),
                    auto_adjust=True,
                )
            else:
                hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        else:
            hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if hist is None or len(hist) < 20:
            return None
        close = hist["Close"].dropna()
        if len(close) < 20:
            return None

        currency, decimals = _currency_for(ticker)
        vol = hist["Volume"].reindex(close.index) if "Volume" in hist else None
        op = hist["Open"].reindex(close.index) if "Open" in hist else None
        hi = hist["High"].reindex(close.index) if "High" in hist else None
        lo = hist["Low"].reindex(close.index) if "Low" in hist else None
        payload = _series_payload(close, currency, decimals, vol, op, hi, lo, ticker=ticker)
        # 저장 스냅샷은 1년 일봉 윈도 — 값 패널의 '기간 %' 라벨이 정직하게
        # '1년' 으로 표시되도록 명시(프론트가 d.period 로 라벨 산출).
        payload["period"] = "1y"
        payload["interval"] = "1d"
        return payload
    except Exception as exc:
        log.warning("chart_data: build failed for %s: %s", ticker, exc)
        return None


def _currency_for(ticker: str) -> tuple[str, int]:
    """Market-aware currency symbol + decimals (0 for KRW/JPY, else 2).
    Same source as the text snapshot's Bollinger / MA formatting."""
    try:
        from bot.market import get_market_config as _gcfg
        _c = _gcfg(ticker)
        return (
            _c.get("currency_symbol", "$"),
            0 if _c.get("currency") in ("KRW", "JPY") else 2,
        )
    except Exception:
        return "$", 2


def _series_payload(
    close, currency: str, decimals: int,
    volume=None, opens=None, highs=None, lows=None,
    ticker: str | None = None, interval: str = "1d",
) -> dict:
    """Build the parallel-array chart payload from a pandas close Series.

    Includes: close + 21 EMA / 55 SMA / 200 SMA + RSI(14) + volume +
    Bollinger(20,2σ) + MACD(12,26,9) + OHLC (for candlestick toggle).
    Bollinger / MACD use the SAME formulas as _compute_technical_snapshot
    (SSoT) so the chart overlays match the text analysis. Each block is
    omitted gracefully when the series is too short or inputs are absent."""
    import math

    ema21 = close.ewm(span=21, adjust=False).mean()
    sma55 = close.rolling(55).mean() if len(close) >= 55 else None
    sma200 = close.rolling(200).mean() if len(close) >= 200 else None

    def _round(v, nd=decimals) -> float | None:
        try:
            f = float(v)
            if math.isnan(f):
                return None
            return round(f, nd)
        except Exception:
            return None

    _intraday = interval in ("1m", "5m", "15m", "1h")
    if _intraday:
        import calendar as _cal
        _times = [int(_cal.timegm(d.timetuple())) for d in close.index]
    else:
        _times = [d.strftime("%Y-%m-%d") for d in close.index]

    payload: dict = {
        "currency": currency,
        "decimals": decimals,
        "times": _times,
        "close": [_round(v) for v in close.values],
        "ema21": [_round(v) for v in ema21.values],
    }
    if sma55 is not None:
        payload["sma55"] = [_round(v) for v in sma55.values]
    if sma200 is not None:
        payload["sma200"] = [_round(v) for v in sma200.values]

    # OHLC (candlestick toggle). open/high/low aligned to close.index.
    if opens is not None and highs is not None and lows is not None:
        payload["open"] = [_round(v) for v in opens.values]
        payload["high"] = [_round(v) for v in highs.values]
        payload["low"] = [_round(v) for v in lows.values]

    # Bollinger Bands(20, 2σ) — overlay. Same as snapshot SSoT.
    if len(close) >= 20:
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        payload["bb_u"] = [_round(v) for v in (bb_mid + 2 * bb_std).values]
        payload["bb_m"] = [_round(v) for v in bb_mid.values]
        payload["bb_l"] = [_round(v) for v in (bb_mid - 2 * bb_std).values]

    # RSI(14) — Wilder exponential smoothing (same as snapshot SSoT).
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
        payload["rsi"] = [_round(v, 1) for v in rsi.values]

    # MACD(12, 26, 9) — lower pane (line + signal + histogram). Same as
    # snapshot SSoT. Extra decimal precision (3) since values are small.
    if len(close) >= 27:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        macd_sig = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - macd_sig
        md = max(decimals, 3)
        payload["macd"] = [_round(v, md) for v in macd_line.values]
        payload["macd_signal"] = [_round(v, md) for v in macd_sig.values]
        payload["macd_hist"] = [_round(v, md) for v in macd_hist.values]

    # Volume — histogram in the price pane bottom. Integer counts.
    if volume is not None:
        def _vol(v):
            try:
                f = float(v)
                if math.isnan(f):
                    return None
                return int(f)
            except Exception:
                return None
        payload["volume"] = [_vol(v) for v in volume.values]

    # 공시 이벤트 마커 (전 시장, ₩0). 차트가 보여줄 기간(span)을 넘겨 KR/US 는
    # 풀히스토리, JP/TW/CN 은 시장별 안전 캡으로 fetch. 보이는 날짜 구간으로 필터.
    # 실패/키부재 시 graceful(빈 리스트). 호재/악재 판단 X.
    if ticker and not _intraday:
        try:
            from bot.chart_events import fetch_disclosure_events
            times = payload.get("times") or []
            if times:
                lo_t, hi_t = times[0], times[-1]
                try:
                    import datetime as _ev_dt
                    span = (_ev_dt.date.fromisoformat(hi_t) - _ev_dt.date.fromisoformat(lo_t)).days
                except Exception:
                    span = 400
                payload["events"] = [
                    e for e in fetch_disclosure_events(ticker, days=max(span, 30))
                    if lo_t <= e["time"] <= hi_t
                ]
        except Exception:
            pass

    # 엘리엇 파동 · 피보나치 되돌림 오버레이 (2026-07-29, Credit Suisse
    # technical_tutorial p23~31 — bot/elliott_fib.py 에 규칙 출처 명시).
    # 파동 원리는 프랙탈(CS p23 "patterns ... every day are similar to ... week,
    # month")이라 일봉/분봉/주봉 어느 interval 이든 그 단위로 계산한다.
    # 순수 가격 연산이라 전 시장 동일(universal). 실패해도 차트 본체엔 영향 없음.
    try:
        from bot.elliott_fib import analyze_waves
        _c = payload.get("close") or []
        _hh = payload.get("high") or _c
        _ll = payload.get("low") or _c
        keep = [i for i, cv in enumerate(_c)
                if cv is not None and _hh[i] is not None and _ll[i] is not None]
        if len(keep) >= 30:
            ef = analyze_waves([payload["times"][i] for i in keep],
                               [_hh[i] for i in keep], [_ll[i] for i in keep],
                               [_c[i] for i in keep])
            if ef:
                payload["elliott"] = ef
    except Exception as exc:      # silent-fail 금지 — 원인 로그는 남긴다
        log.debug("chart_data: elliott/fib overlay skipped: %s", exc)
    return payload


# ── KR intraday via KIS API ──────────────────────────────────────────────
_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _fetch_kr_intraday(ticker: str, interval: str = "5m") -> dict | None:
    """KR 종목 당일 분봉 → chart payload. 네이버 분봉 API(사용자 2026-06-15 '분봉도
    네이버로, KIS 말고'). api.stock.naver.com/chart/domestic/item/<code>/minute →
    1분봉 [{localDateTime(YYYYMMDDHHMMSS), currentPrice, openPrice, highPrice,
    lowPrice, accumulatedTradingVolume(누적)}]. 요청 interval 로 OHLCV 리샘플, 누적
    거래량은 분간 증분(diff)으로 변환. 키 불필요·VM 차단 없음. 실패/장전 → None."""
    iv_min = _INTERVAL_MINUTES.get(interval, 5)
    code = (ticker or "").upper().split(".")[0]
    if not code.isdigit():
        return None
    try:
        import requests
        from bot.naver_quote import _HDRS
        r = requests.get(
            f"https://api.stock.naver.com/chart/domestic/item/{code}/minute",
            headers=_HDRS, timeout=8)
        if r.status_code != 200:
            return None
        bars = r.json()
        if not isinstance(bars, list) or len(bars) < 2:
            return None

        import pandas as pd
        from datetime import datetime as _dt

        recs = []
        for b in bars:
            ldt = str(b.get("localDateTime") or "")
            cp = b.get("currentPrice")
            if len(ldt) < 12 or cp is None:
                continue
            try:
                dt = _dt.strptime(ldt[:12], "%Y%m%d%H%M")
            except Exception:
                continue
            recs.append({
                "dt": dt, "Close": float(cp),
                "Open": float(b.get("openPrice") or cp),
                "High": float(b.get("highPrice") or cp),
                "Low": float(b.get("lowPrice") or cp),
                "Volume": float(b.get("accumulatedTradingVolume") or 0),
            })
        if len(recs) < 2:
            return None
        df = pd.DataFrame(recs).set_index("dt").sort_index()
        # 누적 거래량 → 분간 증분(첫 봉은 자기값), 음수 방어
        df["Volume"] = df["Volume"].diff().fillna(df["Volume"]).clip(lower=0)
        if iv_min > 1:                       # 1분봉 → 요청 interval 리샘플
            df = df.resample(f"{iv_min}min").agg(
                {"Open": "first", "High": "max", "Low": "min",
                 "Close": "last", "Volume": "sum"}).dropna()
        close = df["Close"].dropna()
        if len(close) < 2:
            return None

        currency, decimals = _currency_for(ticker)
        payload = _series_payload(
            close, currency, decimals,
            df["Volume"].reindex(close.index), df["Open"].reindex(close.index),
            df["High"].reindex(close.index), df["Low"].reindex(close.index),
            ticker=ticker, interval=interval,
        )
        payload["interval"] = interval
        payload["period"] = "1d"
        # 라이브 현재가 — 네이버 국내 실시간(분봉 마지막점 보정)
        try:
            from bot.naver_quote import fetch_kr_quote
            _lp = (fetch_kr_quote(ticker) or {}).get("price")
            if _lp:
                payload["last_price"] = round(float(_lp), decimals)
        except Exception:
            pass
        # 52주 신고/신저 — yfinance(분봉과 무관, EOD OK)
        try:
            import yfinance as yf
            hi52, lo52 = _year_high_low(yf.Ticker(ticker), decimals)
            if hi52 is not None:
                payload["wk52_high"] = hi52
            if lo52 is not None:
                payload["wk52_low"] = lo52
        except Exception:
            pass
        return payload
    except Exception as exc:
        log.warning("chart_data: KR intraday (naver) failed %s: %s", ticker, exc)
        return None


# On-demand timeframe fetch (dashboard /api/chart endpoint). interval +
# period are whitelisted; MAs (21/55/200) recompute on the chosen interval
# (so weekly view = 21wk/55wk/200wk — diverges from the daily text SSoT,
# which is expected). Returns None on failure (client keeps current view).
_VALID_INTERVALS = {"5m", "15m", "30m", "1h", "1d", "1wk", "1mo"}
_VALID_PERIODS = {"1d", "1wk", "1mo", "3mo", "6mo", "ytd", "1y", "3y", "5y", "max"}
# Range → 대략 캘린더 일수. yfinance 의 period 문자열에는 '3y' 가 없어
# (유효: 1mo/3mo/6mo/1y/2y/5y/10y/max) 전 범위를 start/end 로 통일 fetch
# → '3y' 정상 동작 + 1개월/3개월 추가가 일관되게 작동. '1d'(당일)은 5분봉
# intraday 라 period='1d' 문자열 직접 fetch (start/end 미사용).
_RANGE_DAYS = {
    "1d": 1, "1wk": 8, "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "3y": 1100,
    "5y": 1830,
}


# ── Live-price (~15분) sanity 가드 ────────────────────────────────────────
# yfinance fast_info 시세는 일부 종목(특히 KR/JP/CN)에서 잘못된 분할·조정
# 기준 / stale / junk 값을 반환한다. 이를 그대로 차트 마지막 봉에 박으면
# 가짜 절벽이 생기고 현재가/분석후/기간 수치까지 오염된다 (파크시스템스
# 140860 2026-05-20: 라이브 163,700 vs 실제 종가 ~280,000 = -42%, 10 EMA
# 278,840 과 모순). 정책(사용자 2026-06-04): 라이브가 조금이라도 의심스러우면
# **무조건 안정적인 '직전 종가' 로 폴백** — 직전 종가는 auto_adjust 시리즈라
# 차트 라인·이동평균과 항상 일치하고 절대 오도하지 않는다(최대 ~15분 stale).
# 임계는 '튜닝 노브' 가 아니라 **물리적 일일 가격제한**(KR ±30% 등)으로만
# 사용 → 그 이상 벌어진 라이브 값은 단일 세션에 불가능 = 글리치 확정.
def _live_price_band(market: str) -> tuple[float, float]:
    """라이브 시세가 직전 종가 대비 들어와야 하는 (low, high) 비율 밴드.

    일일 가격제한 시장(KR ±30% / TW ±10% / CN ±10~20% / JP)은 ±35% 밴드 —
    실제 한도 이동(≤±30%)은 절대 reject 안 되고(밴드가 한도를 5%p 여유로
    포함), 그보다 큰 갭은 물리적으로 불가능하니 글리치로 reject. 일일 한도가
    없는 시장(US/EU/HK)은 느슨한 split-catch 밴드(0.5~2.0x)라 진짜 뉴스 갭은
    살리고 2x/0.5x 급 분할·junk 글리치만 거른다."""
    if market in ("KR", "TW", "CN_A", "JP"):
        return (0.65, 1.35)
    return (0.5, 2.0)


def _validate_live_price(lp, last_close, market: str = "US"):
    """라이브 시세 lp 가 종가 시리즈의 그럴듯한 연속이면 float 로, 아니면
    None(→ 호출부가 직전 종가로 폴백) 반환. 순수·total: 절대 raise 안 하고
    NaN/inf/≤0/형변환 실패/밴드 이탈을 전부 None 처리."""
    try:
        lp = float(lp)
        last_close = float(last_close)
    except (TypeError, ValueError):
        return None
    if not (lp > 0 and last_close > 0):
        return None
    if lp != lp or lp in (float("inf"), float("-inf")):   # NaN / inf
        return None
    low, high = _live_price_band(market)
    return lp if low <= lp / last_close <= high else None


def _year_high_low(t, decimals: int):
    """fast_info 의 52주 신고가/신저가(반올림) → (high, low). 실패 시 (None, None).
    52주값은 차트 interval/range 와 무관한 fundamental — 패널에 항상 표시용.
    ⚠️ fast_info 회로차단/정지 중이면 skip(None) — yahoo IP 차단 회복 보호
    (모든 fast_info 소비처를 회로차단에 물려 쿨다운 중 0콜 → IP 냉각 → 자동 회복)."""
    try:
        from bot.finviz_client import fast_info_ok, yf_paused
        if yf_paused() or not fast_info_ok():
            return (None, None)
        fi = t.fast_info

        def _g(*names):
            for n in names:
                try:
                    v = fi[n] if hasattr(fi, "__getitem__") else getattr(fi, n, None)
                except Exception:
                    v = None
                if v is not None:
                    return v
            return None
        hi = _g("year_high", "yearHigh", "fifty_two_week_high")
        lo = _g("year_low", "yearLow", "fifty_two_week_low")
        return (round(hi, decimals) if isinstance(hi, (int, float)) else None,
                round(lo, decimals) if isinstance(lo, (int, float)) else None)
    except Exception:
        return (None, None)


def _live_last_price(t, payload: dict, decimals: int, ticker: str):
    """검증된 라이브 현재가(반올림) 또는 None(→ 차트가 직전 종가 사용).
    어떤 오류든 raise 하지 않고 None 으로 degrade — '오류 나면 안 된다'."""
    try:
        closes = [c for c in (payload.get("close") or []) if c is not None]
        if not closes:
            return None
        last_close = closes[-1]
        try:
            from bot.market import detect_market
            market = detect_market(ticker)
        except Exception:
            market = "US"
        # 라이브 현재가 폴백 순서 = ① 네이버 → ② KIS → ③ Yahoo (사용자 2026-06-16).
        # Yahoo fast_info 는 ~15분 지연 + IP 서킷브레이커 차단 가능 → 최후순위.
        #  ① 네이버: KR=국내 실시간 · US/JP/HK/CN=해외(worldstock, delayTime:0) ·
        #            TW=TWSE 공식 (네이버는 .TW 미지원).
        #  ② KIS:    KR=국내 실시간(get_realtime_price) · US/JP/HK/CN=해외
        #            (get_overseas_realtime_price — 무료시세 ~15분 지연 가능하나
        #            Yahoo 와 달리 IP 차단 없음) · TW=KIS 미지원 → skip.
        # 검증(_validate_live_price)이 직전 종가 대비 밴드를 강제하므로 어떤
        # 소스의 글리치·거래소 오매칭값도 자동 reject → 다음 소스로 폴백.

        # ① 네이버 (TW 는 네이버 미지원 → TWSE 공식)
        try:
            if market == "KR":
                from bot.naver_quote import fetch_kr_quote
                _q = fetch_kr_quote(ticker)
            elif market == "TW":
                from bot.tw_quote import fetch_tw_quote
                _q = fetch_tw_quote(ticker)
            elif market in ("US", "JP", "HK", "CN_A"):
                from bot.world_quote import fetch_world_quote
                _q = fetch_world_quote(ticker)
            else:
                _q = None
            _nv = _validate_live_price((_q or {}).get("price"), last_close, market)
            if _nv is not None:
                return round(_nv, decimals)
        except Exception as exc:
            log.debug("chart_data: Naver/TWSE price skipped for %s: %s", ticker, exc)

        # ② KIS (대만 미지원 → skip)
        try:
            _kp = None
            if market == "KR":
                from bot.kis_client import get_kis
                _kp = get_kis().get_realtime_price(ticker)
            elif market in ("US", "JP", "HK", "CN_A"):
                from bot.kis_client import get_kis
                _kp = get_kis().get_overseas_realtime_price(ticker)
            _kv = _validate_live_price(_kp, last_close, market)
            if _kv is not None:
                return round(_kv, decimals)
        except Exception as exc:
            log.debug("chart_data: KIS price skipped for %s: %s", ticker, exc)
        # fast_info 회로차단/정지 — 쿨다운·정지 중이면 라이브 skip(차트가 직전 종가
        # 사용). yahoo fast_info IP 차단 회복 보호(사용자 2026-06-14).
        try:
            from bot.finviz_client import fast_info_ok, yf_paused
            if yf_paused() or not fast_info_ok():
                return None
        except Exception:
            pass
        fi = t.fast_info
        raw = None
        for attr in ("last_price", "lastPrice", "regularMarketPrice"):
            try:
                v = fi[attr] if hasattr(fi, "__getitem__") else getattr(fi, attr, None)
            except Exception:
                v = None
            if v is not None:
                raw = v
                break
        valid = _validate_live_price(raw, last_close, market)
        if valid is None:
            if raw is not None:
                log.warning(
                    "chart_data: live price %r implausible vs last close %r for "
                    "%s (%s) — falling back to last close", raw, last_close, ticker, market,
                )
            return None
        return round(valid, decimals)
    except Exception as exc:
        try:
            from bot.finviz_client import fast_info_trip, is_rate_limit_error
            if is_rate_limit_error(exc):
                fast_info_trip("chart_live")
        except Exception:
            pass
        log.warning("chart_data: live price probe failed for %s: %s", ticker, exc)
        return None


def _quote_date(ts):
    """라이브 시세 ts(ISO '..+09:00' 또는 'YYYYMMDD') → date. 실패 시 None."""
    if not ts:
        return None
    import datetime as _d
    s = str(ts)
    try:
        if len(s) == 8 and s.isdigit():            # TWSE '20260615'
            return _d.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        return _d.datetime.fromisoformat(s).date()  # ISO (거래소 현지 tz)
    except Exception:
        return None


def _merge_today_bar(ticker, close, vol, op, hi, lo):
    """일봉(1d) series 의 '당일 봉'을 시장별 라이브 OHLCV 로 갱신/추가.

    yahoo 일봉은 장중 당일 봉이 미완성/누락 → 차트가 전일까지만, 이평선·RSI 도
    전일 기준(사용자 2026-06-15 '장중 당일 캔들 라이브', 비-KR 확장). 시장별 라이브
    소스(KR=네이버 국내·US/JP/HK/CN=네이버 해외·TW=대만거래소)의 당일 OHLCV 로
    마지막 봉 교체(있으면)/추가(없으면) → _series_payload 가 그 위에서 지표 계산.
    당일 판정은 시세 ts(거래소 현지일)와 마지막 봉 날짜 비교(시장별 tz 정확). 글리치는
    직전 종가 대비 _validate_live_price. 실패/미지원 시장은 원본(yahoo) 유지."""
    try:
        from bot.market import detect_market
        mkt = detect_market(ticker)
        o = None
        if mkt == "KR":
            from bot.naver_quote import fetch_kr_quote
            o = fetch_kr_quote(ticker)
        elif mkt == "TW":
            from bot.tw_quote import fetch_tw_quote
            o = fetch_tw_quote(ticker)
        elif mkt in ("US", "JP", "HK", "CN_A"):
            from bot.world_quote import fetch_world_quote
            o = fetch_world_quote(ticker)
        o = o or {}
        c = o.get("close") or o.get("price")
        if not c or close is None or len(close) == 0:
            return close, vol, op, hi, lo
        import pandas as pd
        qdate = _quote_date(o.get("ts"))
        last_date = close.index[-1].date()
        if qdate is None:                  # ts 없으면 마지막 봉을 당일로 간주(교체만)
            qdate = last_date
        # 글리치 가드 — 당일 종가가 직전 종가 대비 비현실이면 원본 유지.
        prior = (float(close.iloc[-2]) if (qdate == last_date and len(close) >= 2)
                 else float(close.iloc[-1]))
        if _validate_live_price(c, prior, mkt) is None:
            return close, vol, op, hi, lo
        o_, h_, l_, v_ = o.get("open"), o.get("high"), o.get("low"), o.get("volume")
        if qdate <= last_date:             # 당일(또는 그 이전) 봉 존재 → 마지막 봉 교체
            close.iloc[-1] = c
            if op is not None and o_ is not None: op.iloc[-1] = o_
            if hi is not None and h_ is not None: hi.iloc[-1] = h_
            if lo is not None and l_ is not None: lo.iloc[-1] = l_
            if vol is not None and v_ is not None: vol.iloc[-1] = v_
        else:                               # 당일 봉 없음(qdate > last) → 새로 추가
            ts = pd.Timestamp(qdate)
            if getattr(close.index, "tz", None) is not None:
                ts = ts.tz_localize(close.index.tz)
            close.loc[ts] = c
            if op is not None: op.loc[ts] = (o_ if o_ is not None else c)
            if hi is not None: hi.loc[ts] = (h_ if h_ is not None else c)
            if lo is not None: lo.loc[ts] = (l_ if l_ is not None else c)
            if vol is not None: vol.loc[ts] = (v_ if v_ is not None else 0)
    except Exception as exc:
        log.debug("chart_data: merge today bar skipped for %s: %s", ticker, exc)
    return close, vol, op, hi, lo


# 하위호환 별칭 — 옛 호출/테스트 (KR 전용 명칭). 동일 함수(전 시장).
_merge_kr_today_bar = _merge_today_bar


# ── 야후 미제공 종목 일봉 폴백 (야후 → 네이버 → KIS, 사용자 2026-06-17) ────────
# yfinance 가 빈 종목(08076.HK 같은 delisted 표기·HK GEM 초소형주 등)은 차트가
# '데이터 없음'. 네이버 차트 일봉(키 불필요)·KIS 일봉(creds) 순으로 폴백. 모두
# graceful(None → 다음 소스 → 최종 None=기존 '데이터 없음'). 외부 API 라 필드명
# 다중 후보 + 검증; VM probe(아래 _probe_fallback)로 양식 확정 후 정밀화 가능.

def _period_start_end(period: str):
    """period → (start, end) datetime — 일봉 폴백 fetch 범위."""
    from datetime import datetime, timedelta
    now = datetime.now()
    end = now + timedelta(days=1)
    if period == "ytd":
        start = datetime(now.year, 1, 1)
    elif period == "max":
        start = end - timedelta(days=4000)
    else:
        start = end - timedelta(days=_RANGE_DAYS.get(period, 366))
    return start, end


def _parse_naver_daily(raw) -> list:
    """네이버 일봉 JSON(list 또는 {key:list}) → rows [{date,open,high,low,close,
    volume}]. 날짜·가격 필드 다중 후보(VM 양식 확인 전 방어). 순수(단위테스트)."""
    rows = raw
    if isinstance(raw, dict):
        for k in ("priceInfos", "chart", "result", "datas", "list", "data"):
            if isinstance(raw.get(k), list):
                rows = raw[k]
                break
    if not isinstance(rows, list):
        return []
    out = []
    for b in rows:
        if not isinstance(b, dict):
            continue
        ds = str(b.get("localDate") or b.get("localDateTime") or b.get("date")
                 or b.get("dt") or b.get("bizdate") or "")
        cp = (b.get("closePrice") if b.get("closePrice") is not None
              else b.get("currentPrice") if b.get("currentPrice") is not None
              else b.get("close"))
        if not ds or cp is None:
            continue

        def g(*ks):
            for k in ks:
                v = b.get(k)
                if v not in (None, ""):
                    return v
            return None
        out.append({
            "date": ds[:10] if "-" in ds[:10] else ds[:8],
            "open": g("openPrice", "open", "startPrice"),
            "high": g("highPrice", "high"),
            "low": g("lowPrice", "low"),
            "close": cp,
            "volume": g("accumulatedTradingVolume", "volume", "accumulatedVolume"),
        })
    return out


def _rows_to_daily_df(rows: list):
    """[{date,open,high,low,close,volume}] → OHLCV DataFrame(DatetimeIndex,
    오름차순·중복제거). <2행이면 None. O/H/L 결측은 종가로, 거래량 결측은 0.
    순수(단위테스트)."""
    import pandas as pd
    from datetime import datetime as _dt
    recs = []
    for b in rows or []:
        if not isinstance(b, dict):
            continue
        ds = str(b.get("date") or "")
        cl = b.get("close")
        if not ds or cl is None:
            continue
        digits = ds.replace("-", "")[:8]
        try:
            dt = _dt.strptime(digits, "%Y%m%d")
            c = float(str(cl).replace(",", ""))
        except Exception:
            continue

        def _f(k):
            v = b.get(k)
            try:
                return float(str(v).replace(",", "")) if v not in (None, "") else c
            except Exception:
                return c

        def _v():
            v = b.get("volume")
            try:
                return float(str(v).replace(",", "")) if v not in (None, "") else 0.0
            except Exception:
                return 0.0
        recs.append({"dt": dt, "Close": c, "Open": _f("open"),
                     "High": _f("high"), "Low": _f("low"), "Volume": _v()})
    if len(recs) < 2:
        return None
    df = pd.DataFrame(recs).set_index("dt").sort_index()
    return df[~df.index.duplicated(keep="last")]


def _df_to_daily_payload(df, ticker: str, period: str):
    """OHLCV DataFrame → chart payload(_series_payload 경유, interval=1d)."""
    if df is None or len(df) < 2:
        return None
    close = df["Close"].dropna()
    if len(close) < 2:
        return None
    currency, decimals = _currency_for(ticker)
    payload = _series_payload(
        close, currency, decimals,
        df["Volume"].reindex(close.index), df["Open"].reindex(close.index),
        df["High"].reindex(close.index), df["Low"].reindex(close.index),
        ticker=ticker, interval="1d",
    )
    payload["interval"] = "1d"
    payload["period"] = period
    return payload


def _fetch_naver_daily(ticker: str, period: str = "1y"):
    """네이버 차트 일봉 폴백. KR=domestic(6자리)·그 외=foreign(reutersCode —
    world_quote 가 해결한 것 재사용). 키 불필요·VM 차단 없음. graceful None."""
    t = (ticker or "").strip().upper()
    if not t:
        return None
    try:
        import requests
        from bot.naver_quote import _HDRS
        start, end = _period_start_end(period)
        params = {"startDateTime": start.strftime("%Y%m%d") + "0000",
                  "endDateTime": end.strftime("%Y%m%d") + "0000"}
        if t.endswith((".KS", ".KQ")):
            code = t.split(".")[0]
            cands = [("domestic", code)] if code.isdigit() else []
        else:
            from bot.world_quote import _candidates, _rc_cache
            ids = ([_rc_cache[t]] if t in _rc_cache else []) + _candidates(t)
            cands = [("foreign", sid) for sid in dict.fromkeys(ids)]
        for kind, sid in cands:
            try:
                r = requests.get(
                    f"https://api.stock.naver.com/chart/{kind}/item/{sid}/day",
                    headers=_HDRS, params=params, timeout=10)
                if r.status_code != 200:
                    continue
                df = _rows_to_daily_df(_parse_naver_daily(r.json()))
                payload = _df_to_daily_payload(df, ticker, period)
                if payload is not None:
                    payload["fallback"] = "네이버"
                    return payload
            except Exception:
                continue
    except Exception as exc:
        log.warning("chart_data: naver daily fallback %s: %s", ticker, exc)
    return None


def _fetch_kis_daily(ticker: str, period: str = "1y"):
    """KIS 일봉 폴백(최종). creds 필요. graceful None."""
    try:
        from bot.kis_client import get_kis
        bars = get_kis().get_daily_chart(ticker, days=_RANGE_DAYS.get(period, 366))
        payload = _df_to_daily_payload(_rows_to_daily_df(bars or []), ticker, period)
        if payload is not None:
            payload["fallback"] = "KIS"
            return payload
    except Exception as exc:
        log.warning("chart_data: kis daily fallback %s: %s", ticker, exc)
    return None


def _fetch_series_fallback(ticker: str, period: str):
    """야후가 빈 종목 → 네이버 → KIS 순 일봉 폴백. 둘 다 실패 시 None
    (호출부가 기존 '데이터 없음'). 사용자 2026-06-17."""
    return _fetch_naver_daily(ticker, period) or _fetch_kis_daily(ticker, period)


def fetch_chart_payload(
    ticker: str, interval: str = "1d", period: str = "1y"
) -> dict | None:
    if interval not in _VALID_INTERVALS:
        interval = "1d"
    if period not in _VALID_PERIODS:
        period = "1y"
    _is_kr = ticker.upper().endswith((".KS", ".KQ"))
    # 단기 기간 → 최적 봉 자동 매핑.
    # KR: yfinance 가 분봉을 제공하지 않으므로 '1d'만 KIS API 로 대체하고,
    # '1wk'/'1mo' 는 일봉으로 유지 (분봉 매핑 skip).
    _PERIOD_INTERVAL_MAP = {
        "1d": "5m", "1wk": "15m", "1mo": "1h",
    }
    if period in _PERIOD_INTERVAL_MAP:
        if _is_kr and period == "1d":
            kr_payload = _fetch_kr_intraday(ticker, _PERIOD_INTERVAL_MAP[period])
            if kr_payload:
                return kr_payload
        if not _is_kr:
            interval = _PERIOD_INTERVAL_MAP[period]
    try:
        import yfinance as yf
        from datetime import datetime, timedelta

        t = yf.Ticker(ticker)
        if period in ("max", "1d"):
            hist = t.history(period=period, interval=interval, auto_adjust=True)
        else:
            now = datetime.now()
            end = now + timedelta(days=1)
            if period == "ytd":
                start = datetime(now.year, 1, 1)
            else:
                start = end - timedelta(days=_RANGE_DAYS.get(period, 366))
            hist = t.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
                auto_adjust=True,
            )
        # Intraday fallback: some markets don't have intraday via yfinance.
        _MIN_INTRADAY = {"5m": 20, "15m": 10, "30m": 10, "1h": 10}
        _got = len(hist) if hist is not None else 0
        if interval in _MIN_INTRADAY and _got < _MIN_INTRADAY[interval]:
            interval = "1d"
            if period in ("max", "1d"):
                hist = t.history(period="5d", interval="1d", auto_adjust=True)
            else:
                hist = t.history(
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval="1d",
                    auto_adjust=True,
                )
        if hist is None or len(hist) < 2:
            return _fetch_series_fallback(ticker, period)   # 야후 빈 → 네이버/KIS
        close = hist["Close"].dropna()
        if len(close) < 2:
            return _fetch_series_fallback(ticker, period)
        currency, decimals = _currency_for(ticker)
        vol = hist["Volume"].reindex(close.index) if "Volume" in hist else None
        op = hist["Open"].reindex(close.index) if "Open" in hist else None
        hi = hist["High"].reindex(close.index) if "High" in hist else None
        lo = hist["Low"].reindex(close.index) if "Low" in hist else None
        # 일봉: yahoo 가 장중 당일 봉을 EOD/미제공 → 시장별 라이브 OHLCV(KR=네이버
        # 국내·US/JP/HK/CN=네이버 해외·TW=대만거래소)로 당일 봉 교체/추가 후 지표
        # 계산(당일 종가가 이평선·RSI 에 반영, 사용자 2026-06-15 비-KR 확장). 주/월봉
        # 제외(당일 일봉을 주/월 series 에 끼우면 안 됨). graceful(실패 시 yahoo 유지).
        if interval == "1d":
            close, vol, op, hi, lo = _merge_today_bar(ticker, close, vol, op, hi, lo)
        payload = _series_payload(close, currency, decimals, vol, op, hi, lo, ticker=ticker, interval=interval)
        payload["interval"] = interval
        payload["period"] = period
        # 장중 last price (yfinance fast_info — ~15분 지연, 무료·무키, ~50ms).
        # 일봉 series 의 마지막 종가는 D-1/EOD 라 장중엔 stale → 프론트가
        # '현재가' 로 우선 사용. 단 fast_info 는 일부 종목(특히 KR/JP/CN)에서
        # 잘못된 분할·조정 기준/stale/junk 값을 반환해 가짜 절벽을 그릴 수
        # 있으므로 _live_last_price 가 직전 종가 대비 검증 — 비현실 값이면
        # None 반환 → 프론트는 안정적인 '직전 종가' 로 폴백(가짜 절벽 차단).
        if interval == "1d":
            lp = _live_last_price(t, payload, decimals, ticker)
            if lp is not None:
                payload["last_price"] = lp
        # 52주 신고가/신저가 — interval 무관(항상 표시). fast_info 캐시라 ₩0.
        hi52, lo52 = _year_high_low(t, decimals)
        if hi52 is not None:
            payload["wk52_high"] = hi52
        if lo52 is not None:
            payload["wk52_low"] = lo52
        return payload
    except Exception as exc:
        log.warning(
            "chart_data: fetch_chart_payload failed %s %s %s: %s",
            ticker, interval, period, exc,
        )
        return _fetch_series_fallback(ticker, period)   # 야후 실패 → 네이버/KIS


# Trade-plan price levels emitted in full_report by the Trader / PM:
#   **Entry Price**: ₩12,345   /   **Stop Loss**: ...   /   **Price Target**: ...
# plus Korean variants (진입가 / 손절가·손절매 / 목표가). Number may carry a
# currency prefix (₩/$/¥/€/NT$/A$) + thousands commas + optional decimals.
_LEVEL_PATTERNS = {
    "entry": re.compile(
        r"(?:Entry\s*Price|진입\s*가격?|매수\s*가)\s*\*{0,2}\s*[:：=]\s*"
        r"[^\d\n]{0,6}?([\d][\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    "stop": re.compile(
        r"(?:Stop\s*Loss|손절\s*가격?|손절매|매도\s*가)\s*\*{0,2}\s*[:：=]\s*"
        r"[^\d\n]{0,6}?([\d][\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    "target": re.compile(
        r"(?:Price\s*Target|Target\s*Price|목표\s*가격?|익절)\s*\*{0,2}\s*[:：=]\s*"
        r"[^\d\n]{0,6}?([\d][\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
}


def parse_trade_levels(
    full_report: str, close_values: list | None = None
) -> dict:
    """Extract entry / stop / target price levels from a saved full_report.

    Returns a dict with only the PLAUSIBLE levels present, e.g.
    ``{"entry": 145.0, "stop": 138.0, "target": 160.0}``. A parsed value
    is kept only when it falls within a sane band relative to the chart's
    close series (0.2x–5x of the last close) — this rejects mis-parses /
    unit slips so the chart never draws a misleading horizontal line
    (the exact risk that kept markers out of chart v1). Empty close_values
    → no plausibility filter possible → return {} (conservative).
    """
    if not full_report or not close_values:
        return {}
    try:
        last = float(close_values[-1])
        lo, hi = last * 0.2, last * 5.0
    except Exception:
        return {}
    out: dict = {}
    for key, pat in _LEVEL_PATTERNS.items():
        m = pat.search(full_report)
        if not m:
            continue
        try:
            val = float(m.group(1).replace(",", ""))
        except (ValueError, AttributeError):
            continue
        if lo <= val <= hi:
            out[key] = round(val, 2)
        else:
            log.info(
                "chart_data: %s level %.2f implausible vs last close %.2f — skipped",
                key, val, last,
            )
    return out
