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

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger(__name__)

USAGE_LOG = Path.home() / ".tradingagents" / "usage.jsonl"
ROTATION_DAYS = 30

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
        except Exception as exc:
            log.warning("usage_tracker: rotation failed: %s", exc)

    return in_window
