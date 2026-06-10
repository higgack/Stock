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

# Gemini 2.5 공개 가격 (USD / 1,000,000 tokens). 출처: ai.google.dev/pricing,
# NOAH bot/usage_tracker.py와 동일. FX 드리프트 시 KRW_PER_USD만 조정.
_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash":      {"in": 0.30,  "out": 2.50},
    "gemini-2.5-flash-lite": {"in": 0.10,  "out": 0.40},
    "gemini-2.5-pro":        {"in": 1.25,  "out": 10.00},
}
KRW_PER_USD = 1380


def cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    r = _PRICING.get(model)
    if not r:
        return 0.0
    return (in_tok or 0) / 1e6 * r["in"] + (out_tok or 0) / 1e6 * r["out"]


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


def summary(now: float | None = None) -> dict:
    """today / 7d / 30d 집계 (calls·tokens·cost_usd·cost_krw) + total_calls."""
    now = now if now is not None else time.time()
    recs = _read()
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
        }

    return {
        "today": agg(now - day),
        "d7": agg(now - 7 * day),
        "d30": agg(now - 30 * day),
        "total_calls": len(recs),
    }


def calls_today(now: float | None = None) -> int:
    return summary(now)["today"]["calls"]
