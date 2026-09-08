"""trade-bot LLM 사용량·비용 기록 (경량 JSONL).

trade 봇은 오랫동안 'LLM 없음·토큰 과금 0'이었으나, 산업트렌드
🔍추가신호 박스(Gemini 2.5 Pro, 데이터 변동 시에만 ~월1회)부터 LLM을
쓴다. NOAH usage_tracker의 공개 가격표를 미러해 호출별 model·토큰·USD/
KRW를 ``~/.trade/usage.jsonl``에 적고 /cost·대시보드가 집계한다. 호출이
드물어 비용은 사실상 0이지만 '비용만 잘 트래킹되면 된다'는 운영자 조건을
충족하기 위해 전부 기록한다.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
USAGE_LOG = _DATA_DIR / "usage.jsonl"

# Gemini 2.5 공개 가격 — **정의는 `bot/usage_tracker.py` 한 곳**에 있고
# 여기선 그 객체를 그대로 참조한다(별칭이지 사본이 아니다). 예전엔 같은 표가
# 여기에도 리터럴로 있었다: 오늘은 한 자리도 안 틀렸지만 메인 비용카드가
# **둘을 합산**하므로 한쪽만 고치는 날 화면이 조용히 틀린다(#38). 회귀가
# "레포에 `_PRICING` 리터럴은 하나뿐" 을 전수로 고정한다(#24·#119).
from bot.usage_tracker import (  # noqa: E402
    _PRICING, KRW_PER_USD, estimate_cost_usd, is_priced,
)

# kg 관계후보 자동발굴 kind — 소스별(블로그/DART)로 분리해 각 대시보드가 자기
# 비용만 표시(메인은 합산). 'kg_candidate'=레거시(소스 미구분, 블로그로 취급).
KG_KINDS = ("kg_candidate", "kg_blog", "kg_dart")


def is_kg(kind: str | None) -> bool:
    """kg 관계후보 발굴 비용 여부 — 수출입(insight)과 분리 집계용."""
    return (kind or "").startswith("kg")


def cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    """USD 비용 — **계산도 단가표도 `bot.usage_tracker` 한 곳**에서 온다.

    ⚠️ 예전엔 여기에 `_PRICING` 리터럴과 산식이 따로 있었다. 오늘은 두 표가
    한 자리도 안 틀렸지만(실측) 메인 비용카드는 **둘을 합산**하므로, 단가를
    한쪽만 고치는 날 화면이 조용히 틀린다(#38·#119 규율을 구조로).
    캐시 토큰은 이 경로에서 안 쓰므로 0 — 값은 그대로다(실측: 두 식의 차이는
    부동소수 연산 순서뿐, 1e-20).
    """
    return estimate_cost_usd(model, int(in_tok or 0), int(out_tok or 0))


def record(model: str, in_tok: int, out_tok: int, *, kind: str = "insight",
           now: float | None = None) -> dict:
    """한 호출을 usage.jsonl에 append. 쓰기 실패는 경고만(과금 추적이
    파이프라인을 깨면 안 됨)."""
    now = now if now is not None else time.time()
    rec = {
        "ts": now, "kind": kind, "model": model,
        "in_tok": int(in_tok or 0), "out_tok": int(out_tok or 0),
        "cost_usd": round(cost_usd(model, in_tok or 0, out_tok or 0), 6),
    }
    if not is_priced(model):
        # ₩0 을 사실인 척 남기지 않는다 — 원장이 스스로 밝힌다(#43).
        rec["unpriced"] = True
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("usage log write failed: %s", exc)
    return rec


def _read() -> list[dict]:
    try:
        lines = USAGE_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def summary(now: float | None = None, *, kinds=None, exclude_kinds=None,
            exclude_kg: bool = False) -> dict:
    """today / 7d / 30d + today_kst / month / total 집계 (calls·tokens·
    cost_usd·cost_krw) + total_calls. today=롤링 24h(기존 소비자 보존),
    today_kst/month=KST 달력일·달력월(대시보드 비용카드 3창 표기 —
    오늘/이번 달/누적, 사용자 2026-07-05), total=전체 누적.
    kinds=세트 → 그 kind 만, exclude_kinds=세트 → 그 kind 제외(대시보드별 분리 집계).
    exclude_kg=True → 모든 kg_* 제외(is_kg, startswith — 미래 kg 종류도 자동 제외해
    수출입 대시보드에 kg 가 새지 않게). 메인/카드의 startswith 분류와 정합."""
    now = now if now is not None else time.time()
    recs = _read()
    if kinds is not None:
        _ks = set(kinds)
        recs = [r for r in recs if r.get("kind") in _ks]
    if exclude_kinds is not None:
        _xs = set(exclude_kinds)
        recs = [r for r in recs if r.get("kind") not in _xs]
    if exclude_kg:
        recs = [r for r in recs if not is_kg(r.get("kind"))]
    day = 86400

    def agg(since):
        sel = [r for r in recs if r.get("ts", 0) >= since]
        cost = sum(r.get("cost_usd", 0.0) for r in sel)
        return {
            "calls": len(sel),
            "in_tok": sum(r.get("in_tok", 0) for r in sel),
            "out_tok": sum(r.get("out_tok", 0) for r in sel),
            "cost_usd": round(cost, 4),
            "cost_krw": int(round(cost * KRW_PER_USD)),
            # 단가 미등재 호출 수 — 그만큼 이 창의 비용이 **과소집계**다.
            # 계산해 두고 화면에 안 실으면 없는 것과 같다(#123·#189·#228).
            "unpriced": sum(1 for r in sel if r.get("unpriced")),
        }

    # KST 달력 경계 (모든 시각 KST 명시계산 — 서버 로컬타임 의존 금지)
    import datetime as _dt
    _kst = _dt.timezone(_dt.timedelta(hours=9))
    _now_kst = _dt.datetime.fromtimestamp(now, _kst)
    _day_start = _dt.datetime(_now_kst.year, _now_kst.month, _now_kst.day,
                              tzinfo=_kst).timestamp()
    _month_start = _dt.datetime(_now_kst.year, _now_kst.month, 1,
                                tzinfo=_kst).timestamp()
    return {
        "today": agg(now - day),
        "d7": agg(now - 7 * day),
        "d30": agg(now - 30 * day),
        "today_kst": agg(_day_start),
        "month": agg(_month_start),
        "total": agg(0),
        "total_calls": len(recs),
    }


def calls_today(now: float | None = None) -> int:
    return summary(now)["today"]["calls"]
