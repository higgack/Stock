"""VN Market Daily — 베트남 장 마감 후 시장 브리프 (16:30 KST, 텔레그램 + 대시보드).

Gemini Pro (web search grounding) 내러티브 only — yfinance 가 VN-Index 미지원이라
모든 데이터를 web search 로 수집.
Daily Byte KR / US Market Daily 와 함께 market.html 카드에 표시.

systemd: vn-market-daily.timer (16:30 KST Mon-Fri) → vn-market-daily.service.
수동 실행: cd ~/stock && .venv/bin/python -m bot.vn_market_daily
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot.vn_market_daily")

_KST = timezone(timedelta(hours=9))
_USD_TO_KRW_FALLBACK = 1330.0


def _now_kst() -> datetime:
    return datetime.now(_KST)


def build_prompt() -> str:
    today = _now_kst().strftime("%Y-%m-%d")
    return f"""당신은 베트남 주식시장 전문 애널리스트입니다.
오늘({today}) 베트남 시장 데이터를 web search 로 수집하여 한국어 베트남 시장 데일리 브리프를 작성하세요.

[수집해야 할 데이터 — 반드시 web search 로 오늘의 실제 수치 확인]
- VN-Index (호치민), HNX (하노이), VN30 종가 + 등락률
- 외국인 순매수/순매도 금액 (VND)
- 거래대금 (호치민 + 하노이)
- 섹터별 동향 (은행, 부동산, IT, 에너지 등)
- 주요 대형주 (VIC, VHM, VNM, HPG, FPT, MBB, TCB, MSN, VCB, BID) 동향
- USD/VND 환율
- 주요 뉴스·정책 (정부 부양책, FDI, 인프라, 부동산 규제 등)

[형식 규칙]
1. 다음 5개 섹션을 이모지 헤더로 구분:
   📊 시장 총평 (VN-Index/HNX/VN30 등락 + 한 줄 요약)
   🏦 섹터 & 대형주 (강세/약세 섹터 + 주요 종목)
   💱 외국인 · 환율 (외인 순매수/순매도 + USD/VND)
   ⚠️ 리스크 & 이벤트 (정책·규제·매크로 이슈)
   🎯 결론 (관전 포인트 2-3줄)
2. 각 섹션은 3-5줄로 간결하게.
3. 검색으로 확인된 수치만 사용. 확인 안 되면 fabrication 금지, '확인 불가' 명시.
4. 미래 날짜 citation 금지.
5. 중립적 정보 전달 (매수/매도 권고 금지).
6. markdown **bold** 는 핵심 수치 · 종목명에만 사용.
7. 총 분량 1200자 내외.
"""


# ── 비용 로깅 + 아카이브 ────────────────────────────────────────────────────
_HOME = os.path.expanduser("~")
_ARCHIVE_DIR = os.path.join(_HOME, ".tradingagents", "vn_market_daily_archive")
_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "vn_market_daily_usage.jsonl")
_NOAH_USAGE_LOG = os.path.join(_HOME, ".tradingagents", "usage.jsonl")


def _log_usage(pt: int, ot: int, cost_krw: float) -> None:
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
        log.warning("vn_market_daily: usage log write failed: %s", exc)
    try:
        os.makedirs(os.path.dirname(_NOAH_USAGE_LOG), exist_ok=True)
        try:
            from bot.screener import _USD_TO_KRW as _fx
        except Exception:
            _fx = _USD_TO_KRW_FALLBACK
        rec_noah = {
            "ts": _time.time(), "type": "llm_call", "model": "gemini-2.5-pro",
            "prompt_tokens": pt, "completion_tokens": ot,
            "cost_usd": round(cost_krw / _fx, 6), "subsystem": "vn_market_daily",
        }
        with open(_NOAH_USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec_noah, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("vn_market_daily: NOAH usage log write failed: %s", exc)


def _save_archive(body: str, cost_krw: float, elapsed_sec: float = 0.0) -> str | None:
    import json as _json
    try:
        now = _now_kst()
        date_iso = now.date().isoformat()
        day_dir = os.path.join(_ARCHIVE_DIR, date_iso)
        os.makedirs(day_dir, exist_ok=True)
        path = os.path.join(day_dir, f"{now:%H%M%S}_vn_market_daily.json")
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
        log.warning("vn_market_daily: archive write failed: %s", exc)
        return None


def _post_process(text: str) -> str:
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
    _hdr = "|".join(("📊", "🏦", "💱", "⚠️", "🎯"))
    text = re.sub(rf"(?m)^[ \t]+(?=(?:{_hdr}))", "", text)
    text = re.sub(rf"(?m)^(?=(?:{_hdr}))", "\n", text)
    text = re.sub(rf"(?m)^((?:{_hdr})[^\n]*)$", r"<b>\1</b>", text)
    text = re.sub(rf"(?m)^(<b>(?:{_hdr})[^\n]*</b>)\n+(?=\S)(?!<b>(?:{_hdr}))", r"\1\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def generate() -> tuple[str, float] | None:
    """Pro web search → guard → archive → (본문, cost_krw). 키 부재 시 None."""
    import time as _time
    _t0 = _time.monotonic()
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("vn_market_daily: GOOGLE_API_KEY missing")
        return None

    from bot.screener import _call_pro, _USD_TO_KRW
    _PRO_IN, _PRO_OUT = 1.25, 10.00
    prompt = build_prompt()
    try:
        raw, pt, ot = _call_pro(api_key, prompt, enable_grounding=True)
    except Exception as exc:
        log.exception("vn_market_daily: Pro call failed: %s", exc)
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
        log.warning("vn_market_daily: market.html regen failed: %s", exc)

    title = f"🇻🇳 <b>VN Market Daily - {_now_kst().strftime('%Y.%m.%d')}</b>"
    full = f"{title}\n<i>베트남 장 마감 후 시장 브리프 · 생성 {_now_kst():%H:%M} KST</i>\n\n{body}"
    return full, cost_krw


# ── Telegram push (sync, httpx — standalone service 호환) ─────────────────

_CHUNK_LIMIT = 3800


def _chunk(text: str) -> list[str]:
    if len(text) <= _CHUNK_LIMIT:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > _CHUNK_LIMIT:
            if cur:
                out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def push_telegram(text: str) -> bool:
    import httpx
    token = (
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        or os.environ.get("STANDARDVIEW_TELEGRAM_TOKEN", "").strip()
    )
    raw_ids = os.environ.get("CHANNEL_CHAT_IDS", "").strip()
    chat_ids = [c.strip() for c in raw_ids.split(",") if c.strip()]
    if not token or not chat_ids:
        log.error("vn_market_daily: TELEGRAM_BOT_TOKEN / CHANNEL_CHAT_IDS missing")
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
                        log.warning("vn_market_daily: chunk %d → %d %s",
                                    i + 1, r.status_code, r.text[:160])
                        ok_all = False
            except Exception as exc:
                log.warning("vn_market_daily: push chunk %d failed: %s", i + 1, exc)
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
        push_telegram(body)
        print(body[:500])
        print(f"\n--- cost: ₩{cost:.1f}")
    else:
        print("Missing GOOGLE_API_KEY.")
