"""Push completed NOAH analyses to the Standard View backend so they
appear in the daily dashboard's "오늘 NOAH 분석 종목" section AND in
the persistent Insights store (queryable from the Standard View SPA).

Phase B-2 (2026-05-19) — initial JSONL append via /api/noah/analysis.
Phase B-2.1 (2026-05-20) — additionally POST to /api/insights so the
analysis lives in the friend's SQLite store and shows up in the
Standard View SPA's Insights view (with filter by company / sector /
impact / tag / date range). InsightIn schema is upstream Standard
View's (StanLee5767/standardview) — license received.

Data flow:

    bot/analyzer.analyze(ticker, date)
      ...
      → push_analysis(ticker, market, verdict, stance_bar, snippet)
          (1) POST /api/insights   — persistent SQLite store, queryable
          (2) POST /api/noah/analysis — JSONL append, daily-generator feed
          (best-effort, both silent fail; either can be down without
          breaking NOAH's analysis return path)
      ...
      → return summary, full

The dual write is deliberate while we validate the /api/insights path
in production. Once the generator switches to read from /api/insights
as primary source, the /api/noah/analysis path can be removed.

Best-effort: silent failure (no raise) — Standard View may be down,
backend unreachable, or NOAH may be running on a host without Standard
View. The push runs AFTER cache + archive + dashboard regen so even if
this call breaks, the user's analysis result is fully persisted.

Source attribution: same friend's repo (StanLee5767/standardview),
license received.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

log = logging.getLogger("bot.standardview_push")

# Same-host POST. The Standard View backend systemd unit binds to
# 0.0.0.0:8002 on the telegram-bot VM; 127.0.0.1 keeps the call inside
# the loopback interface (no external network hop, no auth needed).
PUSH_URL = os.environ.get(
    "STANDARDVIEW_PUSH_URL",
    "http://127.0.0.1:8002/api/noah/analysis",
)

INSIGHTS_URL = os.environ.get(
    "STANDARDVIEW_INSIGHTS_URL",
    "http://127.0.0.1:8002/api/insights",
)

# Public dashboard URL for click-through from the Standard View HTML
# back to the matching NOAH archive page. The HEX path is the per-bot
# auth token (already in _HELP_TEXT, not a secret in this context).
NOAH_DASHBOARD_BASE = os.environ.get(
    "NOAH_DASHBOARD_BASE",
    "http://34.50.23.221:8081/06beb08f5f4ad5515007e65f8f60b471/",
)

_HTTP_TIMEOUT = 5  # seconds — keep tight; this must never delay analysis return

# Verdict → impact_level mapping. Friend's Insights UI shows
# High/Medium/Low filters; we route strong directional verdicts to
# High so the directional calls stand out from the Hold default.
_VERDICT_TO_IMPACT = {
    "Overweight": "High",
    "Buy": "High",
    "Underweight": "High",
    "Sell": "High",
    "Strong Buy": "High",
    "Strong Sell": "High",
}


def _build_insight_payload(
    ticker: str,
    market: str,
    verdict: str,
    stance_bar: str,
    snippet: str,
    dashboard_url: str,
) -> dict:
    """Map NOAH analysis fields onto InsightIn schema. company_name
    holds the ticker (consumers can split market from prefix); tags
    is comma-separated so the UI's tag filter works on individual
    labels (noah / market / verdict)."""
    impact = _VERDICT_TO_IMPACT.get(verdict, "Medium")
    return {
        "created_at": date.today().isoformat(),
        "author": "NOAH",
        "company_name": ticker,
        "sector": market or "?",
        "source_type": "noah_auto",
        "source_title": f"NOAH analysis: {ticker} → {verdict}",
        "source_url": dashboard_url,
        "key_content": (snippet or "")[:1000],
        "user_insight": stance_bar or "",
        "impact_level": impact,
        "next_actions": "5거래일 후 outcome 자동 평가 (NOAH memory log)",
        "tags": f"noah,auto,{market},{verdict}",
    }


def push_analysis(
    ticker: str,
    market: str,
    verdict: str,
    stance_bar: str,
    snippet: str,
    dashboard_url: Optional[str] = None,
) -> bool:
    """Best-effort dual POST to Standard View. Returns True when the
    Insights POST (the primary persistent path) succeeds; the JSONL
    secondary path is fire-and-forget. Never raises."""
    if not ticker or not verdict:
        return False
    url = dashboard_url or NOAH_DASHBOARD_BASE

    # Legacy JSONL path — kept while the generator's _load_noah_today
    # still reads from /api/noah/today. Swap to /api/insights GET later.
    legacy_payload = {
        "ticker": ticker,
        "market": market or "?",
        "verdict": verdict,
        "stance_bar": stance_bar or "",
        "snippet": (snippet or "")[:400],
        "dashboard_url": url,
    }
    insight_payload = _build_insight_payload(
        ticker, market, verdict, stance_bar, snippet, url
    )

    try:
        import requests
    except Exception as exc:
        log.info("standardview push skipped (requests not installed): %s", exc)
        return False

    insights_ok = False
    try:
        r = requests.post(
            INSIGHTS_URL, json=insight_payload, timeout=_HTTP_TIMEOUT
        )
        if 200 <= r.status_code < 300:
            log.info(
                "standardview /api/insights: %s → %s (%s, id=%s)",
                ticker, verdict, r.status_code,
                (r.json() or {}).get("id", "?"),
            )
            insights_ok = True
        else:
            log.warning(
                "standardview /api/insights failed: %s status=%s body=%s",
                ticker, r.status_code, r.text[:200],
            )
    except Exception as exc:
        log.info(
            "standardview /api/insights skipped for %s (%s: %s)",
            ticker, type(exc).__name__, exc,
        )

    # Legacy JSONL path — fire-and-forget. Don't gate primary success on it.
    try:
        r2 = requests.post(PUSH_URL, json=legacy_payload, timeout=_HTTP_TIMEOUT)
        if 200 <= r2.status_code < 300:
            log.info(
                "standardview /api/noah/analysis: %s → %s (%s)",
                ticker, verdict, r2.status_code,
            )
        else:
            log.warning(
                "standardview /api/noah/analysis failed: %s status=%s",
                ticker, r2.status_code,
            )
    except Exception as exc:
        log.info(
            "standardview /api/noah/analysis skipped for %s (%s)",
            ticker, type(exc).__name__,
        )

    return insights_ok
