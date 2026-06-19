"""Shared google-genai backend factory: AI Studio (api_key) ↔ Vertex AI (ADC).

Single env toggle moves the **whole bot** between the two Google Gemini
backends — no per-call-site config:

  GOOGLE_GENAI_USE_VERTEXAI=true   → Vertex AI via Application Default
       Credentials (ADC, no API key). Reads GOOGLE_CLOUD_PROJECT +
       GOOGLE_CLOUD_LOCATION (default us-central1).
  (unset / false)                  → AI Studio (Gemini Developer API)
       via GOOGLE_API_KEY — the historical default.

Why a factory instead of editing every call site: the bot has one
google-genai choke point (`bot.screener._call_pro`, reused by ~11
scheduled-feed modules) plus a couple of standalone clients
(screener_gics_check). They all route through `make_client()` here.

The langchain Chat surface (analysts + decision tier, trade bot) is
handled separately in TradingAgents google_client.py / trade.llm_insights
because those need ChatVertexAI, not the google-genai Client.

Read at call time (runtime), so it is robust regardless of when .env is
loaded (systemd EnvironmentFile vs load_dotenv in a module main).

Rule applies to all markets / all LLM surfaces going forward.
"""

from __future__ import annotations

import os
from typing import Optional

# Truthy sentinel returned by effective_key() in Vertex mode so the bot's
# many `if not api_key:` readiness guards keep passing (Vertex auths via
# ADC, not an API key). make_client() recognises and ignores it.
_VERTEX_SENTINEL = "vertex-adc"

_TRUE = ("1", "true", "yes", "on")


def use_vertex() -> bool:
    """True when the bot should route Gemini calls through Vertex AI."""
    return (os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") or "").strip().lower() in _TRUE


def project() -> str:
    return (os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCP_PROJECT") or "").strip()


def location() -> str:
    return (os.environ.get("GOOGLE_CLOUD_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_REGION") or "us-central1").strip()


def llm_ready() -> bool:
    """True when a Gemini backend is configured (Vertex project, or AI
    Studio key). Use where a boolean readiness check is wanted."""
    if use_vertex():
        return bool(project())
    return bool((os.environ.get("GOOGLE_API_KEY") or "").strip())


def effective_key() -> Optional[str]:
    """API key for AI Studio mode, or a truthy sentinel in Vertex mode so
    existing presence-guards (`if not api_key:`) pass. The sentinel is
    ignored by make_client(); never sent over the wire."""
    if use_vertex():
        return _VERTEX_SENTINEL if project() else None
    return os.environ.get("GOOGLE_API_KEY")


def make_client(api_key: Optional[str] = None):
    """Return a configured google.genai.Client for the active backend.

    Vertex mode:   genai.Client(vertexai=True, project=, location=) — ADC,
                   no key. `api_key` arg (often the sentinel) is ignored.
    AI Studio mode: genai.Client(api_key=...). Falls back to GOOGLE_API_KEY
                   from the environment when no real key is passed in.
    """
    from google import genai

    if use_vertex():
        return genai.Client(vertexai=True, project=project(), location=location())

    real = api_key if (api_key and api_key != _VERTEX_SENTINEL) else None
    key = real or (os.environ.get("GOOGLE_API_KEY") or "").strip()
    return genai.Client(api_key=key)
