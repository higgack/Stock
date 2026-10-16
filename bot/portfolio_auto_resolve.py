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
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_HOME = Path.home() / ".tradingagents"
# v2(2026-10-14) — 신원 확인(원천 이름 ↔ 모델이 말한 회사) 이전 판의 캐시엔 국내 비상장 → 무관한 미국 티커가
# 영구로 남아 있다(SK에코플랜트 → SKHY). 새 파일에서 한 번씩 다시 묻는다(종목당 ₩1~2 · #18 구워진 값은 안 바뀐다).
_CACHE_PATH = _HOME / "portfolio_auto_aliases_v2.json"
_USAGE = _HOME / "usage.jsonl"
_FLASH_IN, _FLASH_OUT = 0.30, 2.50
_MAX_BATCH = 30
_KST = timezone(timedelta(hours=9))

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


# 실패 기록(2026-10-16 사용자 "ASML·아날로그 디바이시스·에버스핀·에흐르 는 티커가 확실히 있는데 왜 미매칭?").
# 옛 판은 AI 호출 실패·응답 해석 실패·Yahoo 이름 조회 실패까지 **영구** None 으로 구워, 한 번 실패한 종목은 다시
# 묻지 않았고 사유도 남지 않았다(#161·#303 · #82). 이제 캐시 값은 셋 중 하나다:
#   [ticker, market]                      — 매칭
#   {"why", "final": True, "ts"}          — 답을 받고 판정한 실패(AI 가 해외 상장사가 아니라 함 · Yahoo 이름이 다른
#                                           회사 · 미국 밖 티커) · _FINAL_SEC 뒤 다시 묻는다. AI 의 답은 확률적이라
#                                           '다시 물어도 같은 답' 이 아니다 — 영구로 굽지 않는다(독립 리뷰 2026-10-16)
#   {"why", "final": False, "ts"}         — 일시 실패 · _RETRY_SEC 뒤 다시 묻는다(비용 상한 = 하루 4회 × ₩1~2)
# 사유 없는 옛 None(v2 판)은 무엇으로 실패했는지 모르므로 한 번 다시 묻는다.
_RETRY_SEC = 6 * 3600
_FINAL_SEC = 30 * 86400
# 확인 단계(Yahoo) 벽시계 예산 — portfolio-watch 는 TimeoutStartSec=180 이라 넘기면 Flash 값은 냈는데 아무것도 안
# 남는다. 예산 밖 종목은 일시 실패로 남긴다(독립 리뷰 2026-10-16).
_VERIFY_BUDGET_SEC = 60.0


def _miss(why: str, *, final: bool, now: float) -> dict:
    return {"why": why, "final": final, "ts": now}


def entry_state(v, now: float | None = None) -> tuple[str, str]:
    """캐시 값 → (상태, 설명). 상태 = hit / final / wait / ask."""
    now = time.time() if now is None else now
    if isinstance(v, list) and len(v) == 2 and v[0]:
        return "hit", f"{v[0]} ({v[1]})"
    if isinstance(v, dict):
        why = str(v.get("why") or "사유 미기록")
        final = bool(v.get("final"))
        try:
            left = (_FINAL_SEC if final else _RETRY_SEC) - (now - float(v.get("ts")))
        except (TypeError, ValueError):
            return "ask", why + " · 시각 미기록 — 다시 묻는다"
        if left > 0:
            nxt = datetime.fromtimestamp(now + left, _KST).strftime("%m-%d %H:%M")
            return ("final" if final else "wait"), f"{why} · 다음 재시도 {nxt} KST"
        return "ask", why + " · 재시도 시각 지남"
    return "ask", "옛 판 실패 기록(사유 미기록) — 다시 묻는다"


def miss_reason(name: str) -> str:
    """미매칭 종목 이름 → 사람이 읽는 사유(캐시에 없으면 '아직 묻지 않음')."""
    from bot.portfolio_resolve import _normalize
    key = _normalize(name)
    if len(key) < 2:
        return "이름이 너무 짧아 AI 매칭을 묻지 않음"
    cache = _load()
    if key not in cache:
        # 키가 없으면 batch_resolve 는 기록 없이 돌아간다 — '아직' 이라 적으면 영영 오지 않을 약속이다(#380)
        return "AI 키가 없어 매칭을 묻지 못함" if not _effective_key() else "아직 AI 매칭을 묻지 않음"
    st, why = entry_state(cache[key])
    return why if st != "hit" else ""


def _verify_yfinance(ticker: str, company: str = "") -> tuple[str | None, str, bool]:
    """yfinance 로 티커 검증 → (market | None, 실패 사유, 최종 여부). 값이 있는 것만으로는 부족하다 — 그 티커의 원천
    이름이 모델이 말한 회사(`company`)와 같은 회사여야 한다(국내 비상장 SK에코플랜트가 무관한 미국 SKHY 로 붙었다,
    2026-10-14). 이름을 못 받으면 확인 불가 — 틀린 티커보다 미매칭이 낫지만 그건 **일시 실패**라 다시 묻는다
    (`get_info` 는 Yahoo 가 자주 거절한다 — 그때 검색 결과의 이름으로 한 번 더 본다)."""
    from bot.portfolio_resolve import same_company
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.fast_info
        if not (getattr(info, "last_price", None) or
                getattr(info, "previous_close", None)):
            return None, f"Yahoo 에 {ticker} 시세 없음", False
        got = ""
        try:
            meta = t.get_info() or {}
            got = str(meta.get("longName") or meta.get("shortName") or "")
        except Exception as exc:                               # noqa: BLE001 — 검색 이름으로 한 번 더
            log.info("auto_resolve: %s get_info 실패(%s) — 검색 이름으로", ticker, type(exc).__name__)
        if not got:
            try:
                for q in getattr(yf.Search(ticker, max_results=6), "quotes", None) or []:
                    if str(q.get("symbol") or "").upper() == ticker:
                        got = str(q.get("longname") or q.get("shortname") or "")
                        break
            except Exception as exc:                           # noqa: BLE001
                log.info("auto_resolve: %s 검색 실패(%s)", ticker, type(exc).__name__)
        if not got:
            return None, f"Yahoo 에서 {ticker} 회사 이름을 못 받음", False
        if not same_company(company, got):
            log.info("auto_resolve: %s 이름 %r ≠ %r — 버림", ticker, got, company)
            return None, f"{ticker} 의 Yahoo 이름 '{got}' 과 AI '{company}' 의 신원 낱말이 겹치지 않음", True
        return "US", "", True
    except Exception as exc:                                   # noqa: BLE001
        return None, f"Yahoo 확인 실패({type(exc).__name__})", False


def batch_resolve(names: list[str], now: float | None = None) -> dict[str, tuple[str, str] | None]:
    """이름 목록 → {정규화키: (ticker, market) | None}. 캐시 우선 — 매칭·최종 실패·재시도 대기 중인 일시 실패는
    묻지 않고, 나머지(처음 · 재시도 시각이 지난 일시 실패 · 사유 없는 옛 기록)만 Flash 에 묻는다.

    resolve_ticker() 에서 미매칭된 이름만 넘겨야 함 (KR/ETF 는 이미 처리).
    """
    from bot.portfolio_resolve import _normalize

    now = time.time() if now is None else now
    cache = _load()
    result: dict[str, tuple[str, str] | None] = {}
    todo: list[tuple[str, str]] = []

    for name in names:
        key = _normalize(name)
        if not key or len(key) < 2:
            continue
        if key in cache:
            st, _ = entry_state(cache[key], now)
            if st == "hit":
                result[key] = tuple(cache[key])
                continue
            if st in ("final", "wait"):
                result[key] = None
                continue
        if all(k != key for k, _ in todo):
            todo.append((key, name))

    if not todo:
        return result

    api_key = _effective_key()
    if not api_key:
        for key, _ in todo:          # 키가 없으면 묻지도 않았다 — 기록하지 않는다(키를 넣으면 바로 묻는다)
            result[key] = None
        return result

    batch = todo[:_MAX_BATCH]
    # 묻기 **전에** '조회 중' 을 기록한다 — 프로세스가 중간에 죽어도(유닛 시간 상한) 다음 주기가 같은 묶음을 곧바로
    # 다시 묻지 않는다(그러면 2분마다 Flash 값을 낸다, 독립 리뷰 2026-10-16)
    for key, _ in batch:
        cache[key] = _miss("조회 중 중단됨(프로세스가 끝나기 전에 결과를 못 남김)", final=False, now=now)
    _save()
    lines = "\n".join(f"{i+1}. {orig}" for i, (_, orig) in enumerate(batch))
    prompt = (
        "다음은 한국 증권사 계좌의 종목명입니다. 한국 상장주는 이미 걸렀으니, 미국 거래소에 상장된 해외 회사만 "
        "티커와 영문 회사명을 식별하세요.\n"
        "- 한글 음역(시놉시스=Synopsys=SNPS) 또는 영문(ROUNDHILL MEMORY=DRAM)\n"
        "- 결과는 JSON만: {\"시놉시스\": {\"ticker\": \"SNPS\", \"company\": \"Synopsys\"}, ...}\n"
        "- 키는 위 목록의 종목명을 그대로 쓰세요\n"
        "- 한국 회사(비상장 포함 — 예: SK에코플랜트)·식별 불가·이름이 비슷한 다른 회사뿐이면 null\n"
        "- 설명 금지, JSON만 출력\n\n" + lines)

    raw: dict | None = None
    fail = ""
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
        got = json.loads(clean)
        if isinstance(got, dict):
            raw = got
        else:
            fail = "AI 응답이 JSON 객체가 아님"
    except json.JSONDecodeError:
        fail = "AI 응답을 JSON 으로 못 읽음"
    except Exception as e:                                     # noqa: BLE001
        fail = f"AI 호출 실패({type(e).__name__})"
        log.info("auto_resolve flash: %s", e)

    # 모델이 키를 조금 바꿔 쓰는 일(괄호·공백·목록 번호 '1. ')은 정규화로 맞춘다 — 그래도 없으면 '응답에 없음'(일시 실패)
    by_norm = {_normalize(re.sub(r"^\s*\d+\s*[.)]\s*", "", str(k))): v
               for k, v in (raw or {}).items()} if raw is not None else {}
    t0 = time.monotonic()
    for key, orig in batch:
        if raw is None:
            cache[key] = _miss(fail, final=False, now=now)
            result[key] = None
            continue
        if orig in raw:
            g = raw[orig]
        elif key in by_norm:
            g = by_norm[key]
        else:
            cache[key] = _miss("AI 응답에 이 종목이 없음", final=False, now=now)
            result[key] = None
            continue
        if g is None:
            cache[key] = _miss("AI: 미국 상장 해외 회사로 식별 못 함(국내·비상장·식별 불가)", final=True, now=now)
            result[key] = None
            continue
        if not isinstance(g, dict):    # 옛 형식(티커 문자열만)은 회사명이 없어 신원을 확인할 수 없다 — 받지 않는다
            cache[key] = _miss("AI 응답 형식이 다름(회사명 없음)", final=False, now=now)
            result[key] = None
            continue
        ticker, company = g.get("ticker"), str(g.get("company") or "")
        if not isinstance(ticker, str) or not ticker or not company:
            cache[key] = _miss("AI 응답에 티커·회사명이 없음", final=False, now=now)
            result[key] = None
            continue
        ticker = ticker.strip().upper()
        if not re.match(r"^[A-Z0-9.-]{1,10}$", ticker):
            cache[key] = _miss(f"AI 티커 모양이 이상함({ticker[:12]})", final=False, now=now)
            result[key] = None
            continue
        if "." in ticker:                  # 7203.T·ASML.AS — 미국 밖 거래소 티커를 'US' 로 굽지 않는다(Yahoo 미국 표기는 '-')
            cache[key] = _miss(f"AI 가 미국 밖 거래소 티커를 줌({ticker})", final=True, now=now)
            result[key] = None
            continue
        if time.monotonic() - t0 > _VERIFY_BUDGET_SEC:
            cache[key] = _miss(f"Yahoo 확인 시간 예산 {_VERIFY_BUDGET_SEC:.0f}초 초과 — 다음에 다시", final=False, now=now)
            result[key] = None
            continue
        mkt, why, final = _verify_yfinance(ticker, company)
        if mkt:
            cache[key] = [ticker, mkt]
            result[key] = (ticker, mkt)
            log.info("auto_resolve: %r → %s", orig, ticker)
        else:
            cache[key] = _miss(why, final=final, now=now)
            result[key] = None
            log.info("auto_resolve: %r → %s (%s)", orig, ticker, why)
        _save()                            # 하나씩 남긴다 — 중간에 죽어도 확인한 것은 다시 안 묻는다

    _save()
    return result


def why(out=print) -> int:
    """읽기 전용 진단 — 지금 자산 기록의 미매칭 종목마다 자동 매칭 상태와 사유(AI·Yahoo 호출 없음)."""
    from bot.portfolio import load
    from bot.portfolio_resolve import _normalize
    model = load() or {}
    names = sorted({str(h.get("상품명") or "") for h in model.get("holdings") or []
                    if not h.get("matched") and not h.get("source") and h.get("상품명")})
    cache = _load()
    out(f"# portfolio_auto_resolve --why · 캐시 {_CACHE_PATH.name} {len(cache)}항목 · 재시도 간격 {_RETRY_SEC // 3600}시간")
    if not names:
        out("미매칭 종목 없음")
        return 0
    out(f"미매칭 {len(names)}종목:")
    for n in names:
        key = _normalize(n)
        st, why_ = entry_state(cache[key]) if key in cache else ("ask", "아직 AI 매칭을 묻지 않음")
        lab = {"hit": "✅ 캐시엔 매칭(다음 재매칭에 붙음)", "final": "⛔ 최종", "wait": "⏳ 대기",
               "ask": "🔁 다음 재매칭에 묻는다"}[st]
        out(f"  · {n} — {lab}: {why_}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(why() if "--why" in sys.argv[1:] else 2)
