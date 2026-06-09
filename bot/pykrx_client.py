"""KRX trading-flow data via pykrx — KR-only investor-type net purchases.

The KR market is dominated by foreign / institutional flow on short
horizons: a stock can have great fundamentals and still drop 10% in a
week when foreigners net-sell across the board. yfinance has nothing
on this; KRX is the authoritative source and pykrx wraps the public
endpoint cleanly.

What we expose to the analyzer for KR tickers:

- 5-day net purchases by investor type (foreign / institutional /
  individual), in KRW
- Direction labels ('우호적' / '비우호적' / '중립') for the prompt
  to read as plain text rather than parse the integer

Graceful degradation everywhere: pykrx import missing, network blip,
empty response — return None and let the rest of the analysis run
without the flow block. Cached on disk per (ticker, today) at
~/.tradingagents/cache/pykrx/ — KRX flow data only updates once daily
after the 3:30 KST close so the cache stays valid all day.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.pykrx")

_CACHE_DIR = Path.home() / ".tradingagents" / "cache" / "pykrx"
_CACHE_TTL_HOURS = 12  # KRX flow updates once a day; 12h is generous

# KRX 가 2025-12-27 부터 'KRX Data Marketplace' 로 전환되며 로그인이
# 필수가 됐다 (AI 봇 무단 수집 차단 목적, 데이터 조회는 여전히 무료).
# pykrx (≥1.2.x) 는 KRX_ID/KRX_PW 환경변수로 인증한다. 미설정 시 모든
# fetch 가 anonymous 경로로 빠져 JSONDecodeError + pykrx 내부 logging
# 버그(comm/util.py 의 logging.info(args, kwargs))로 stderr 트레이스백
# 폭주를 일으킨다. 호출 전 krx_login_ready() 로 차단해 단 한 번만
# actionable 경고를 남기고 graceful skip → None.
import os as _os

_KRX_CRED_WARNED = False


def krx_login_ready() -> bool:
    """KRX_ID/KRX_PW 둘 다 .env 에 있으면 True. 미설정 시 최초 1회만
    가입 안내 경고를 남기고 False. pykrx 를 호출하는 모든 함수의 preflight
    gate — universal (main /ticker KR 수급 + Daily Byte 양쪽 적용)."""
    global _KRX_CRED_WARNED
    ready = bool(_os.environ.get("KRX_ID") and _os.environ.get("KRX_PW"))
    if not ready and not _KRX_CRED_WARNED:
        log.warning(
            "pykrx: KRX_ID/KRX_PW 미설정 — KRX 가 2025-12-27 부터 로그인 "
            "필수(KRX Data Marketplace, 무료). Naver/Kakao 로 가입 후 .env "
            "에 KRX_ID/KRX_PW 추가 필요. 그때까지 KR pykrx 수급 데이터 skip."
        )
        _KRX_CRED_WARNED = True
    return ready


def _quiet_pykrx_logging() -> None:
    """pykrx 내부의 logging.info(args, kwargs) 호출(comm/util.py)이 실패
    경로마다 'Logging error' 트레이스백을 stderr 로 도배한다 — 해당
    네임드 로거 레벨을 CRITICAL 로 올려 record 생성 자체를 차단. 자격
    증명이 없어 fetch 가 실패하는 케이스는 krx_login_ready() gate 가
    1차로 막고, 이 함수는 creds 가 있어도 발생하는 transient 실패의
    backstop. best-effort (예외 무시)."""
    try:
        for name in ("pykrx", "pykrx.website", "pykrx.website.comm"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
    except Exception:
        pass


_quiet_pykrx_logging()


def _normalize_code(ticker: str) -> Optional[str]:
    """Strip .KS/.KQ suffix, validate as 6-digit KRX code."""
    code = (ticker or "").upper().split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        return None
    return code


def _fsc_market_cap(ticker: str) -> Optional[dict]:
    """KRX-login-free fallback (금융위 FSC 주식시세) — pykrx 가 creds 부재/
    무데이터로 None 일 때 동일 shape 으로 시총·종가·거래량·상장주식수 반환.
    FSC 는 T+1 지연이라 5거래일 horizon·시총 cross-check 에 무해. 실패 시 None."""
    try:
        from bot.fsc_client import latest_price
        p = latest_price(ticker)
    except Exception as exc:
        log.debug("pykrx market_cap: FSC fallback failed: %s", exc)
        return None
    if not p or not p.get("mrktTotAmt"):
        return None
    bas = str(p.get("basDt") or "")
    date = f"{bas[:4]}-{bas[4:6]}-{bas[6:]}" if len(bas) == 8 else bas
    log.info("pykrx market_cap: FSC fallback used for %s (basDt %s)", ticker, bas)
    return {
        "market_cap": int(p.get("mrktTotAmt") or 0),
        "close": int(p.get("clpr") or 0),
        "volume": int(p.get("trqu") or 0),
        "shares": int(p.get("lstgStCnt") or 0),
        "date": date, "_source": "fsc",
    }


def get_kr_market_cap(ticker: str) -> Optional[dict]:
    """Fetch KRX official 시가총액 + 최신 close 를 cross-check 용으로
    반환 (Phase 4-CN-D D1 fallback). yfinance .info marketCap 이 일부
    KR 종목에 stale / corrupt / missing 한 케이스에 KRX 직접 값으로
    cross-check + override 가능.

    Returns dict {market_cap: int (원), close: int (원), volume: int,
    shares: int (상장주식수), date: str (YYYY-MM-DD)} or None on failure.

    pykrx 의 stock.get_market_cap_by_ticker() 사용 — KRX 공식 데이터.
    가장 최근 영업일 기준 (오늘이 휴일이면 직전 영업일).
    """
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return _fsc_market_cap(ticker)  # KRX creds 부재 → FSC 백본
    try:
        from pykrx import stock
        import pandas as pd
        from datetime import datetime, timedelta
    except Exception as exc:
        log.warning("pykrx market_cap: import failed: %s", exc)
        return _fsc_market_cap(ticker)

    today = datetime.now()
    # Try recent ~7 days backward to handle weekends + holidays
    for days_back in range(0, 10):
        target = today - timedelta(days=days_back)
        date_str = target.strftime("%Y%m%d")
        try:
            df = stock.get_market_cap_by_ticker(date_str)
        except Exception as exc:
            log.warning(
                "pykrx market_cap: fetch failed for %s: %s",
                date_str, exc,
            )
            continue
        if df is None or len(df) == 0:
            continue
        if code not in df.index:
            # Code 미상장 또는 KRX 가 반환하지 않음 — try same date 다음 종목
            continue
        try:
            row = df.loc[code]
            return {
                "market_cap": int(row.get("시가총액", 0) or 0),
                "close": int(row.get("종가", 0) or 0),
                "volume": int(row.get("거래량", 0) or 0),
                # 상장주식수 — root cause of 047810.KS N/A cascade
                # (yfinance .info sharesOutstanding missing for many KR
                # tickers, KIS lstn_stcn occasionally 0). KRX 공식값.
                "shares": int(row.get("상장주식수", 0) or 0),
                "date": target.strftime("%Y-%m-%d"),
            }
        except Exception as exc:
            log.warning(
                "pykrx market_cap: row parse failed for %s on %s: %s",
                code, date_str, exc,
            )
            continue
    log.info("pykrx market_cap: no data found for %s in last 10 days", code)
    return _fsc_market_cap(ticker)  # pykrx 무데이터 → FSC 백본
    return None


def get_kr_ohlcv_stats(ticker: str) -> Optional[dict]:
    """260 거래일 OHLCV 기반 52주 최고/최저 + 50일/200일 SMA 산출 (D1
    Phase 3 fallback, 307950.KS 2026-05-23 surfaced). yfinance .info 가
    일부 KR 종목에서 fiftyTwoWeekHigh/Low + fiftyDayAverage/
    twoHundredDayAverage 를 비워서 반환 — pykrx 일봉 시계열에서 직접
    계산해 채운다.

    Returns dict {wk_high, wk_low, sma50, sma200, last_close, days, date}
    in KRW (원). None when pykrx 미설치 / 미상장 / 시계열 empty.

    Cached on disk per (ticker, today) — KRX OHLCV 가 daily close 후
    갱신되므로 12h TTL 충분.
    """
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"ohlcv_stats_{code}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        log.warning("pykrx not installed; KR OHLCV stats unavailable")
        return None

    end = date.today()
    # 52주 = 252 거래일 → 캘린더 ~370일 buffer (주말 + 공휴일 포함)
    start = end - timedelta(days=380)
    try:
        df = stock.get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: OHLCV fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty or len(df) < 50:
        return None

    try:
        # 마지막 252 거래일로 52w 윈도 산정
        recent_252 = df.tail(252)
        wk_high = int(recent_252["고가"].max())
        wk_low  = int(recent_252["저가"].min())
        # SMA — 마지막 N 거래일 종가 평균
        sma50_vals  = df["종가"].tail(50).astype(float)
        sma200_vals = df["종가"].tail(200).astype(float)
        sma50  = float(sma50_vals.mean())  if len(sma50_vals)  >= 50  else None
        sma200 = float(sma200_vals.mean()) if len(sma200_vals) >= 200 else None
        last_close = int(df["종가"].iloc[-1])
        last_date = df.index[-1]
        try:
            last_date_str = last_date.strftime("%Y-%m-%d")
        except Exception:
            last_date_str = str(last_date)[:10]
        result = {
            "wk_high":    wk_high,
            "wk_low":     wk_low,
            "sma50":      sma50,
            "sma200":     sma200,
            "last_close": last_close,
            "days":       int(len(df)),
            "date":       last_date_str,
        }
    except Exception as exc:
        log.warning("pykrx: OHLCV stats parse failed for %s: %s", code, exc)
        return None

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    return result


def get_kr_beta_60m(ticker: str) -> Optional[float]:
    """60-month monthly beta of a KR stock vs KOSPI 200 (pykrx index
    code 1028). yfinance .info["beta"] for KR tickers is computed
    against US benchmarks (typically S&P 500) regardless of the labeled
    benchmark — 010140.KS 2026-05-23 펀더 박스 quoted '베타 1.83 (vs
    KOSPI 200)' but the underlying value was actually vs S&P 500.
    Recompute against the correct KR market index.

    Monthly resampling: take last business-day close of each month for
    both the stock and the index, compute monthly returns, then
    beta = Cov(stock_ret, market_ret) / Var(market_ret). Requires at
    least 36 months of overlap; returns None below that.

    Disk-cached per (ticker, today) at 12h TTL.

    Rule applies to all KR analyses going forward — surfaced by
    삼성중공업 010140.KS 2026-05-23 review.
    """
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return None
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"beta60m_{code}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                return cached.get("beta")
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        return None

    end = date.today()
    # 5년 + 6개월 buffer (휴장일 / 신규 상장 등 흡수)
    start = end - timedelta(days=int(365.25 * 5.5))
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    try:
        stock_df = stock.get_market_ohlcv_by_date(start_str, end_str, code)
        idx_df = stock.get_index_ohlcv_by_date(start_str, end_str, "1028")
    except Exception as exc:
        log.warning("pykrx: beta60m fetch failed for %s: %s", code, exc)
        return None

    if (stock_df is None or stock_df.empty
            or idx_df is None or idx_df.empty):
        return None

    try:
        s_close = stock_df["종가"].astype(float)
        i_close = idx_df["종가"].astype(float)
        # 월별 마지막 영업일 종가로 resample
        s_monthly = s_close.resample("ME").last()
        i_monthly = i_close.resample("ME").last()
        s_ret = s_monthly.pct_change().dropna()
        i_ret = i_monthly.pct_change().dropna()
        # 공통 인덱스만 사용
        common = s_ret.index.intersection(i_ret.index)
        if len(common) < 36:
            return None
        s_r = s_ret.loc[common]
        i_r = i_ret.loc[common]
        var_m = float(i_r.var())
        if var_m <= 0:
            return None
        cov_sm = float(s_r.cov(i_r))
        beta = cov_sm / var_m
    except Exception as exc:
        log.warning("pykrx: beta60m compute failed for %s: %s", code, exc)
        return None

    if beta != beta or beta is None:
        return None
    beta = round(float(beta), 3)

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"beta": beta, "n": len(common)}))
    except Exception:
        pass

    return beta


def get_kr_trading_flow(ticker: str, days_back: int = 5) -> Optional[dict]:
    """5-day investor-type net purchase summary for a KR ticker.

    Returns:
        dict with keys:
          - foreign_net (int, KRW)
          - institutional_net (int, KRW)
          - individual_net (int, KRW)
          - days (int, actual trading days covered — may be < days_back
            around holidays)
        OR None when pykrx is missing, the ticker is invalid, KRX
        returns empty, or any exception occurs.

    Sign convention: positive = net buy by that investor group over
    the window. Foreign + institutional + individual net purchases
    sum to ~zero by construction (every buy has a sell).
    """
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return None

    # Cache check — flow data is daily, key by today's date.
    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"flow_{code}_{today_str}_{days_back}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception as exc:
            log.warning("pykrx: cache read failed for %s: %s", code, exc)

    try:
        from pykrx import stock
    except ImportError:
        log.warning("pykrx not installed; KR trading flow unavailable")
        return None

    end = date.today()
    # Buffer for weekends + KR public holidays in the window.
    start = end - timedelta(days=days_back + 10)

    try:
        df = stock.get_market_trading_value_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: trading flow fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty:
        log.info("pykrx: empty trading flow response for %s", code)
        return None

    # Take last N trading days. pykrx returns one row per trading day
    # (already excludes weekends/holidays), sorted ascending by date.
    recent = df.tail(days_back)
    if recent.empty:
        return None

    # Sum investor-type net values. pykrx column names differ slightly
    # by version; try the modern set first, fall back to alternates.
    def _sum_col(*candidates) -> int:
        for col in candidates:
            if col in recent.columns:
                try:
                    return int(recent[col].sum())
                except Exception:
                    continue
        return 0

    foreign = _sum_col("외국인합계", "외국인", "외국인투자자")
    institutional = _sum_col("기관합계", "기관", "기관투자자")
    individual = _sum_col("개인")

    result = {
        "foreign_net": foreign,
        "institutional_net": institutional,
        "individual_net": individual,
        "days": int(len(recent)),
    }

    # Cache (best-effort)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception as exc:
        log.warning("pykrx: cache write failed for %s: %s", code, exc)

    return result


def format_flow_for_prompt(flow: dict) -> str:
    """Render the trading-flow dict as a Korean bullet list for the
    instrument context. Adds a directional label so the analyst can
    read it as a discrete signal without parsing the integer."""
    if not flow:
        return ""

    def _label(val: int) -> str:
        if val > 0:
            return "순매수"
        if val < 0:
            return "순매도"
        return "중립"

    def _fmt_krw(val: int) -> str:
        """Render KRW with 억 unit. KRX flow numbers are typically in
        the tens-of-billions range; 억 is the natural read."""
        if val > 0:
            return f"+₩{val / 1e8:,.1f}억"
        if val < 0:
            return f"-₩{abs(val) / 1e8:,.1f}억"
        return "±₩0.0억"

    days = flow.get("days", "?")
    foreign = flow.get("foreign_net", 0)
    inst = flow.get("institutional_net", 0)
    indiv = flow.get("individual_net", 0)

    out = [
        f"- 외국인 {days}거래일 누적: {_fmt_krw(foreign)} ({_label(foreign)})",
        f"- 기관 {days}거래일 누적: {_fmt_krw(inst)} ({_label(inst)})",
        f"- 개인 {days}거래일 누적: {_fmt_krw(indiv)} ({_label(indiv)})",
    ]
    # 단위 혼동 가드 (Samsung 005930 2026-05-31 review): LLM 이 5일 누적
    # ±수천억~조 단위 수급을 RULE 10 의 '당일 ±100억 noise' 기준에 잘못
    # 적용해 "기관 +2조 순매수" 를 "±100억 미만 노이즈" 로 둔갑시킨 모순
    # 발생. Python 이 5일 누적 magnitude 를 미리 판정해 박는다 (KIS 패턴
    # mirror). 위 숫자는 모두 '5거래일 누적' 이며 '당일' 이 아님.
    _strong = [lab for lab, v in (("외국인", foreign), ("기관", inst),
                                  ("개인", indiv)) if abs(v) >= 1e11]  # ≥1,000억
    if _strong:
        out.append(
            "  ⚠️ 위 수치는 모두 **5거래일 누적**(당일 아님). "
            + " / ".join(_strong)
            + " 은 ±1,000억 이상의 강한 누적 수급으로 '노이즈'가 아니라"
            " 5거래일 horizon 의 dominant 수급 신호다. RULE 10 의 '당일"
            " ±100억 noise' 기준을 5일 누적값에 적용 금지.")
    return "\n".join(out)


def get_kr_foreign_ownership_trend(ticker: str, days_back: int = 30) -> Optional[dict]:
    """30-day foreign ownership rate trajectory for a KR ticker.

    Pulls KRX's daily foreign-ownership series (한도소진률 or 지분율
    depending on the column variant pykrx returns) and reports:
      - current_pct: today's foreign holding %
      - start_pct: the % `days_back` calendar days ago
      - change_pp: current - start (percentage points)
      - days: actual trading days covered

    Used to surface trend signals like "외국인이 꾸준히 늘리는 중
    (+1.2pp 30일)" that single-day net-purchase data doesn't show.
    Returns None on any failure (graceful)."""
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"foreign_own_{code}_{today_str}_{days_back}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        return None

    end = date.today()
    start = end - timedelta(days=days_back + 10)
    try:
        df = stock.get_exhaustion_rates_of_foreign_investment(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: foreign ownership fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty or len(df) < 2:
        return None

    # pykrx column names vary by version; try the common candidates
    # in order of how likely they are to carry the rate.
    pct_col = None
    for c in ("지분율", "보유비중", "한도소진률", "한도소진율"):
        if c in df.columns:
            pct_col = c
            break
    if pct_col is None:
        return None

    try:
        current = float(df[pct_col].iloc[-1])
        start_val = float(df[pct_col].iloc[0])
    except Exception:
        return None

    result = {
        "current_pct": current,
        "start_pct": start_val,
        "change_pp": current - start_val,
        "days": int(len(df)),
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    return result


def get_kr_foreign_holding(ticker: str) -> Optional[dict]:
    """KRX 공식 외국인 보유현황 스냅샷 (최신 1일).

    pykrx get_exhaustion_rates_of_foreign_investment 로 최근 10일 조회 →
    마지막 행에서 보유비율/보유주식수/상장주식수/한도소진율 추출.

    Returns Seibro-compatible shape:
        {
            "date": str (YYYYMMDD),
            "foreign_pct": float,
            "foreign_shares": int,
            "listed_shares": int,
            "limit_exhaustion_pct": float (if available),
            "limit_shares": int (if available),
            "source": "krx",
        }
        or None on failure.
    """
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"foreign_hold_{code}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        return None

    end = date.today()
    start = end - timedelta(days=10)
    try:
        df = stock.get_exhaustion_rates_of_foreign_investment(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: foreign holding fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty:
        return None

    row = df.iloc[-1]
    row_date = df.index[-1]
    if hasattr(row_date, "strftime"):
        dt_str = row_date.strftime("%Y%m%d")
    else:
        dt_str = str(row_date).replace("-", "")[:8]

    def _col(candidates):
        for c in candidates:
            if c in df.columns:
                try:
                    v = float(row[c])
                    return v
                except (TypeError, ValueError):
                    continue
        return None

    foreign_pct = _col(["지분율", "보유비중"])
    if foreign_pct is None:
        return None

    foreign_shares = _col(["보유수량", "보유주식수"])
    listed_shares = _col(["상장주식수"])
    exhaust_pct = _col(["한도소진률", "한도소진율"])
    limit_shares = _col(["한도수량", "한도주식수"])

    result: dict = {
        "date": dt_str,
        "foreign_pct": round(foreign_pct, 2),
        "foreign_shares": int(foreign_shares) if foreign_shares else 0,
        "listed_shares": int(listed_shares) if listed_shares else 0,
        "source": "krx",
    }
    if exhaust_pct is not None:
        result["limit_exhaustion_pct"] = round(exhaust_pct, 2)
    if limit_shares is not None:
        result["limit_shares"] = int(limit_shares)

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    log.info(
        "pykrx: foreign holding %s → pct=%.2f exhaust=%s date=%s",
        code, foreign_pct, exhaust_pct, dt_str,
    )
    return result


def get_kr_short_balance_trend(ticker: str, days_back: int = 30) -> Optional[dict]:
    """30-day short-selling balance trajectory for a KR ticker.

    Returns:
      - current_pct: today's short balance as % of float
      - start_pct: % at the start of the window
      - change_pp: current - start
      - days: trading days covered

    Rising short balance is a bearish positioning signal; collapsing
    short balance with rising price suggests a squeeze. Neither shows
    up in the daily net-purchase data. Returns None on any failure."""
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"short_{code}_{today_str}_{days_back}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        return None

    end = date.today()
    start = end - timedelta(days=days_back + 10)
    try:
        df = stock.get_shorting_balance_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
    except Exception as exc:
        log.warning("pykrx: short balance fetch failed for %s: %s", code, exc)
        return None

    if df is None or df.empty or len(df) < 2:
        return None

    pct_col = None
    for c in ("비중", "공매도비중", "잔고비중"):
        if c in df.columns:
            pct_col = c
            break
    if pct_col is None:
        return None

    try:
        current = float(df[pct_col].iloc[-1])
        start_val = float(df[pct_col].iloc[0])
    except Exception:
        return None

    result = {
        "current_pct": current,
        "start_pct": start_val,
        "change_pp": current - start_val,
        "days": int(len(df)),
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    return result


def format_trend_for_prompt(foreign: Optional[dict], short: Optional[dict]) -> str:
    """Render the foreign-ownership and short-balance trends as a
    prompt block. Direction labels + magnitude qualifiers so the
    analyst reads it as a discrete signal."""
    lines: list[str] = []
    if foreign:
        cur = foreign.get("current_pct", 0)
        chg = foreign.get("change_pp", 0)
        days = foreign.get("days", "?")
        direction = "증가" if chg > 0 else "감소" if chg < 0 else "변동 없음"
        magnitude = "큰 폭" if abs(chg) >= 1.0 else "소폭" if abs(chg) >= 0.2 else "미미"
        lines.append(
            f"- 외국인 지분율 {days}거래일 추이: {cur:.2f}% (시작 {foreign.get('start_pct', 0):.2f}% → 변동 {chg:+.2f}pp, {magnitude} {direction})"
        )
    if short:
        cur = short.get("current_pct", 0)
        chg = short.get("change_pp", 0)
        days = short.get("days", "?")
        direction = "증가" if chg > 0 else "감소" if chg < 0 else "변동 없음"
        magnitude = "큰 폭" if abs(chg) >= 1.0 else "소폭" if abs(chg) >= 0.2 else "미미"
        squeeze_note = ""
        if chg < -0.5:
            squeeze_note = " — 잠재 squeeze 청산 진행 중"
        elif chg > 1.0:
            squeeze_note = " — 베어 positioning 강화"
        lines.append(
            f"- 공매도 잔고 {days}거래일 추이: {cur:.2f}% (시작 {short.get('start_pct', 0):.2f}% → 변동 {chg:+.2f}pp, {magnitude} {direction}){squeeze_note}"
        )
    return "\n".join(lines)


def _pp_from_series(values: list[float], periods: list[int]) -> dict[int, float]:
    """Compute pp changes at lookback points from a sorted daily pct series.

    Returns None for a period if the series is too short (avoids mislabeling
    a 45-day change as "60d").  Requires at least p+1 data points for period p.
    """
    if len(values) < 2:
        return {}
    current = values[-1]
    pds: dict[int, float] = {}
    for p in periods:
        if len(values) > p:
            idx = len(values) - 1 - p
            pds[p] = round(current - values[idx], 3)
        else:
            pds[p] = None
    return pds


def _pykrx_foreign_trend(code: str, start_str: str, end_str: str,
                          periods: list[int]) -> Optional[dict]:
    """Try pykrx for foreign ownership multi-period trend."""
    try:
        from pykrx import stock
    except ImportError:
        return None
    try:
        df = stock.get_exhaustion_rates_of_foreign_investment(
            start_str, end_str, code,
        )
        if df is None or df.empty or len(df) < 2:
            log.info("pykrx: foreign trend empty for %s (cols=%s, rows=%d)",
                     code, list(df.columns) if df is not None and hasattr(df, 'columns') else "?",
                     len(df) if df is not None else 0)
            return None
        log.info("pykrx: foreign trend cols for %s: %s, rows=%d",
                 code, list(df.columns), len(df))
        for c in ("지분율", "보유비중", "보유비율", "한도소진률", "한도소진율"):
            if c in df.columns:
                series = df[c].dropna().tolist()
                if len(series) >= 2:
                    pds = _pp_from_series(series, periods)
                    return {"current_pct": round(series[-1], 2), "periods": pds}
        for c in df.columns:
            vals = df[c].dropna()
            if len(vals) >= 2:
                sample = float(vals.iloc[-1])
                if 0 < sample < 100:
                    series = [float(v) for v in vals]
                    pds = _pp_from_series(series, periods)
                    log.info("pykrx: foreign trend using col '%s' for %s", c, code)
                    return {"current_pct": round(series[-1], 2), "periods": pds}
    except Exception as exc:
        log.info("pykrx: foreign trend failed for %s: %s", code, exc)
    return None


def _seibro_foreign_trend(ticker: str, periods: list[int]) -> Optional[dict]:
    """Fallback: Seibro/KSD daily foreign holding → multi-period pp."""
    try:
        from bot.seibro_client import seibro_key_ready, _is_kr_ticker, _kr_code, _fetch_items, _float
    except ImportError:
        return None
    if not seibro_key_ready() or not _is_kr_ticker(ticker):
        return None
    code = _kr_code(ticker)
    if not code:
        return None
    try:
        begin = (date.today() - timedelta(days=120)).strftime("%Y%m%d")
        items = _fetch_items({"likeSrtnCd": code, "beginBasDt": begin, "numOfRows": 200})
        rows = []
        for it in items:
            if _kr_code(it.get("srtnCd", "")) != code:
                continue
            pct = _float(it.get("frnHldnRt"))
            if pct is None:
                pct = _float(it.get("frnOwnhRt"))
            if pct is not None:
                rows.append((str(it.get("basDt", "")), pct))
        rows.sort(key=lambda r: r[0])
        values = [r[1] for r in rows]
        if len(values) < 2:
            return None
        pds = _pp_from_series(values, periods)
        return {"current_pct": round(values[-1], 2), "periods": pds}
    except Exception as exc:
        log.info("seibro: foreign trend fallback failed for %s: %s", ticker, exc)
        return None


def _has_any_period(pds: Optional[dict]) -> bool:
    """True if at least one period has a non-None value."""
    if not pds:
        return False
    return any(v is not None for v in pds.values())


def get_kr_multi_period_trends(ticker: str) -> Optional[dict]:
    """Multi-period foreign ownership + short balance trends.

    3-tier fallback for foreign: pykrx → Seibro → Naver.
    Short balance: pykrx only (no free alternative).

    Returns:
        {
            "foreign": {"current_pct": float, "periods": {5: pp, 10: pp, ...}},
            "short":   {"current_pct": float, "periods": {5: pp, 10: pp, ...}},
        }
        or None on total failure.
    """
    code = _normalize_code(ticker)
    if not code:
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"multi_trend_{code}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                cached = json.loads(cache_file.read_text())
                if cached and cached.get("foreign", {}).get("periods"):
                    return cached
        except Exception:
            pass

    end = date.today()
    start = end - timedelta(days=90)
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")
    periods = [5, 10, 20, 30, 60]
    result: dict = {}

    # Foreign ownership: pykrx → Seibro fallback (Naver has no historical series)
    if krx_login_ready():
        fo = _pykrx_foreign_trend(code, start_str, end_str, periods)
        if fo and _has_any_period(fo.get("periods")):
            result["foreign"] = fo

    if not _has_any_period(result.get("foreign", {}).get("periods")):
        fo = _seibro_foreign_trend(ticker, periods)
        if fo and _has_any_period(fo.get("periods")):
            result["foreign"] = fo

    # Short balance: pykrx only
    if krx_login_ready():
        try:
            from pykrx import stock
            df_s = stock.get_shorting_balance_by_date(start_str, end_str, code)
            if df_s is not None and not df_s.empty and len(df_s) >= 2:
                log.info("pykrx: short balance cols for %s: %s", code, list(df_s.columns))
                for c in ("비중", "공매도비중", "잔고비중", "공매도잔고비중"):
                    if c in df_s.columns:
                        vals = df_s[c].dropna().tolist()
                        if len(vals) >= 2:
                            pds_s = _pp_from_series(vals, periods)
                            result["short"] = {"current_pct": round(vals[-1], 2), "periods": pds_s}
                            break
                if "short" not in result:
                    for c in df_s.columns:
                        vals = df_s[c].dropna()
                        if len(vals) >= 2:
                            sample = float(vals.iloc[-1])
                            if 0 <= sample < 100:
                                series = [float(v) for v in vals]
                                pds_s = _pp_from_series(series, periods)
                                log.info("pykrx: short using col '%s' for %s", c, code)
                                result["short"] = {"current_pct": round(series[-1], 2), "periods": pds_s}
                                break
        except Exception as exc:
            log.info("pykrx: multi-period short failed for %s: %s", code, exc)

    if not result:
        return None

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    return result


_INVESTOR_COLS_DETAIL = [
    ("금융투자", ["금융투자", "금융투자업자"]),
    ("보험", ["보험", "보험회사"]),
    ("투신", ["투신", "투자신탁", "집합투자"]),
    ("사모", ["사모", "사모펀드"]),
    ("은행", ["은행"]),
    ("기타금융", ["기타금융", "기타금융기관"]),
    ("연기금", ["연기금", "연기금등"]),
    ("기타법인", ["기타법인"]),
    ("개인", ["개인"]),
    ("외국인", ["외국인합계", "외국인", "외국인투자자"]),
]

_INVESTOR_COLS_SUMMARY = [
    ("기관", ["기관합계", "기관", "기관투자자"]),
    ("기타법인", ["기타법인"]),
    ("개인", ["개인"]),
    ("외국인", ["외국인합계", "외국인", "외국인투자자"]),
]


def get_kr_investor_detail(ticker: str) -> Optional[dict]:
    """Per-ticker multi-period investor flow detail (pykrx).

    Fetches 90 calendar days of daily net purchases by investor type,
    computes 1d/5d/10d/20d/30d/60d cumulative sums for each.

    Returns:
        {
            "investors": {
                "금융투자": {"1d": int, "5d": int, ..., "60d": int},
                "보험": {...},
                ...
            },
            "unit": "억원",
            "trading_days": int,
        }
        or None on failure. Values in 억원.
    """
    code = _normalize_code(ticker)
    if not code:
        return None
    if not krx_login_ready():
        return None

    today_str = date.today().isoformat()
    cache_file = _CACHE_DIR / f"inv_detail_{code}_{today_str}.json"
    if cache_file.exists():
        try:
            age_h = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_h < _CACHE_TTL_HOURS:
                return json.loads(cache_file.read_text())
        except Exception:
            pass

    try:
        from pykrx import stock
    except ImportError:
        return None

    end = date.today()
    start = end - timedelta(days=90)
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    df = None
    col_map: list[tuple[str, str]] = []  # (display_label, df_column)

    # Try detail=True first for sub-investor breakdown
    try:
        df = stock.get_market_trading_value_by_date(
            start_str, end_str, code, detail=True,
        )
    except (TypeError, Exception):
        # detail param might not exist in older pykrx
        try:
            df = stock.get_market_trading_value_by_date(
                start_str, end_str, code,
            )
        except Exception as exc:
            log.info("pykrx: investor detail fetch failed for %s: %s", code, exc)
            return None

    if df is None or df.empty:
        return None

    # Build column mapping — try detail columns first, fall back to summary
    cols_available = set(df.columns)
    for label, candidates in _INVESTOR_COLS_DETAIL:
        for c in candidates:
            if c in cols_available:
                col_map.append((label, c))
                break

    if len(col_map) < 3:
        col_map = []
        for label, candidates in _INVESTOR_COLS_SUMMARY:
            for c in candidates:
                if c in cols_available:
                    col_map.append((label, c))
                    break

    if not col_map:
        return None

    eok = 1e8  # 억원
    periods = [1, 5, 10, 20, 30, 60]
    investors: dict[str, dict[str, int]] = {}
    n_rows = len(df)

    for label, col in col_map:
        try:
            series = df[col].fillna(0)
        except Exception:
            continue
        pds: dict[str, int] = {}
        for p in periods:
            window = series.tail(min(p, n_rows))
            pds[f"{p}d"] = round(float(window.sum()) / eok, 1)
        investors[label] = pds

    if not investors:
        return None

    result = {
        "investors": investors,
        "unit": "억원",
        "trading_days": n_rows,
    }

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result))
    except Exception:
        pass

    return result
