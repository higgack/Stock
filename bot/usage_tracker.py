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
import time
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
        model or "unknown",
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
