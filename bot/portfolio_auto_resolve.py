"""해외 종목 한글명 → 티커 자동 매칭 (Gemini Flash + yfinance 검증, 영구 캐시).

portfolio_resolve._OVERSEAS_ALIAS 에 없는 해외 종목명을 Flash 배치 번역 →
yfinance 검증 → 영구 디스크 캐시. 종목당 1회 ₩1-2 (Flash), 캐시 후 ₩0.
GOOGLE_API_KEY 부재 시 graceful skip.
"""
from __future__ import annotations

import json
import logging
import os
from bot.genai_factory import effective_key as _effective_key
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

_HOME = Path.home() / ".tradingagents"
# v2(2026-10-14) — 신원 확인(원천 이름 ↔ 모델이 말한 회사) 이전 판의 캐시엔 국내 비상장 → 무관한 미국 티커가
# 영구로 남아 있다(SK에코플랜트 → SKHY). 새 파일에서 한 번씩 다시 묻는다(종목당 ₩1~2 · #18 구워진 값은 안 바뀐다).
_CACHE_PATH = _HOME / "portfolio_auto_aliases_v2.json"
_USAGE = _HOME / "usage.jsonl"
_FLASH_IN, _FLASH_OUT = 0.30, 2.50
_MAX_BATCH = 30

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = json.loads(_CACHE_PATH.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _cache = {}
    return _cache


def _save() -> None:
    if _cache is None:
        return
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, _CACHE_PATH)
    except OSError:
        pass


def _log_usage(pt: int, ot: int) -> None:
    try:
        cost_usd = (pt * _FLASH_IN + ot * _FLASH_OUT) / 1e6
        with open(_USAGE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "type": "llm_call",
                                "model": "gemini-2.5-flash",
                                "prompt_tokens": pt, "completion_tokens": ot,
                                "cost_usd": round(cost_usd, 6),
                                "subsystem": "portfolio"}) + "\n")
    except OSError:
        pass


def _verify_yfinance(ticker: str, company: str = "") -> str | None:
    """yfinance 로 티커 검증 → market 또는 None. 값이 있는 것만으로는 부족하다 — 그 티커의 원천 이름이 모델이 말한
    회사(`company`)와 같은 회사여야 한다(국내 비상장 SK에코플랜트가 무관한 미국 SKHY 로 붙었다, 2026-10-14).
    이름을 못 받으면 확인 불가 = None(틀린 티커보다 미매칭이 낫다 — 화면은 이름과 금액을 그대로 보여 준다)."""
    from bot.portfolio_resolve import same_company
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.fast_info
        if not (getattr(info, "last_price", None) or
                getattr(info, "previous_close", None)):
            return None
        meta = t.get_info() or {}
        got = str(meta.get("longName") or meta.get("shortName") or "")
        if not same_company(company, got):
            log.info("auto_resolve: %s 이름 %r ≠ %r — 버림", ticker, got, company)
            return None
        return "US"
    except Exception:
        return None


def batch_resolve(names: list[str]) -> dict[str, tuple[str, str] | None]:
    """이름 목록 → {정규화키: (ticker, market) | None}. 캐시 우선, 미캐시만 Flash.

    resolve_ticker() 에서 미매칭된 이름만 넘겨야 함 (KR/ETF 는 이미 처리).
    """
    from bot.portfolio_resolve import _normalize

    cache = _load()
    result: dict[str, tuple[str, str] | None] = {}
    todo: list[tuple[str, str]] = []

    for name in names:
        key = _normalize(name)
        if not key or len(key) < 2:
            continue
        if key in cache:
            v = cache[key]
            result[key] = tuple(v) if v else None
            continue
        todo.append((key, name))

    if not todo:
        return result

    api_key = _effective_key()
    if not api_key:
        for key, _ in todo:
            result[key] = None
        return result

    batch = todo[:_MAX_BATCH]
    lines = "\n".join(f"{i+1}. {orig}" for i, (_, orig) in enumerate(batch))
    prompt = (
        "다음은 한국 증권사 계좌의 종목명입니다. 한국 상장주는 이미 걸렀으니, 미국 거래소에 상장된 해외 회사만 "
        "티커와 영문 회사명을 식별하세요.\n"
        "- 한글 음역(시놉시스=Synopsys=SNPS) 또는 영문(ROUNDHILL MEMORY=DRAM)\n"
        "- 결과는 JSON만: {\"시놉시스\": {\"ticker\": \"SNPS\", \"company\": \"Synopsys\"}, ...}\n"
        "- 한국 회사(비상장 포함 — 예: SK에코플랜트)·식별 불가·이름이 비슷한 다른 회사뿐이면 null\n"
        "- 설명 금지, JSON만 출력\n\n" + lines)

    guesses: dict[str, dict] = {}
    try:
        from bot.screener import _call_pro
        text, pt, ot = _call_pro(api_key, prompt, model="gemini-2.5-flash",
                                 enable_grounding=False)
        _log_usage(pt, ot)
        clean = (text or "").strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        raw = json.loads(clean)
        if isinstance(raw, dict):           # 옛 형식(티커 문자열만)은 회사명이 없어 신원을 확인할 수 없다 — 받지 않는다
            guesses = {k: v for k, v in raw.items() if isinstance(v, dict)}
    except Exception as e:
        log.info("auto_resolve flash: %s", e)

    for key, orig in batch:
        g = guesses.get(orig) or {}
        ticker, company = g.get("ticker"), str(g.get("company") or "")
        if not isinstance(ticker, str) or not ticker:
            cache[key] = None
            result[key] = None
            continue
        ticker = ticker.strip().upper()
        if not re.match(r"^[A-Z0-9.-]{1,10}$", ticker):
            cache[key] = None
            result[key] = None
            continue
        mkt = _verify_yfinance(ticker, company)
        if mkt:
            cache[key] = [ticker, mkt]
            result[key] = (ticker, mkt)
            log.info("auto_resolve: %r → %s", orig, ticker)
        else:
            cache[key] = None
            result[key] = None
            log.info("auto_resolve: %r → %s (verify failed)", orig, ticker)

    _save()
    return result
