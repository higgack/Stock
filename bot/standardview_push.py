"""Push completed NOAH analyses to the Standard View backend so they
appear in the daily dashboard's "오늘 NOAH 분석 종목" section.

Phase B-2 (Step 2A, 2026-05-19) — the second half of NOAH ↔ Standard
View bi-directional integration. Phase B-1 pulls Standard View daily
brief into NOAH macro context; this module pushes per-ticker analyses
the other way.

Data flow:

    bot/analyzer.analyze(ticker, date)
      ...
      → push_analysis(ticker, market, verdict, stance_bar, snippet)
          POST http://127.0.0.1:8002/api/noah/analysis
          → Standard View appends to ~/standardview/noah_analyses.jsonl
      ...
      → return summary, full

    (next 08:00 KST Standard View daily generator)
      reads noah_analyses.jsonl filtered for today
      → injects new HTML section between takeaway-card and deploy-card

Best-effort: silent failure (no raise) — Standard View may be down,
backend unreachable, or NOAH may be running on a host without Standard
View. The push runs AFTER cache + archive + dashboard regen so even if
this call breaks, the user's analysis result is fully persisted.

Source attribution: same friend's repo (StanLee5767/standardview),
license received.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger("bot.standardview_push")

# Same-host POST. The Standard View backend systemd unit binds to
# 0.0.0.0:8002 on the telegram-bot VM; 127.0.0.1 keeps the call inside
# the loopback interface (no external network hop, no auth needed).
PUSH_URL = os.environ.get(
    "STANDARDVIEW_PUSH_URL",
    "http://127.0.0.1:8002/api/noah/analysis",
)

# Public dashboard URL for click-through from the Standard View HTML
# back to the matching NOAH archive page. The HEX path is the per-bot
# auth token (already in _HELP_TEXT, not a secret in this context).
NOAH_DASHBOARD_BASE = os.environ.get(
    "NOAH_DASHBOARD_BASE",
    "http://34.50.23.221:8081/06beb08f5f4ad5515007e65f8f60b471/",
)

_HTTP_TIMEOUT = 5  # seconds — keep tight; this must never delay analysis return


def push_analysis(
    ticker: str,
    market: str,
    verdict: str,
    stance_bar: str,
    snippet: str,
    dashboard_url: Optional[str] = None,
) -> bool:
    """Best-effort POST to Standard View. Returns True on 2xx, False
    on any failure. Never raises — caller continues regardless."""
    if not ticker or not verdict:
        return False
    payload = {
        "ticker": ticker,
        "market": market or "?",
        "verdict": verdict,
        "stance_bar": stance_bar or "",
        "snippet": (snippet or "")[:400],
        "dashboard_url": dashboard_url or NOAH_DASHBOARD_BASE,
    }
    try:
        import requests
        resp = requests.post(
            PUSH_URL,
            json=payload,
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code >= 200 and resp.status_code < 300:
            log.info(
                "standardview push: %s → %s (%s)",
                ticker, verdict, resp.status_code,
            )
            return True
        log.warning(
            "standardview push failed: %s status=%s body=%s",
            ticker, resp.status_code, resp.text[:200],
        )
    except Exception as exc:
        # Standard View down, network blip, requests not installed, etc.
        # Never break NOAH's analysis path.
        log.info(
            "standardview push skipped for %s (%s: %s)",
            ticker, type(exc).__name__, exc,
        )
    return False
