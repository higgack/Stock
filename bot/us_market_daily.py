"""US Market Daily — 미국 장 마감 후 시장 브리프 (07:00 KST, 텔레그램 + 대시보드).

yfinance 주요 지수/섹터 데이터 + Gemini Pro (web search grounding) 내러티브.
Daily Byte KR 과 쌍으로 market.html 우측 카드에 표시.

systemd: us-market-daily.timer (07:00 KST Mon-Fri) → us-market-daily.service.
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

    return result


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

    return "\n".join(parts)


def build_prompt(data: dict) -> str:
    """Build the Gemini Pro prompt for US market narrative."""
    data_block = _format_data_for_prompt(data)
    today = _now_kst().strftime("%Y-%m-%d")

    return f"""당신은 미국 주식시장 전문 애널리스트입니다.
아래 데이터는 오늘({today}) 미국 장 마감 후 yfinance 에서 수집한 정확한 수치입니다.
이 수치를 **글자 그대로** 인용하고, 절대 반올림하거나 변경하지 마세요.

{data_block}

위 데이터를 바탕으로 한국어 미국 시장 데일리 브리프를 작성하세요.

[형식 규칙]
1. 다음 6개 섹션을 이모지 헤더로 구분:
   📊 시장 총평 (지수 등락 + 한 줄 요약)
   🔥 섹터 로테이션 (강세/약세 섹터 + 원인)
   💰 Mag-7 동향 (주요 변동 종목 + catalyst)
   📈 채권·환율·원자재 (금리/DXY/금/유가 변동 의미)
   ⚠️ 리스크 & 이벤트 (향후 1주 주요 일정: FOMC/고용/실적 등)
   🎯 결론 (내일/이번 주 관전 포인트 2-3줄)
2. 각 섹션은 3-5줄로 간결하게.
3. 모든 수치는 위 데이터에서 **글자 단위 copy**. 제공되지 않은 수치는 fabrication 금지.
4. web search 로 오늘의 catalyst / 뉴스 / 일정을 보완하되, 미래 날짜 citation 금지.
5. 중립적 정보 전달 (매수/매도 권고 금지).
6. markdown **bold** 는 핵심 수치 · 종목명에만 사용.
7. 총 분량 1500자 내외.
"""


# ── 비용 로깅 + 아카이브 ────────────────────────────────────────────────────
_HOME = os.path.expanduser("~")
_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "us_market_daily_archive")
_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "us_market_daily_usage.jsonl")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")
_USD_TO_KRW_FALLBACK = 1330.0


def _log_usage(pt: int, ot: int, cost_krw: float) -> None:
    """Dual-log: us_market_daily_usage.jsonl + usage.jsonl (subsystem='market_daily')."""
    import json as _json
    import time as _time
    try:
        os.makedirs(os.path.dirname(_USAGE_LOG), exist_ok=True)
        now = _now_kst()
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "month": now.date().isoformat()[:7],
            "prompt_tok": pt, "output_tok": ot,
            "cost_krw": round(cost_krw, 4),
        }
        with open(_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("us_market_daily: usage log write failed: %s", exc)
    try:
        os.makedirs(os.path.dirname(_NOAH_USAGE_LOG), exist_ok=True)
        try:
            from bot.screener import _USD_TO_KRW as _fx
        except Exception:
            _fx = _USD_TO_KRW_FALLBACK
        rec_noah = {
            "ts": _time.time(), "type": "llm_call", "model": "gemini-2.5-pro",
            "prompt_tokens": pt, "completion_tokens": ot,
            "cost_usd": round(cost_krw / _fx, 6), "subsystem": "market_daily",
        }
        with open(_NOAH_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec_noah, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("us_market_daily: NOAH usage log write failed: %s", exc)


def _save_archive(body: str, cost_krw: float, elapsed_sec: float = 0.0) -> str | None:
    """Write → ~/.tradingagents/us_market_daily_archive/YYYY-MM-DD/HHMMSS_us_market_daily.json"""
    import json as _json
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = os.path.join(_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, f"{now:%H%M%S}_us_market_daily.json")
        rec = {
            "ts": now.isoformat(timespec="seconds"),
            "date": date_iso,
            "kind": "daily",
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
    _hdr = "|".join(("📊", "🔥", "💰", "📈", "⚠️", "🎯"))
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
        from bot.dashboard import regenerate_market_index
        regenerate_market_index()
    except Exception as exc:
        log.warning("us_market_daily: market.html regen failed: %s", exc)

    title = f"🇺🇸 <b>US Market Daily - {_now_kst().strftime('%Y.%m.%d')}</b>"
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


async def push_telegram(application) -> None:
    """Generate US market daily and push to NOAH channel."""
    result = generate()
    if result is None:
        log.info("us_market_daily: skip (no data or key)")
        return
    full, cost_krw = result
    try:
        from bot.telegram_bot import CHANNEL_CHAT_IDS
        chat_ids = CHANNEL_CHAT_IDS
    except Exception:
        chat_ids = []
    if not chat_ids:
        log.warning("us_market_daily: no channel chat IDs configured")
        return

    for cid in chat_ids:
        for chunk in _chunk(full):
            try:
                await application.bot.send_message(
                    chat_id=cid, text=chunk, parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as exc:
                log.warning("us_market_daily: push to %s failed: %s", cid, exc)
                try:
                    await application.bot.send_message(
                        chat_id=cid, text=chunk, disable_web_page_preview=True,
                    )
                except Exception:
                    pass

    log.info("us_market_daily: pushed to %d channels (₩%.1f)", len(chat_ids), cost_krw)


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import dotenv
    dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    result = generate()
    if result:
        body, cost = result
        print(body[:500])
        print(f"\n--- cost: ₩{cost:.1f}")
    else:
        print("No data or missing key.")
