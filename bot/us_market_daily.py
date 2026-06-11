"""US Market Daily — 미국 장 마감 후 시장 브리프 (07:30 KST, 텔레그램 + 대시보드).

yfinance 주요 지수/섹터 데이터 + Gemini Pro (web search grounding) 내러티브.
KR Daily Byte 와 동일 아카이브(daily_byte_archive)에 저장, daily_byte.html 에
함께 표시. market.html 에서 Daily Byte 로 연결.

systemd: us-market-daily.timer (07:30 KST Mon-Fri) → us-market-daily.service.
수동 실행: cd ~/stock && .venv/bin/python -m bot.us_market_daily
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.us_market_daily")

_KST = timezone(timedelta(hours=9))

# ── yfinance 데이터 수집 ────────────────────────────────────────────────────

_INDICES = {
    "S&P 500": "^GSPC",
    "NASDAQ": "^IXIC",
    "Dow Jones": "^DJI",
    "Russell 2000": "^RUT",
    "VIX": "^VIX",
}

_SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication": "XLC",
}

_MAG7 = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "NVDA": "NVIDIA", "META": "Meta", "TSLA": "Tesla",
}

_BOND_FX = {
    "US 10Y": "^TNX",
    "US 2Y": "^IRX",
    "DXY": "DX-Y.NYB",
    "Gold": "GC=F",
    "WTI": "CL=F",
}


def _now_kst() -> datetime:
    return datetime.now(_KST)


def collect_us_data() -> dict:
    """Fetch US market snapshot via yfinance. Returns structured dict."""
    import yfinance as yf

    result: dict = {"indices": {}, "sectors": {}, "mag7": {}, "bonds_fx": {}}

    def _snap(ticker: str) -> dict | None:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            prev = info.get("regularMarketPreviousClose") or info.get("previousClose")
            chg = None
            if price and prev and prev > 0:
                chg = round((price - prev) / prev * 100, 2)
            return {"price": price, "prev": prev, "chg": chg}
        except Exception:
            return None

    def _batch_snap(mapping: dict) -> dict:
        out = {}
        for name, ticker in mapping.items():
            s = _snap(ticker)
            if s and s.get("price") is not None:
                out[name] = s
        return out

    result["indices"] = _batch_snap(_INDICES)
    result["sectors"] = _batch_snap(_SECTOR_ETFS)
    result["mag7"] = _batch_snap(_MAG7)
    result["bonds_fx"] = _batch_snap(_BOND_FX)

    # S&P 500 per-stock 수급 프록시 (KR Daily Byte 미러, 2026-06-10) — 실패해도
    # 지수/섹터 브리프는 정상 (graceful).
    try:
        result["movers"] = collect_sp500_movers()
    except Exception as exc:
        log.warning("us_market_daily: movers collection failed: %s", exc)
        result["movers"] = {}

    return result


# ── S&P 500 per-stock 수급 프록시 (KR Daily Byte 미러, 2026-06-10) ──────────
# KR Daily Byte 의 핵심은 pykrx 투자주체별 수급을 Python 이 정확 산출(환각 0)
# 하고 Pro 는 내러티브만 하는 것. 미국엔 무료 투자주체 데이터가 없으므로
# 등가물 = breadth(상승종목 비율) + 등락 상하위 + 거래대금 + **거래량 서지**
# (당일 거래량 / 직전 5일 평균 — 기관성 자금 유입 프록시) + 양→음 전환.
# S&P 500 유니버스(stock_screener 7d 캐시 재사용)를 yf.download 벌크 1콜로.

_MIN_DOLLAR_VOL_M = 200.0   # $200M 미만 거래대금은 랭킹 제외 (노이즈 컷)
_SURGE_MIN = 1.8            # 거래량 서지 임계 (당일 / 5일 평균)
_TOP_N = 12


def collect_sp500_movers() -> dict:
    """S&P 500 전 종목 6일 일봉 벌크 fetch → 당일 등락/거래대금/거래량 서지/
    양→음 전환/breadth 를 Python 이 정확 산출. 반환 dict 의 수치가 그대로
    프롬프트에 들어간다 (Pro 재계산 금지 directive)."""
    import yfinance as yf
    try:
        from bot.stock_screener import _get_us_universe
        universe = _get_us_universe()
    except Exception as exc:
        log.warning("us_market_daily: universe fetch failed: %s", exc)
        return {}
    if not universe:
        return {}

    df = yf.download(universe, period="6d", interval="1d",
                     group_by="ticker", threads=True,
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        return {}

    rows: list[dict] = []
    for tk in universe:
        try:
            sub = df[tk] if tk in df.columns.get_level_values(0) else None
            if sub is None:
                continue
            closes = sub["Close"].dropna()
            vols = sub["Volume"].dropna()
            if len(closes) < 3 or len(vols) < 3:
                continue
            c0, c1, c2 = float(closes.iloc[-1]), float(closes.iloc[-2]), float(closes.iloc[-3])
            if c1 <= 0 or c2 <= 0:
                continue
            chg = (c0 - c1) / c1 * 100.0          # 당일 등락 %
            chg_prev = (c1 - c2) / c2 * 100.0     # 전일 등락 % (양→음 전환 감지)
            v0 = float(vols.iloc[-1])
            v_hist = vols.iloc[:-1].tail(5)
            v_avg = float(v_hist.mean()) if len(v_hist) else 0.0
            surge = (v0 / v_avg) if v_avg > 0 else None
            dollar_m = c0 * v0 / 1e6              # 거래대금 $M
            rows.append({
                "ticker": tk, "chg": round(chg, 2), "chg_prev": round(chg_prev, 2),
                "dollar_m": round(dollar_m, 1),
                "surge": round(surge, 2) if surge else None,
            })
        except Exception:
            continue

    if len(rows) < 50:   # 벌크 응답이 사실상 빈 경우 — 부분 데이터로 오도 방지
        log.warning("us_market_daily: movers rows too few (%d) — skip", len(rows))
        return {}

    up = sum(1 for r in rows if r["chg"] > 0)
    breadth = round(up / len(rows) * 100.0, 1)
    avg_chg = round(sum(r["chg"] for r in rows) / len(rows), 2)

    liquid = [r for r in rows if r["dollar_m"] >= _MIN_DOLLAR_VOL_M]
    gainers = sorted(liquid, key=lambda r: r["chg"], reverse=True)[:_TOP_N]
    losers = sorted(liquid, key=lambda r: r["chg"])[:_TOP_N]
    by_dollar = sorted(rows, key=lambda r: r["dollar_m"], reverse=True)[:_TOP_N]
    surges = sorted(
        (r for r in liquid if r.get("surge") and r["surge"] >= _SURGE_MIN
         and r["chg"] > 0),
        key=lambda r: r["surge"], reverse=True)[:_TOP_N]
    # 양→음 전환: 전일 +2% 이상 → 당일 -1% 이하 (단기 추세 반전 경고)
    reversals = sorted(
        (r for r in liquid if r["chg_prev"] >= 2.0 and r["chg"] <= -1.0),
        key=lambda r: r["chg"])[:8]

    return {
        "n": len(rows), "breadth_pct": breadth, "avg_chg": avg_chg,
        "gainers": gainers, "losers": losers, "by_dollar": by_dollar,
        "surges": surges, "reversals": reversals,
    }


def _format_data_for_prompt(data: dict) -> str:
    """Format collected data into structured text for Pro prompt."""
    parts: list[str] = []

    parts.append("=== 주요 지수 ===")
    for name, s in data.get("indices", {}).items():
        chg = f"{s['chg']:+.2f}%" if s.get("chg") is not None else "N/A"
        parts.append(f"  {name}: {s['price']:,.2f} ({chg})")

    parts.append("\n=== 섹터 ETF 등락률 ===")
    sectors = sorted(data.get("sectors", {}).items(),
                     key=lambda x: x[1].get("chg") or 0, reverse=True)
    for name, s in sectors:
        chg = f"{s['chg']:+.2f}%" if s.get("chg") is not None else "N/A"
        parts.append(f"  {name}: {chg}")

    parts.append("\n=== Mag-7 ===")
    for ticker, s in data.get("mag7", {}).items():
        chg = f"{s['chg']:+.2f}%" if s.get("chg") is not None else "N/A"
        name = _MAG7.get(ticker, ticker)
        parts.append(f"  {name} ({ticker}): ${s['price']:,.2f} ({chg})")

    parts.append("\n=== 채권 / 환율 / 원자재 ===")
    for name, s in data.get("bonds_fx", {}).items():
        if s.get("price") is not None:
            parts.append(f"  {name}: {s['price']:,.2f}")

    # ── S&P 500 per-stock 수급 프록시 블록 (없으면 통째 생략 — graceful) ──
    mv = data.get("movers") or {}
    if mv.get("n"):
        def _row(r: dict, with_prev: bool = False) -> str:
            surge = f" · 거래량 {r['surge']:.1f}x(5일평균 대비)" if r.get("surge") else ""
            prev = f" · 전일 {r['chg_prev']:+.2f}%" if with_prev else ""
            return (f"  {r['ticker']}: {r['chg']:+.2f}%{prev}"
                    f" · 거래대금 ${r['dollar_m']:,.0f}M{surge}")

        parts.append(
            f"\n=== S&P 500 breadth (전 종목 {mv['n']}개 Python 산출) ===\n"
            f"  상승종목 비율: {mv['breadth_pct']}% · 평균 등락: {mv['avg_chg']:+.2f}%")
        if mv.get("gainers"):
            parts.append("\n=== 당일 상승 상위 (거래대금 $200M+ 필터) ===")
            parts += [_row(r) for r in mv["gainers"]]
        if mv.get("losers"):
            parts.append("\n=== 당일 하락 상위 (거래대금 $200M+ 필터) ===")
            parts += [_row(r) for r in mv["losers"]]
        if mv.get("by_dollar"):
            parts.append("\n=== 거래대금 상위 ===")
            parts += [_row(r) for r in mv["by_dollar"]]
        if mv.get("surges"):
            parts.append(
                "\n=== 거래량 서지 동반 상승 (당일 거래량 ≥ 직전 5일 평균의 "
                f"{_SURGE_MIN}x — 기관성 자금 유입 프록시) ===")
            parts += [_row(r) for r in mv["surges"]]
        if mv.get("reversals"):
            parts.append("\n=== 양→음 전환 (전일 +2%↑ → 당일 -1%↓) ===")
            parts += [_row(r, with_prev=True) for r in mv["reversals"]]

    return "\n".join(parts)


def build_prompt(data: dict) -> str:
    """Gemini Pro 프롬프트 — KR Daily Byte(daily_kr_flow._PROMPT) 8-섹션 구조
    미러 (사용자 2026-06-10 '한국 Daily Byte 참조해서 미국도 비슷하게').
    수치는 Python 산출분을 글자 그대로, Pro 는 섹터 그룹핑·로테이션 내러티브
    ·catalyst(web search)만. 미국엔 KR 투자주체(외인/기관) 데이터가 없으므로
    수급 프록시 = breadth·거래대금·거래량 서지임을 본문에 정직하게 전제."""
    data_block = _format_data_for_prompt(data)
    _now = _now_kst()
    today = _now.strftime("%Y-%m-%d")
    date_kr = f"{_now.year}년 {_now.month}월 {_now.day}일"
    has_movers = bool((data.get("movers") or {}).get("n"))
    movers_note = "" if has_movers else (
        "\n(오늘은 S&P 500 per-stock 데이터 수집이 실패해 지수/섹터/Mag-7 "
        "데이터만 제공됨 — 종목 단위 섹션은 Mag-7 범위로 간략히.)")

    return f"""당신은 미국 주식시장 전문 buy-side 애널리스트입니다. 아래는
오늘({today}) 미국 장 마감 후 yfinance 에서 직접 산출한 **정확한 수치**입니다.
이를 바탕으로 'Daily Byte' 미국 일일 브리프를 작성하세요.{movers_note}

{data_block}

---

작성 규칙:
1. **수치는 위 데이터를 글자 그대로 인용** — 재계산·반올림·창작 절대 금지.
   위에 없는 종목/수치를 지어내지 말 것. 등락률·거래대금·서지 배율·breadth
   도 위 값 그대로 사용.
2. **수급 프록시 해석**: 미국은 한국 같은 투자주체(외인/기관) 데이터가 없으
   므로 breadth(상승종목 비율)·거래대금·거래량 서지(5일평균 대비)가 자금
   흐름의 프록시. 거래량 서지 + 상승 동반 종목은 "기관성 자금 유입 신호"로,
   지수 상승 + breadth 약세는 "쏠림(narrow rally)"으로 해석해 서술.
3. **섹터 그룹핑 + 로테이션**: 섹터 ETF 등락 + 위 종목들을 산업/테마로 묶어
   (AI 반도체/소프트웨어/금융/에너지/헬스케어/소비 등) 유출→유입 방향 명시.
   종목→섹터 분류는 당신의 지식 + web search 로.
4. **catalyst 맥락 (web search 활용)**: 상위 종목의 당일 실적/공시/뉴스를
   web search 로 확인해 "왜 이 자금이 들어왔나" 맥락 + 구체 날짜 제공.
   **출처 날짜는 반드시 {today} 이하** — 미래 날짜 citation 금지. 확인 안
   되면 '맥락 미확인' 명시, 추측 catalyst 창작 금지.
5. **중립 표현**: "주목 종목"은 데이터 관찰일 뿐 BUY/SELL 권고가 아님.
   '매수 추천'/'목표가' 같은 직접 투자권유 표현 금지.
6. **구조** (각 섹션 지정 이모지로 시작하는 헤더 한 줄):
   📊 시장 총평 (지수 + breadth % + VIX·금리 — 자금 흐름 관점 해석)
   🔥 지금 강한 섹터·종목 (섹터 ETF 상위 + 거래량 서지 동반 상승 종목 우선)
   🔄 섹터 로테이션 (유출 섹터 → 유입 섹터, ETF 등락 명시)
   💰 거래대금 상위 동향 (Mag-7 포함 — 등락률 병기, 손바뀜/쏠림 해석)
   📈 주목할 수급 패턴 (거래량 서지 / 신규 모멘텀 / breadth 다이버전스)
   🏆 주목 종목 5선 (각 종목: 등락 + 거래대금 + 서지 + catalyst, 중립)
   ⚠️ 경고 시그널 (양→음 전환 종목 + VIX/금리/달러 리스크 + 향후 1주 일정)
   🎯 한 줄 결론 (포지셔닝 관점, 중립)
7. **본문은 일반 텍스트** + 섹션마다 위 지정 이모지로 시작하는 헤더 한 줄.
   **굵게(`**`)는 섹션 헤더·핵심 수치에만 최소 사용** — catalyst·맥락·설명
   문장은 일반 텍스트(굵게 금지). `---` 같은 수평선/구분선 절대 쓰지 말 것.
   HTML 태그·`<br>`·`<ul>` 금지. 각 항목 줄바꿈.
8. 분량: 한국 Daily Byte 벤치마크 수준의 정보 밀도로 충실하게. 데이터에
   있는 종목만 다룰 것.

제목: 첫 줄은 정확히 "{date_kr} 미국 증시 데일리 브리프" 한 줄 (다른 형식
금지 — 한국 Daily Byte 와 제목 형식 통일, 사용자 정책 2026-06-11). 면책·
디스클레이머("투자 권유 아님"·"교육·정보 목적" 등) 문구는 본문 어디에도
포함하지 말 것 — 채널·페이지 차원에서 별도 표기됨.
"""


# ── 비용 로깅 + 아카이브 ────────────────────────────────────────────────────
_HOME = os.path.expanduser("~")
_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "daily_byte_archive")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")
_USD_TO_KRW_FALLBACK = 1330.0


def _log_usage(pt: int, ot: int, cost_krw: float) -> None:
    """Log to usage.jsonl with subsystem='daily_byte' (KR Daily Byte 와 동일 버킷)."""
    import json as _json
    import time as _time
    try:
        os.makedirs(os.path.dirname(_NOAH_USAGE_LOG), exist_ok=True)
        try:
            from bot.screener import _USD_TO_KRW as _fx
        except Exception:
            _fx = _USD_TO_KRW_FALLBACK
        rec_noah = {
            "ts": _time.time(), "type": "llm_call", "model": "gemini-2.5-pro",
            "prompt_tokens": pt, "completion_tokens": ot,
            "cost_usd": round(cost_krw / _fx, 6), "subsystem": "daily_byte",
        }
        with open(_NOAH_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec_noah, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("us_market_daily: usage log write failed: %s", exc)


def _save_archive(body: str, cost_krw: float, elapsed_sec: float = 0.0) -> str | None:
    """Write → ~/.tradingagents/daily_byte_archive/YYYY-MM-DD/HHMMSS_us_daily_byte.json"""
    import json as _json
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = os.path.join(_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, f"{now:%H%M%S}_us_daily_byte.json")
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "date": date_iso,
            "kind": "us_daily",
            "body": body,
            "cost_krw": round(cost_krw, 4),
            "elapsed_sec": round(elapsed_sec, 1),
        }
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(rec, f, ensure_ascii=False)
        return path
    except Exception as exc:
        log.warning("us_market_daily: archive write failed: %s", exc)
        return None


def _post_process(text: str) -> str:
    """Post-process Pro output: strip future citations, HTML-safe."""
    import html as _html
    import re
    date_iso = _now_kst().date().isoformat()
    try:
        from bot.screener import _strip_future_dated_citations, _strip_invalid_dates
        text, _ = _strip_future_dated_citations(text, date_iso)
        text, _ = _strip_invalid_dates(text)
    except Exception:
        pass
    text = _html.unescape(text)
    text = re.sub(r"</?[a-zA-Z][^>\n]*?>", "", text)
    text = _html.escape(text, quote=False)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^\s*[\*\-]\s+", "• ", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s*([^\n]+)$", r"<b>\1</b>", text)
    text = re.sub(r"(?m)^[^\w\n]*[-*_]{2,}[^\w\n]*$", "", text)
    # 면책/디스클레이머 줄 제거 — KR _post_process 와 동일 백스톱
    # (사용자 정책 2026-06-11).
    text = re.sub(r"(?m)^(?=.*투자\s*권유)(?=.*아(?:님|닙)).*$", "", text)
    text = re.sub(r"(?m)^\s*면책.*$", "", text)
    _hdr = "|".join(("📊", "🔥", "🔄", "💰", "📈", "🏆", "⚠️", "🎯"))
    text = re.sub(rf"(?m)^[ \t]+(?=(?:{_hdr}))", "", text)
    text = re.sub(rf"(?m)^(?=(?:{_hdr}))", "\n", text)
    text = re.sub(rf"(?m)^((?:{_hdr})[^\n]*)$", r"<b>\1</b>", text)
    text = re.sub(rf"(?m)^(<b>(?:{_hdr})[^\n]*</b>)\n+(?=\S)(?!<b>(?:{_hdr}))", r"\1\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def generate() -> tuple[str, float] | None:
    """US 데이터 fetch → Pro narrate → guard → archive → (본문, cost_krw).
    데이터 없거나 키 부재 시 None."""
    import time as _time
    _t0 = _time.monotonic()
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("us_market_daily: GOOGLE_API_KEY missing")
        return None

    data = collect_us_data()
    if not data.get("indices"):
        log.warning("us_market_daily: no index data collected")
        return None

    from bot.screener import _call_pro, _USD_TO_KRW
    _PRO_IN, _PRO_OUT = 1.25, 10.00
    prompt = build_prompt(data)
    try:
        raw, pt, ot = _call_pro(api_key, prompt, enable_grounding=True)
    except Exception as exc:
        log.exception("us_market_daily: Pro call failed: %s", exc)
        return None
    if not raw:
        return None

    body = _post_process(raw)
    cost_krw = (pt * _PRO_IN + ot * _PRO_OUT) / 1e6 * _USD_TO_KRW
    _log_usage(pt, ot, cost_krw)

    elapsed = _time.monotonic() - _t0
    _save_archive(body, cost_krw, elapsed_sec=elapsed)

    try:
        from bot.dashboard import regenerate_daily_byte_index, regenerate_market_index
        regenerate_daily_byte_index()
        regenerate_market_index()
    except Exception as exc:
        log.warning("us_market_daily: dashboard regen failed: %s", exc)

    title = f"🇺🇸 <b>미국 Daily Byte - {_now_kst().strftime('%Y.%m.%d')}</b>"
    full = f"{title}\n<i>미국 장 마감 후 시장 브리프 · 생성 {_now_kst():%H:%M} KST</i>\n\n{body}"
    return full, cost_krw


# ── Telegram push ────────────────────────────────────────────────────────────

_TG_LIMIT = 4096
_CHUNK = 3800


def _chunk(text: str) -> list[str]:
    if len(text) <= _CHUNK:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > _CHUNK:
            if cur:
                out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def push_telegram_sync(text: str) -> bool:
    """Push text to NOAH channel via httpx (sync, standalone service 호환)."""
    import httpx
    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or os.environ.get("STANDARDVIEW_TELEGRAM_TOKEN", "").strip()
    )
    raw_ids = os.environ.get("CHANNEL_CHAT_IDS", "").strip()
    chat_ids = [c.strip() for c in raw_ids.split(",") if c.strip()]
    if not token or not chat_ids:
        log.error("us_market_daily: TELEGRAM_BOT_TOKEN / CHANNEL_CHAT_IDS missing")
        return False
    api = f"https://api.telegram.org/bot{token}"
    chunks = _chunk(text)
    ok_all = True
    for chat in chat_ids:
        for i, msg in enumerate(chunks):
            params = {"chat_id": chat, "text": msg,
                      "parse_mode": "HTML", "disable_web_page_preview": True}
            try:
                r = httpx.post(f"{api}/sendMessage", json=params, timeout=20)
                if r.status_code != 200:
                    import re
                    params.pop("parse_mode", None)
                    params["text"] = re.sub(r"<[^>]+>", "", msg)
                    r = httpx.post(f"{api}/sendMessage", json=params, timeout=20)
                    if r.status_code != 200:
                        log.warning("us_market_daily: chunk %d → %d %s",
                                    i + 1, r.status_code, r.text[:160])
                        ok_all = False
            except Exception as exc:
                log.warning("us_market_daily: push chunk %d failed: %s", i + 1, exc)
                ok_all = False
    return ok_all


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import dotenv
    dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    result = generate()
    if result:
        body, cost = result
        push_telegram_sync(body)
        print(body[:500])
        print(f"\n--- cost: ₩{cost:.1f}")
    else:
        print("No data or missing key.")
