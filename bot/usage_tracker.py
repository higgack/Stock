"""Tracks Gemini API usage by hooking LangChain's callback system.

Each analysis writes two record types to ``~/.tradingagents/usage.jsonl``:
  - ``llm_call`` per Gemini call: model, prompt + completion tokens, cost
  - ``analysis`` per /TICKER request: ticker, elapsed time, cache_hit flag
  - ``failure``  per timeout / exception in the main bot (cost-aware
    failure visibility — written from ``bot.telegram_bot``)

The ``/usage`` command reads the JSONL, aggregates over today / 7d / 30d,
and renders a digest with model breakdown, a 7-day bar chart, and the
most-analyzed tickers. Records older than ``ROTATION_DAYS`` are evicted
from the file on the next read so it stays bounded.

Pricing is the public Google AI rate sheet for Gemini 2.5 (USD per 1M
tokens), converted at the constant ``KRW_PER_USD`` defined below. Edit
that constant if the FX rate drifts materially.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover - langchain absent (lightweight env)
    # Only UsageCallback needs the real base class; the cost/aggregation
    # helpers (sum_analysis_cost_krw, load_records, estimate_cost_usd, …) are
    # pure and must stay importable where langchain isn't installed.
    BaseCallbackHandler = object  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

USAGE_LOG = Path.home() / ".tradingagents" / "usage.jsonl"
ROTATION_DAYS = 30
# 로테이션으로 파일에서 빠지는 llm_call 비용을 영구 적산하는 롤업 —
# '누적(전체)' 비용 표기(대시보드·/usage)가 30일 로테이션에도 참이 되게
# (사용자 2026-07-05 '비용카드는 오늘/이번달/누적'). 누적 = 롤업 + 현재 파일.
ROLLUP_PATH = USAGE_LOG.with_name("usage_rollup.json")

# Gemini 2.5 public pricing — USD per 1,000,000 tokens.
# Source: https://ai.google.dev/gemini-api/docs/pricing  (≤200K input tier).
_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash":      {"in": 0.30,  "out": 2.50},
    "gemini-2.5-flash-lite": {"in": 0.10,  "out": 0.40},
    "gemini-2.5-pro":        {"in": 1.25,  "out": 10.00},
}

# What each model tier does in our pipeline. Surfaced by /usage so the
# user can see WHAT the dollars are buying, not just how many calls.
MODEL_PURPOSE: dict[str, str] = {
    "gemini-2.5-flash":      "4명 분석가 (시장 / 감정 / 뉴스 / 펀더멘털)",
    "gemini-2.5-flash-lite": "Bull/Bear 토론 + Risk 3인 토론",
    "gemini-2.5-pro":        "결정 3노드 (Research Mgr / Trader / Portfolio Mgr)",
}

# Static FX. Edit when the rate drifts materially; we don't fetch live
# rates here because /usage should stay fast and offline-tolerant.
KRW_PER_USD = 1380

# `_extract_token_usage` 가 모델을 못 읽었을 때 적는 이름.
# 분류기와 기록부가 같은 상수를 봐야 갈리지 않는다(#38).
UNRECORDED_MODEL = "unknown"


# 단가표에 없는 모델을 **모델당 한 번만** 알리기 위한 기억(#25 늘 뜨는 경고
# 금지). 테스트는 이걸 monkeypatch 로 갈아끼워 격리한다(#30).
_UNPRICED_SEEN: set[str] = set()


def is_priced(model: str) -> bool:
    """단가표에 있는 모델인가 — 원장이 ₩0 을 사실인 척 적지 않게(#43)."""
    return model in _PRICING


def is_unpriced_record(rec: dict) -> bool:
    """이 레코드의 비용이 **단가 미등재 때문에** 0 인가.

    ⚠️ 저장된 표식만 믿으면 안 된다(독립 리뷰 2026-09-08 실측): 이 원장에 쓰는
    곳이 13곳인데 표식을 붙이는 곳은 둘뿐이고(#24 열거형), 이 커밋 **이전**
    레코드엔 아예 없다. 그래서 **모델 이름을 단가표에 대조**하는 쪽을 같이 둔다
    — 읽는 쪽이 원천(단가표)에 직접 물으면 쓰는 쪽이 몇 곳이든 안 샌다(#86).
    저장된 표식도 그대로 존중한다: 나중에 그 모델이 `_PRICING` 에 추가되면
    이름 대조는 '있음'이 되지만 **그때 저장된 cost_usd 는 여전히 0** 이므로,
    표식이 있는 옛 레코드는 계속 미등재로 세야 사실이다.
    """
    if rec.get("unpriced"):
        return True
    m = rec.get("model")
    return bool(m) and not is_priced(m)


def is_unrecorded_model(rec: dict) -> bool:
    """모델 이름을 **못 읽어서** ₩0 인가 — 단가표 문제가 아니다.

    `_extract_token_usage` 는 모델을 못 찾으면 `"unknown"` 으로 적는다. 그
    이름은 `_PRICING` 에 넣을 수 있는 것이 아니므로, '단가 미등재' 와 같은
    라벨로 묶으면 화면이 **이행 불가능한 처방**을 준다(#82·#260).
    """
    return (rec.get("model") or UNRECORDED_MODEL) == UNRECORDED_MODEL


def split_unpriced(records) -> dict:
    """₩0 으로 적힌 호출을 **처방이 다른 두 갈래**로 가른다(#82).

    - `missing_rate` — 단가표에 없는 모델. `_PRICING` 에 공식 요율로 추가하면
      고쳐진다(그때 공표 요율 핀 회귀도 같이 갱신, `_RATE_PIN`).
    - `no_model` — 모델을 못 읽어 `"unknown"`(또는 빈 값)으로 적힌 것.
      기록 경로(`_extract_token_usage`) 문제라 요율표로는 고칠 수 없다.
      규모를 알 수 있게 토큰 합도 같이 센다(#202 숫자로 말하라).

    ⚠️ `trade/llm_usage` 는 호출부가 모델 이름을 **명시해** 넘기므로 거기선
    `no_model` 이 구조적으로 생기지 않는다 — 그쪽 라벨은 지금도 참이라
    건드리지 않는다(잊은 게 아니다, #55).
    """
    out = {"missing_rate": 0, "no_model": 0, "total": 0, "no_model_tokens": 0}
    for rec in records or []:
        if rec.get("type") != "llm_call" or not is_unpriced_record(rec):
            continue
        out["total"] += 1
        if is_unrecorded_model(rec):
            out["no_model"] += 1
            out["no_model_tokens"] += (int(rec.get("prompt_tokens") or 0)
                                       + int(rec.get("completion_tokens") or 0))
        else:
            out["missing_rate"] += 1
    return out


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """USD cost of a single LLM call. Returns 0 for unrecognised models.

    `cached_tokens` (BUG3, 2026-05-29 audit): the portion of `prompt_tokens`
    served from a Gemini explicit context cache. Gemini bills cached input
    at ~25% of the normal input rate, so the cached tokens are discounted
    75%. `prompt_tokens` from usage_metadata is the TOTAL input (cached +
    fresh), so we subtract 0.75×cached to get the effective billable input.
    Defaults to 0 → identical to the previous behaviour when no cache hit."""
    rate = _PRICING.get(model)
    if not rate:
        # ⚠️ 조용한 ₩0 금지. 모델 id 가 바뀌면(2.5→3.0 류) 모든 호출이 0 으로
        # 적히고 **비용카드가 '공짜'라고 말한다** — 값이 다 '있어서' 어떤
        # 감사도 안 걸린다(#284·#43·#82). 단가를 지어낼 수는 없으므로(#32)
        # 0 은 그대로 두되 **모델 이름을 대서** 알린다.
        # 모델당 한 번만 — 늘 뜨는 경고는 아무것도 안 재는 것과 같다(#25·#260).
        if model not in _UNPRICED_SEEN:
            _UNPRICED_SEEN.add(model)
            log.warning("usage_tracker: 단가 미등재 모델 %r — 이 호출들의 비용이 "
                        "0 으로 집계된다. _PRICING 에 추가할 것", model)
        return 0.0
    cached_tokens = max(0, min(cached_tokens, prompt_tokens))
    effective_input = prompt_tokens - 0.75 * cached_tokens
    return (
        effective_input * rate["in"] + completion_tokens * rate["out"]
    ) / 1_000_000


_write_lock = Lock()


def _append(record: dict) -> None:
    """Atomic append to the JSONL log. Process-safe at the OS level
    (POSIX append() is atomic for ≤PIPE_BUF), thread-safe via the lock."""
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    try:
        with _write_lock:
            with open(USAGE_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:
        log.warning("usage_tracker: write failed: %s", exc)


def _extract_token_usage(response) -> tuple[str, int, int]:
    """Pull (model, prompt_tokens, completion_tokens) out of an LLMResult.

    Different LangChain provider integrations (and different versions of
    the Google one) put these in slightly different places. Try the
    documented locations in order and fall through to zeros if none
    match — wrong numbers are worse than missing numbers in a cost log.
    """
    output = getattr(response, "llm_output", None) or {}
    model = output.get("model_name") or output.get("model") or ""

    usage = output.get("token_usage") or {}
    if not usage:
        # google_genai surfaces it on each generation as usage_metadata
        for batch in getattr(response, "generations", []) or []:
            for gen in batch:
                gen_info = getattr(gen, "generation_info", None) or {}
                usage = gen_info.get("usage_metadata") or {}
                if usage:
                    break
                # newer langchain versions: directly on the message
                msg = getattr(gen, "message", None)
                msg_usage = getattr(msg, "usage_metadata", None) if msg else None
                if msg_usage:
                    usage = dict(msg_usage)
                    break
            if usage:
                break

    prompt_tokens = (
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or usage.get("prompt_token_count")
        or 0
    )
    completion_tokens = (
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or usage.get("candidates_token_count")
        or 0
    )
    # BUG3 (2026-05-29 audit): cached input tokens (Gemini explicit cache
    # hit) are reported separately and billed at ~25%. langchain surfaces
    # them under input_token_details.cache_read on the message usage, or
    # cached_content_token_count in the raw genai usage_metadata. Capture
    # for cost discounting + cache-effectiveness visibility.
    details = usage.get("input_token_details") or {}
    cached_tokens = (
        (details.get("cache_read") if isinstance(details, dict) else 0)
        or usage.get("cached_content_token_count")
        or usage.get("cache_read_input_tokens")
        or 0
    )

    if not model:
        # Last-resort: scan generation kwargs for a model field.
        for batch in getattr(response, "generations", []) or []:
            for gen in batch:
                info = getattr(gen, "generation_info", None) or {}
                model = info.get("model") or info.get("model_name") or ""
                if model:
                    break
            if model:
                break

    return (
        model or UNRECORDED_MODEL,
        int(prompt_tokens or 0),
        int(completion_tokens or 0),
        int(cached_tokens or 0),
    )


class UsageCallback(BaseCallbackHandler):
    """LangChain callback that writes one ``llm_call`` record per
    Gemini invocation. Wire it into the LLM client via
    ``llm_kwargs['callbacks'] = [UsageCallback()]``.
    """

    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[override]
        try:
            model, p_tokens, c_tokens, cached_tokens = _extract_token_usage(response)
            cost = estimate_cost_usd(model, p_tokens, c_tokens, cached_tokens)
            record = {
                "ts": time.time(),
                "type": "llm_call",
                "model": model,
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "cost_usd": cost,
            }
            if not is_priced(model):
                # ₩0 을 사실인 척 남기지 않는다 — 원장이 스스로 밝힌다(#43).
                record["unpriced"] = True
            # Only emit cached_tokens when non-zero — keeps legacy log lines
            # unchanged + makes cache hits greppable for effectiveness checks.
            if cached_tokens:
                record["cached_tokens"] = cached_tokens
            _append(record)
        except Exception as exc:
            log.warning("usage_tracker: on_llm_end failed: %s", exc)

    # `on_chat_model_end` exists separately in newer LangChain; route
    # both through the same recording logic.
    on_chat_model_end = on_llm_end  # type: ignore[assignment]


def log_analysis(ticker: str, elapsed_sec: float, cache_hit: bool) -> None:
    """Record a single completed analysis (cache hit or fresh run)."""
    _append({
        "ts": time.time(),
        "type": "analysis",
        "ticker": ticker,
        "elapsed_sec": round(elapsed_sec, 2),
        "cache_hit": cache_hit,
    })


# Subsystems whose cost belongs to a single /TICKER analysis run: the
# analysis's own Gemini calls land with NO subsystem tag (UsageCallback
# above omits the field), and chart_translate writes its own llm_call with
# subsystem='chart_translate' for THIS run's chart disclosure-title
# translation. Everything else (screener / daily_byte / realestate /
# cheongyak / blog) is an independent surface that may run concurrently in
# a separate timer process and must NOT be attributed to the ticker.
_ANALYSIS_COST_SUBSYSTEMS = (None, "", "chart_translate")


def sum_analysis_cost_krw(since_ts: float, until_ts: float | None = None) -> int:
    """KRW cost of one analysis run, summed from the usage log over
    ``[since_ts, until_ts]``.

    Reads ``usage.jsonl`` directly (not the in-process UsageCallback)
    because that file is the single sink BOTH the analysis Gemini calls and
    chart_translate land in — an in-memory accumulator would miss the
    translation cost the user wants counted. Restricts to the analysis's own
    subsystems so a concurrent Daily Byte / screener run can't leak in. The
    bot serialises /TICKER runs behind the .busy marker, so the previous
    run's calls (ts < since_ts) never overlap this window. Best-effort —
    returns 0 on any read error so a cost-stamp failure can't break the
    archive write."""
    return sum_subsystem_cost_krw(_ANALYSIS_COST_SUBSYSTEMS, since_ts, until_ts)


def sum_subsystem_cost_krw(subsystems, since_ts: float,
                           until_ts: float | None = None) -> int:
    """KRW cost of one run of ``subsystems`` over ``[since_ts, until_ts]``.

    `sum_analysis_cost_krw` 의 일반화 — 종목분석 외 surface(실적분석 등)도
    "이번 실행 얼마" 를 같은 방식·같은 sink(usage.jsonl)로 보고하기 위해
    subsystem 태그만 갈아끼워 재사용한다(사용자 2026-08-16 '실적분석도
    비용 종목분석처럼 보고루트 따라서'). 전 surface 공용 — 시장/기능
    특정 로직 없음. Best-effort: 읽기 실패 시 0."""
    if isinstance(subsystems, (str, type(None))):
        subsystems = (subsystems,)
    wanted = set(subsystems)
    if until_ts is None:
        until_ts = time.time()
    if not USAGE_LOG.exists():
        return 0
    total_usd = 0.0
    try:
        with open(USAGE_LOG, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "llm_call":
                    continue
                ts = rec.get("ts", 0)
                if ts < since_ts or ts > until_ts:
                    continue
                if rec.get("subsystem") not in wanted:
                    continue
                total_usd += rec.get("cost_usd", 0) or 0
    except Exception as exc:
        log.warning("usage_tracker: sum_subsystem_cost_krw failed: %s", exc)
        return 0
    return int(round(total_usd * KRW_PER_USD))


def log_failure(ticker: str, reason: str) -> None:
    """Record an analysis that didn't complete — timeout, exception, etc."""
    _append({
        "ts": time.time(),
        "type": "failure",
        "ticker": ticker,
        "reason": reason[:200],
    })


def log_tool_failure(tool: str, reason: str) -> None:
    """Record a single tool-level failure (yfinance fetch dead, alpha
    vantage rate-limited, etc). Aggregated by the dashboard so persistent
    upstream issues become visible before they cause a wave of analyst
    failures. Cheap append-only — call freely from inside tool wrappers."""
    _append({
        "ts": time.time(),
        "type": "tool_failure",
        "tool": tool,
        "reason": reason[:200],
    })


def load_records(window_days: int = ROTATION_DAYS) -> list[dict]:
    """Read all records within the window. Auto-rotates older records out
    of the file on each read so the JSONL never grows unbounded."""
    if not USAGE_LOG.exists():
        return []

    cutoff = time.time() - ROTATION_DAYS * 86400
    window_cutoff = time.time() - window_days * 86400

    keep_in_file: list[str] = []
    in_window: list[dict] = []
    rotated = 0
    rotated_cost_usd = 0.0
    try:
        with open(USAGE_LOG, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts", 0)
                if ts >= cutoff:
                    keep_in_file.append(line)
                    if ts >= window_cutoff:
                        in_window.append(rec)
                else:
                    rotated += 1
                    if rec.get("type") == "llm_call":
                        rotated_cost_usd += rec.get("cost_usd", 0) or 0
    except Exception as exc:
        log.warning("usage_tracker: read failed: %s", exc)
        return []

    if rotated > 0:
        # Rewrite atomically. Any concurrent appends after the read are
        # lost from the rotation pass but still present in any future read
        # because the file we're replacing is the snapshot we just read.
        tmp = USAGE_LOG.with_suffix(".jsonl.tmp")
        try:
            with _write_lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    for line in keep_in_file:
                        f.write(line + "\n")
                os.replace(tmp, USAGE_LOG)
                # 롤업 적산은 replace 성공 후 같은 lock 안. 순서 근거:
                # replace 실패 → 레코드 잔존, 다음 로테이션 재시도(이중적산 없음).
                # 롤업쓰기 실패(희귀) → 그 회차 비용만 1회 미적산(과대계상 없음).
                if rotated_cost_usd > 0:
                    _add_rollup_cost_usd(rotated_cost_usd)
        except Exception as exc:
            log.warning("usage_tracker: rotation failed: %s", exc)

    return in_window


def rollup_cost_usd() -> float:
    """로테이션으로 usage.jsonl 에서 빠진 과거 llm_call 비용 총액(USD).
    '누적(전체)' = rollup_cost_usd() + 현재 파일 합. graceful(0.0)."""
    try:
        with open(ROLLUP_PATH, encoding="utf-8") as f:
            return float((json.load(f) or {}).get("cost_usd", 0.0) or 0.0)
    except FileNotFoundError:
        return 0.0
    except Exception as exc:
        log.warning("usage_tracker: rollup read failed: %s", exc)
        return 0.0


def _add_rollup_cost_usd(delta: float) -> None:
    """롤업 파일에 delta 적산(원자적 replace). 호출부가 _write_lock 보유."""
    total = rollup_cost_usd() + delta
    tmp = ROLLUP_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"cost_usd": round(total, 6),
                   "note": "usage.jsonl 30일 로테이션으로 빠진 비용 적산분"}, f)
    os.replace(tmp, ROLLUP_PATH)


# ── 진단 CLI ──────────────────────────────────────────────────────────────
# `cd ~/stock && .venv/bin/python -m bot.usage_tracker --check`
_CHECK_VER = 2

# ④ 자기검증 표본. ⚠️ **실제 모델 id 를 쓰면 안 된다** — ⑥ 이 시키는 대로
# 그 모델을 `_PRICING` 에 넣는 순간 이 검증이 빨간불이 되어 §Pre-commit 6 이
# 무관한 커밋까지 전부 막는다(독립 리뷰 2026-09-09 실측 · #294 성공 조건이
# 뇌관인 시한폭탄 · #67 리터럴을 박으면 bump 마다 무관한 빨간불).
_UNPRICED_SAMPLE = "__단가미등재_표본__"

_UNKNOWN_MODEL = UNRECORDED_MODEL   # 하위호환 별칭(#38 단일 출처)

# 공표 요율을 그대로 못박아 둔 회귀. 단가표를 고치면 여기도 같이 고쳐야
# 한다 — 요율 변경을 **의도적으로** 만들려고 둔 마찰이다(#317).
_RATE_PIN = ("tests/test_regression.py::TestPricingSingleSource"
             "::test_canonical_rates_are_the_published_ones")


def _scan_ledger_readonly() -> dict:
    """원장의 llm_call 을 **읽기만** 해서 갈래별로 센다.

    ⚠️ `load_records()` 를 부르면 안 된다 — 그 함수는 읽으면서 파일을 다시
    쓰고 로테이션분을 롤업에 적산한다. 진단이 자기가 읽을 신호를 오염시키면
    안 된다(#264·#283 — dry-run 이 `generate()` 로 실제 과금·기록을 남긴 그것).

    ⚠️ 판정은 **화면이 쓰는 그 술어**(`is_unpriced_record`)로 한다 — `not
    is_priced(m)` 로 재면 저장된 표식이 붙은 레코드를 통째로 놓쳐, 대시보드
    비용카드가 과소집계 중인데 CLI 가 초록불을 준다(#35, 리뷰 실측).
    """
    out = {"exists": USAGE_LOG.exists(), "total": 0, "missing": {},
           "flagged": {}, "unknown": 0, "unrecorded": 0,
           "nomodel_tokens": 0, "nomodel_first": 0.0, "nomodel_last": 0.0,
           "err": ""}
    if not out["exists"]:
        return out
    try:
        with open(USAGE_LOG, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "llm_call":
                    continue
                out["total"] += 1
                m = rec.get("model")
                if not m or m == _UNKNOWN_MODEL:
                    out["unrecorded" if not m else "unknown"] += 1
                    out["nomodel_tokens"] += (
                        int(rec.get("prompt_tokens") or 0)
                        + int(rec.get("completion_tokens") or 0))
                    # 언제 그랬는지를 알아야 어느 배포·어느 경로인지 좁힌다 —
                    # 안 실으면 다음 라운드에 또 손으로 명령을 조립하게 된다
                    # (#319 가 바로 그 사고다).
                    ts = float(rec.get("ts") or 0)
                    if ts:
                        out["nomodel_first"] = min(out["nomodel_first"] or ts, ts)
                        out["nomodel_last"] = max(out["nomodel_last"], ts)
                elif is_unpriced_record(rec):
                    # 단가표에 없어서 0 인가(고칠 수 있다), 아니면 옛 표식인가
                    # (그때 0 으로 적혔다 — 이제 와서 고칠 수 없다, #260).
                    key = "flagged" if is_priced(m) else "missing"
                    out[key][m] = out[key].get(m, 0) + 1
    except Exception as exc:
        # ⚠️ 읽다 끊긴 통계를 완결인 척 판정하면 안 된다(#41·#54) — 사유를
        # 싣고 호출부가 ❓ 로 찍는다.
        out["err"] = f"{type(exc).__name__}: {exc}"
        log.warning("usage_tracker --check: 원장 읽기 실패(%s)", out["err"])
    return out


def _span(sc: dict) -> str:
    """모델 미기록 호출이 **언제** 났는지. KST 로 적는다(전역 표기 규칙 10a).

    못 재면 단정하지 않는다(#165) — 'ts 없음' 이라고 말한다.
    """
    a, b = sc.get("nomodel_first") or 0.0, sc.get("nomodel_last") or 0.0
    if not a or not b:
        return "시각 미기록"
    kst = timezone(timedelta(hours=9))
    fmt = "%Y-%m-%d %H:%M"
    lo = datetime.fromtimestamp(a, kst).strftime(fmt)
    hi = datetime.fromtimestamp(b, kst).strftime(fmt)
    return f"{lo} KST" if lo == hi else f"{lo} ~ {hi} KST"


def _check() -> int:
    """단가표·환율·미등재 판정을 사실대로 찍는다. 읽기 전용·무과금.

    ⚠️ **이상이 없을 때도 말한다** — 빈 출력이 정답인 진단은 없다(#274).
    그리고 대조할 게 없으면 ✅ 가 아니라 ❓ 다(#54).
    """
    print(f"usage_tracker --check v{_CHECK_VER}")
    # ① 인터프리터 — venv 밖에서 돌면 결과가 통째로 거짓일 수 있다(#132).
    print(f"① 인터프리터: {sys.executable}")

    print("② 단가표(USD / 1M tokens) — 이게 원천이다:")
    for m in sorted(_PRICING):
        r = _PRICING[m]
        print(f"   {m:24} in {r['in']:>6}  out {r['out']:>6}")

    # ③ 두 환율은 **다른 일**을 한다. 숫자만 나란히 두면 또 '중복'이라고
    #    통일된다 — 실제로 그렇게 통일했다가 과거 합계가 3.6% 틀어질 뻔했다
    #    (#317 리뷰 오수용). 그래서 이름을 갈라 적는다.
    print("③ 환율 — 두 상수는 서로 다른 일을 한다:")
    print(f"   표시 환산            KRW_PER_USD = {KRW_PER_USD}")
    try:
        from bot.dashboard import _LEGACY_KRW_WRITE_FX as _legacy
        print(f"   레거시 cost_krw 기록  = {_legacy}"
              "  (그때 기록에 쓰인 환율 — 되읽을 땐 이걸로 나눠야 왕복이 맞다)")
    except Exception as exc:
        print(f"   레거시 기록 환율     ❓ 판정 불가({type(exc).__name__}: {exc})")

    # ④ 판정을 **표본으로 태워** 보인다 — 손으로 따옴표를 조립하다 키가
    #    `"model"` 이 되어 멀쩡한 가드가 False 를 낸 그 사고(#319·#252).
    print("④ 미등재 판정 자체 검증:")
    for m in (sorted(_PRICING)[0], _UNPRICED_SAMPLE):
        tag = "등재" if is_priced(m) else "미등재"
        print(f"   {m:24} {tag} · is_unpriced_record 판정="
              f"{is_unpriced_record({'model': m})}")
    print("   표식 {'unpriced': True} 인 옛 레코드 → "
          f"{is_unpriced_record({'model': sorted(_PRICING)[0], 'unpriced': True})}"
          "  (그때 저장된 cost_usd 는 여전히 0 이라 계속 미등재로 센다)")

    # ⑤ 원장 대조 — 읽기 전용(로테이션 안 함).
    sc = _scan_ledger_readonly()
    print(f"⑤ 원장 대조(읽기 전용): {USAGE_LOG}")
    if not sc["exists"]:
        # '없음'과 '비어 있음'은 처방이 다르다(#82) — 같은 문구로 적으면
        # 잘못된 사용자·인터프리터로 돈 것을 '원장이 비었다'로 읽는다.
        print("   ❓ 원장 파일이 없다 — 경로·실행 사용자를 확인할 것"
              "(판정 불가, #54)")
        print("⑥ 판정: ❓ 단가표·판정은 정상이나 원장 대조는 못 했다")
        return 0
    if sc["err"]:
        print(f"   ❓ 원장을 끝까지 못 읽었다({sc['err']}) — "
              f"{sc['total']:,}건까지만 봤다(부분 통계, 판정 불가)")
        print("⑥ 판정: ❓ 원장 대조 실패 — 위 사유를 먼저 볼 것")
        return 1
    if sc["total"] == 0:
        print("   ❓ llm_call 0건 — 대조할 게 없다(판정 불가, #54)")
        print("⑥ 판정: ❓ 단가표·판정은 정상이나 원장 대조는 못 했다")
        return 0

    print(f"   llm_call {sc['total']:,}건")
    for m, n in sorted(sc["flagged"].items(), key=lambda kv: -kv[1]):
        # 고칠 수 없는 과거분이므로 ❌ 로 찍지 않는다 — 매일 오는 ❌ 는
        # 진짜 ❌ 를 가린다(#260).
        print(f"   ⓘ 표식 {m} — {n:,}콜이 단가 미등재로 기록됐다"
              "(그때 0 으로 적힘 · 지금은 단가표에 있다 · 과거분이라 고칠 수 없음)")
    if sc["unknown"] or sc["unrecorded"]:
        # 규모를 모르면 고칠지 말지 못 정한다 — 토큰 합을 같이 적는다(#202).
        print(f"   ⚠️ 모델 미기록('{_UNKNOWN_MODEL}') "
              f"{sc['unknown'] + sc['unrecorded']:,}콜 · "
              f"토큰 {sc['nomodel_tokens']:,} · {_span(sc)} — 단가표가 아니라 "
              "**기록 경로**(_extract_token_usage) 문제다. "
              "이 이름은 _PRICING 에 넣을 수 없다")
    if sc["missing"]:
        for m, n in sorted(sc["missing"].items(), key=lambda kv: -kv[1]):
            print(f"   ❌ 단가 미등재 {m} — {n:,}콜의 비용이 0 으로 집계된다")
        # ⚠️ 처방은 **끝까지** 적는다 — 1단계만 하면 공표 요율 핀 회귀가
        #    빨간불이 되어 §Pre-commit 6 으로 커밋이 막히고, 운영자는 이유를
        #    모른다(#82·#274). 그 핀은 요율 변경을 의도적으로 만들려고 둔
        #    것이므로 약화시키지 않고 처방에 싣는다.
        print("⑥ 판정: ❌ ① _PRICING 에 위 모델을 공식 요율로 추가"
              f"(지어내지 말 것) ② 같은 커밋에서 {_RATE_PIN} 도 갱신")
        return 1
    if sc["flagged"] or sc["unknown"] or sc["unrecorded"]:
        print("⑥ 판정: ⚠️ 단가표엔 빠진 모델이 없다 — 위 항목은 과거분·기록 경로")
        return 0
    print("   ✅ 원장의 모든 모델이 단가표에 있다")
    print("⑥ 판정: ✅ 이상 없음")
    return 0


def main() -> int:
    if "--check" in sys.argv[1:]:
        return _check()
    print("사용법: cd ~/stock && .venv/bin/python -m bot.usage_tracker --check")
    return 0


if __name__ == "__main__":  # pragma: no cover - 진입점
    raise SystemExit(main())
