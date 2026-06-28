"""기술 분석 탭 — 검증 지표 세트(무료) + 강세/약세 토론·판정·시나리오(LLM 1콜).

사용자 2026-06-28. 우리 종목분석 방법론(TradingAgents bull/bear/judge 구조)을
기술전용·저비용으로 재사용. cost-gated:
  • compute_indicators(): yfinance 일봉 → 지표 dict. LLM 0원. 탭 열면 즉시.
  • run_debate(): 지표 → 단일 Gemini(flash-lite) 구조화 호출 → 강세/약세·축별
    판정·시나리오. 클릭 시에만 실행. (티커, KST날짜) 캐시 → 같은 날 재실행 무과금.
    usage.jsonl 비용 기록(/usage·대시보드 합산).

우리 강점 반영: 5거래일 horizon 판정, corp action 가드(감자/분할 시 경고),
한국어 자연 문체, DATA OFFLINE 가드(지표 없으면 None → 호출부가 안내).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
_CACHE_DIR = Path.home() / ".tradingagents" / "technical_cache"
_USAGE_LOG = Path.home() / ".tradingagents" / "usage.jsonl"
_USD_TO_KRW = 1380

# gemini-2.5-flash-lite 단가(USD/1M tok, 근사 — 비용 기록용. 정밀 청구는 GCP 콘솔).
_IN_PER_M = 0.10
_OUT_PER_M = 0.40
_MODEL = "gemini-2.5-flash-lite"


def today_kst() -> str:
    return datetime.now(_KST).date().isoformat()


# ─────────────────────────────────────────────────────────────────────────
# 검증 지표 세트 (무료 — pandas 계산)
# ─────────────────────────────────────────────────────────────────────────
def compute_indicators(ticker: str) -> dict | None:
    """yfinance 일봉 1년 → 기술지표 dict. 데이터 없으면 None(DATA OFFLINE 가드)."""
    try:
        import yfinance as yf
        import pandas as pd  # noqa: F401
    except Exception as exc:
        log.warning("technical: yfinance/pandas import fail: %s", exc)
        return None
    try:
        hist = yf.Ticker(ticker).history(period="1y", auto_adjust=False)
    except Exception as exc:
        log.warning("technical: history fetch %s: %s", ticker, exc)
        return None
    if hist is None or len(hist) < 30:
        return None
    close = hist["Close"].dropna()
    vol = hist["Volume"].reindex(close.index).fillna(0)
    high, low = hist["High"].reindex(close.index), hist["Low"].reindex(close.index)
    if close.empty:
        return None

    def _last(s):
        v = s.dropna()
        return float(v.iloc[-1]) if len(v) else None

    ema10 = _last(close.ewm(span=10, adjust=False).mean())
    sma50 = _last(close.rolling(50).mean())
    sma200 = _last(close.rolling(200).mean())
    # RSI14 (Wilder)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi14 = _last(100 - 100 / (1 + rs))
    # MACD(12,26,9)
    macd_line = (close.ewm(span=12, adjust=False).mean()
                 - close.ewm(span=26, adjust=False).mean())
    macd_sig = macd_line.ewm(span=9, adjust=False).mean()
    macd, macds = _last(macd_line), _last(macd_sig)
    macdh = (macd - macds) if (macd is not None and macds is not None) else None
    # Bollinger(20,2) 위치% = (close-LB)/(UB-LB)*100
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    ub, lb = mid + 2 * std, mid - 2 * std
    cur, ub_v, lb_v = _last(close), _last(ub), _last(lb)
    boll_pos = (None if None in (cur, ub_v, lb_v) or ub_v == lb_v
                else round((cur - lb_v) / (ub_v - lb_v) * 100, 1))
    # ATR14
    tr = pd_true_range(high, low, close)
    atr14 = _last(tr.ewm(alpha=1 / 14, adjust=False).mean())
    atr_pct = (round(atr14 / cur * 100, 1) if (atr14 and cur) else None)
    # VWMA20 = Σ(close·vol)/Σ(vol)
    pv = (close * vol).rolling(20).sum()
    vv = vol.rolling(20).sum()
    vwma20 = _last((pv / vv.replace(0, float("nan"))))
    # 거래량 / 20일 평균
    vol_avg20 = vol.rolling(20).mean()
    vol_ratio = (round(float(vol.iloc[-1]) / float(vol_avg20.iloc[-1]), 2)
                 if len(vol_avg20.dropna()) else None)
    # 60일 고저
    hi60 = float(close.tail(60).max())
    lo60 = float(close.tail(60).min())

    def _r(v, n=2):
        return round(v, n) if isinstance(v, (int, float)) and not math.isnan(v) else None

    return {
        "ticker": ticker, "asof": str(close.index[-1].date()),
        "close": _r(cur), "ema10": _r(ema10), "sma50": _r(sma50),
        "sma200": _r(sma200), "rsi14": _r(rsi14, 1), "macd": _r(macd, 3),
        "macd_signal": _r(macds, 3), "macd_hist": _r(macdh, 3),
        "boll_pos_pct": boll_pos, "atr14": _r(atr14), "atr_pct": atr_pct,
        "vwma20": _r(vwma20), "vol_ratio": vol_ratio,
        "high60": _r(hi60), "low60": _r(lo60),
    }


def pd_true_range(high, low, close):
    import pandas as pd
    prev = close.shift(1)
    return pd.concat([(high - low), (high - prev).abs(),
                      (low - prev).abs()], axis=1).max(axis=1)


# ─────────────────────────────────────────────────────────────────────────
# LLM 토론 (cost-gated, 캐시)
# ─────────────────────────────────────────────────────────────────────────
def _cache_file(ticker: str, date: str) -> Path:
    safe = "".join(c for c in ticker if c.isalnum() or c in "._-")
    return _CACHE_DIR / f"{safe}__{date}.json"


def cached_debate(ticker: str) -> dict | None:
    f = _cache_file(ticker, today_kst())
    if f.exists():
        try:
            return json.loads(f.read_text("utf-8"))
        except Exception:
            return None
    return None


def _log_usage(pt: int, ot: int) -> None:
    try:
        _USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        cost = (pt * _IN_PER_M + ot * _OUT_PER_M) / 1e6
        rec = {"ts": time.time(), "type": "llm_call", "model": _MODEL,
               "prompt_tokens": pt, "completion_tokens": ot,
               "cost_usd": round(cost, 6), "subsystem": "technical"}
        with open(_USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("technical: usage log fail: %s", exc)


def _prompt(ticker: str, ind: dict) -> str:
    return (
        "너는 기술적 분석 리서치 매니저다. 아래 지표만 근거로 강세/약세 연구원의 대립\n"
        "논거를 정리하고 축별 판정 후 종합 판정을 내려라. 우리 규칙:\n"
        "1) 결론은 5거래일(단기) 방향성 — 장기 thesis 아님.\n"
        "2) 지표에 없는 수치·뉴스·펀더멘털 날조 금지(주어진 지표만).\n"
        "3) 한국어 자연 문체(번역체·기계적 병렬 회피), 간결.\n"
        "4) 감자/액면분할 등 corp action 의심 신호(가격 급변 등)면 기술지표 신뢰도\n"
        "   낮춤을 명시.\n\n"
        f"종목: {ticker} (기준일 {ind.get('asof')})\n"
        f"지표: {json.dumps(ind, ensure_ascii=False)}\n\n"
        "아래 JSON 스키마로만 출력(설명·코드펜스 금지):\n"
        '{"verdict":"강한 상승 우위|상승 우위|중립|하락 우위|강한 하락 우위",'
        '"score":0-100,"confidence":0-100,"consensus":0-100,'
        '"bull":["..."],"bear":["..."],'
        '"axes":[{"name":"추세 정렬|모멘텀|변동성·밴드|거래량 확인|가격대 위치",'
        '"bull":"...","bear":"...","verdict":"+1.00 강세 우위 형태 문자열"}],'
        '"scenarios":{"up":"추세 강화 조건","down":"추세 약화 조건","range":"통상 잠잠 범위"}}'
    )


def _parse_json(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(t[a:b + 1])
    except Exception:
        return None


def run_debate(ticker: str, indicators: dict, *, force: bool = False) -> dict:
    """지표 → 단일 Gemini 호출 → 구조화 토론. 캐시(티커+KST날짜) 우선.
    반환 {ok, ...debate} 또는 {ok:False, error}."""
    if not force:
        c = cached_debate(ticker)
        if c:
            c["cached"] = True
            return c
    try:
        from bot.genai_factory import make_client
        client = make_client()
        try:
            from google.genai import types as _t
            cfg = _t.GenerateContentConfig(response_mime_type="application/json")
            resp = client.models.generate_content(
                model=_MODEL, contents=_prompt(ticker, indicators), config=cfg)
        except Exception:
            resp = client.models.generate_content(
                model=_MODEL, contents=_prompt(ticker, indicators))
    except Exception as exc:
        log.warning("technical: LLM call %s: %s", ticker, exc)
        return {"ok": False, "error": "LLM 호출 실패"}
    text = (getattr(resp, "text", None) or "").strip()
    pt = ot = 0
    try:
        um = getattr(resp, "usage_metadata", None)
        if um:
            pt = int(getattr(um, "prompt_token_count", 0) or 0)
            ot = int(getattr(um, "candidates_token_count", 0) or 0)
    except Exception:
        pass
    _log_usage(pt, ot)
    data = _parse_json(text)
    if not data:
        return {"ok": False, "error": "응답 파싱 실패"}
    data["ok"] = True
    data["cached"] = False
    data["asof"] = indicators.get("asof")
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file(ticker, today_kst()).write_text(
            json.dumps(data, ensure_ascii=False), "utf-8")
    except Exception as exc:
        log.warning("technical: cache write fail: %s", exc)
    return data
