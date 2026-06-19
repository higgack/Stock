"""Gemini explicit context caching for decision-tier LLM calls.

F1-MVP cost reduction (Phase 4-CN-D follow-up, 2026-05-19). Wraps
google-genai SDK's `client.caches.create()` API. The decision tier
(research_manager + trader + portfolio_manager) all consume the same
instrument_context (5-10K tokens) — without explicit caching, each
of the 3 calls re-bills full input. With caching, the 2nd and 3rd
calls bill the shared prefix at ~25% rate.

Why decision tier only (MVP scope):
 • Decision tier uses Gemini 2.5 Pro = highest $/token = biggest ROI
 • All 3 decision nodes see IDENTICAL instrument_context (no per-
   analyst slicing applies there) → 3x clean cache hits
 • Analyst tier (Flash) already per-analyst sliced via Option 1 —
   each analyst has different prompt prefix → cross-analyst caching
   would be more complex. Defer to F1-Full when KR Step 2 sources
   land + the per-analyst common-base / variable-suffix split is
   restructured.

Expected savings: ~5% per analysis. Input cost savings only — output
generation unchanged.

Lifecycle (per-analysis):
 1. analyzer.py builds instrument_context, calls
    `GeminiContextCache.create_for_analysis(ticker, context)`
 2. If creation succeeds, cache.name flows through AgentState.gemini_cache_name
 3. Decision-tier nodes invoke their Pro LLM with `cached_content=cache.name`
 4. After graph.invoke() completes, analyzer.py calls `cache.delete()`
    (best-effort cleanup; Gemini auto-expires after TTL anyway)

Failure modes (all silent fallback to non-cached):
 • Context too small (<4000 chars) — cache creation rejected by Gemini
   (min token threshold for the model)
 • Network / API error — cache_name set to empty, downstream LLM calls
   work normally without cache reference
 • API key missing — same as above

Required deps: `google-genai>=1.0` (already installed per F1 sanity check
2026-05-19: version 1.75.0 on bot host).
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional


log = logging.getLogger("bot.gemini_cache")

# Gemini explicit caching minimum token threshold (model-dependent).
# Gemini 2.5 Pro: 4096 tokens minimum for the cached content. Below
# this threshold, caches.create() returns INVALID_ARGUMENT.
#
# Char-to-token heuristic for mixed Korean/English: ~2.5-3 chars per
# token (한국어 1 글자 ≈ 1 토큰, 영어 1 토큰 ≈ 4-5 chars). 4000 chars
# ≈ 1300-1600 tokens. Gemini 2.5 Pro 의 실제 minimum 은 4096 tokens
# 인데 이는 ~12-16K chars 정도. 5000 chars threshold (이전) 는 너무
# 보수적 — 노바렉스 194700.KS 2026-05-19 케이스에서 context 4460 chars
# 가 threshold 미달로 cache skip → 저커버리지 KR 종목 (뉴스/감정 skip)
# 이 caching 효과를 못 받음.
#
# Fix L (2026-05-19) — threshold 5000 → 4000. Gemini API 호출 자체
# 가 4096 token 미달 시 INVALID_ARGUMENT 던지므로 우리 threshold 가
# 정확히 API floor 와 일치할 필요 없음 — 시도하고 실패해도 graceful
# fallback (None 반환 + 경고 log). 더 많은 종목이 caching 효과 받음.
_MIN_CONTEXT_CHARS = 4000

# Cache TTL — Gemini hard caps at 60 minutes for explicit caches.
# Set slightly lower (50 min) so we don't race against expiration
# during a slow analysis (typical analysis: 3-5 min, well under cap).
_CACHE_TTL_SECONDS = 3000  # 50min


class GeminiContextCache:
    """One-shot explicit cache for a single analysis run.

    Holds the cache resource name (e.g. `projects/.../cachedContents/cache-xyz`)
    plus a reference to the client used to create it (for delete).
    Construct via `GeminiContextCache.create_for_analysis(...)` —
    direct __init__ is internal."""

    def __init__(self, cache_name: str, client) -> None:
        self.cache_name = cache_name
        self._client = client

    @staticmethod
    def create_for_analysis(
        ticker: str,
        common_context: str,
        model: str = "gemini-2.5-pro",
    ) -> Optional["GeminiContextCache"]:
        """Create a Gemini CachedContent for this ticker's analysis run.

        Returns None when caching is unavailable (missing key, context
        too small, or API error). Callers should treat None as "no
        cache available, proceed without caching" — non-cached LLM
        calls work normally.

        Args:
            ticker: yfinance ticker (for cache display name + logging).
            common_context: The shared instrument_context that all
                decision-tier nodes will consume. Must be ≥5000 chars
                to clear Gemini's minimum cache size.
            model: Gemini model identifier. Must match the model used
                by the LLMs that will reference the cache (typically
                'gemini-2.5-pro' for decision tier).
        """
        from bot.genai_factory import use_vertex
        if use_vertex():
            # Vertex explicit caching uses a different resource API and the
            # cached_content bind path on ChatVertexAI is unverified. Defer
            # it — caching is only a ~5% input-cost optimization and is
            # graceful to skip (decision nodes just run without a cache).
            log.info("gemini cache skipped: Vertex mode (explicit caching deferred)")
            return None
        api_key = os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            log.info("gemini cache skipped: GOOGLE_API_KEY not set")
            return None
        if len(common_context) < _MIN_CONTEXT_CHARS:
            log.info(
                "gemini cache skipped: context too small (%d chars < %d)",
                len(common_context), _MIN_CONTEXT_CHARS,
            )
            return None

        try:
            from google.genai import types as genai_types
            from bot.genai_factory import make_client
        except ImportError as exc:
            log.warning("gemini cache skipped: google-genai not importable (%s)", exc)
            return None

        try:
            # 팩토리 경유 — Vertex 모드면 ADC 클라이언트(api_key 무시→401 회피). 지금은
            # 위 use_vertex() early-return 으로 Vertex 시 미도달(캐싱 보류)이나, 향후
            # Vertex 캐싱 활성화 시 이 줄이 api_key 를 Vertex 에 들이밀어 401 나는 걸
            # 방지(사용자 2026-06-19 지적). AI Studio 모드에선 api_key 클라이언트.
            client = make_client(api_key)
            cache = client.caches.create(
                model=model,
                config=genai_types.CreateCachedContentConfig(
                    contents=[
                        genai_types.Content(
                            role="user",
                            parts=[genai_types.Part(text=common_context)],
                        )
                    ],
                    ttl=f"{_CACHE_TTL_SECONDS}s",
                    display_name=f"analysis_{ticker}_{date.today().isoformat()}",
                ),
            )
            log.info(
                "gemini cache created: %s (ticker=%s, %d chars, model=%s, ttl=%ds)",
                cache.name, ticker, len(common_context), model, _CACHE_TTL_SECONDS,
            )
            return GeminiContextCache(cache.name, client)
        except Exception as exc:
            log.warning(
                "gemini cache creation failed for %s: %s — proceeding without cache",
                ticker, exc,
            )
            return None

    def delete(self) -> None:
        """Best-effort cleanup. Safe to call multiple times. Failures
        logged but not raised — Gemini auto-expires caches at TTL
        even if delete() never runs."""
        if not self.cache_name:
            return
        try:
            self._client.caches.delete(name=self.cache_name)
            log.info("gemini cache deleted: %s", self.cache_name)
            self.cache_name = ""  # idempotent guard
        except Exception as exc:
            log.warning("gemini cache delete failed for %s: %s", self.cache_name, exc)


def maybe_create_cache(
    ticker: str, common_context: str,
) -> tuple[str, Optional[GeminiContextCache]]:
    """Convenience wrapper for analyzer.py.

    Returns (cache_name, cache_obj). cache_name is empty string when
    caching unavailable — flows through AgentState.gemini_cache_name
    so downstream nodes can treat empty as 'no cache'. cache_obj is
    held by analyzer.py to call .delete() in finally block.
    """
    cache = GeminiContextCache.create_for_analysis(ticker, common_context)
    if cache is None:
        return "", None
    return cache.cache_name, cache
