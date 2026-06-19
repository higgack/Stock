import logging
import os
from typing import Any, Optional

from langchain_google_genai import ChatGoogleGenerativeAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

log = logging.getLogger("tradingagents.google")

# Optional Vertex AI surface. langchain-google-vertexai is a separate
# package from langchain-google-genai; it is only needed when the bot is
# toggled to Vertex (GOOGLE_GENAI_USE_VERTEXAI=true). Import lazily so the
# AI Studio path keeps working even if the package is absent.
try:
    from langchain_google_vertexai import ChatVertexAI as _BaseChatVertexAI
except ImportError:  # package not installed — AI Studio mode only
    _BaseChatVertexAI = None

_TRUE = ("1", "true", "yes", "on")


def _use_vertex() -> bool:
    return (os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") or "").strip().lower() in _TRUE


def _project() -> str:
    return (os.environ.get("GOOGLE_CLOUD_PROJECT")
            or os.environ.get("GCP_PROJECT") or "").strip()


def _location() -> str:
    return (os.environ.get("GOOGLE_CLOUD_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_REGION") or "us-central1").strip()


class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI with normalized content output.

    Gemini 3 models return content as list of typed blocks.
    This normalizes to string for consistent downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


if _BaseChatVertexAI is not None:

    class NormalizedChatVertexAI(_BaseChatVertexAI):
        """ChatVertexAI with the same content normalization as the AI
        Studio variant — Gemini 3 returns typed blocks on Vertex too."""

        def invoke(self, input, config=None, **kwargs):
            return normalize_content(super().invoke(input, config, **kwargs))
else:
    NormalizedChatVertexAI = None


class GoogleClient(BaseLLMClient):
    """Client for Google Gemini models (AI Studio or Vertex AI)."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return a configured Gemini chat LLM.

        Routes to Vertex AI (ChatVertexAI, ADC auth) when
        GOOGLE_GENAI_USE_VERTEXAI is set, otherwise AI Studio
        (ChatGoogleGenerativeAI, API key)."""
        self.warn_if_unknown_model()
        if _use_vertex():
            return self._get_vertex_llm()
        return self._get_aistudio_llm()

    def _thinking_kwargs(self) -> dict:
        """Map thinking_level to the right API param for this model.

        Gemini 3 Pro: low, high
        Gemini 3 Flash: minimal, low, medium, high
        Gemini 2.5 Pro: thinking required (rejects thinking_budget=0)
        Gemini 2.5 Flash/Flash-Lite: thinking_budget (0=disable, -1=dynamic)
        """
        thinking_level = self.kwargs.get("thinking_level")
        if not thinking_level:
            return {}
        model_lower = self.model.lower()
        if "gemini-3" in model_lower:
            # Gemini 3 Pro doesn't support "minimal", use "low" instead
            if "pro" in model_lower and thinking_level == "minimal":
                thinking_level = "low"
            return {"thinking_level": thinking_level}
        if "pro" in model_lower:
            # Gemini 2.5 Pro requires thinking — thinking_budget=0 raises
            # 400. Cap at 4096 (Option 4: thinking_budget_override lets PM
            # consensus paths run lighter, e.g. 2048). See git history for
            # the AMAT 2026-05-10 cost rationale.
            return {"thinking_budget": self.kwargs.get("thinking_budget_override") or 4096}
        # Gemini 2.5 Flash / Flash-Lite
        return {"thinking_budget": -1 if thinking_level == "high" else 0}

    def _get_aistudio_llm(self) -> Any:
        """AI Studio (Gemini Developer API) — API key auth."""
        llm_kwargs = {"model": self.model}

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in ("timeout", "max_retries", "callbacks", "http_client", "http_async_client"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        # Cap output length to control cost. Forces concise reports rather
        # than letting the model ramble with long-tail filler tokens.
        if "max_output_tokens" in self.kwargs and self.kwargs["max_output_tokens"]:
            llm_kwargs["max_output_tokens"] = self.kwargs["max_output_tokens"]

        # Unified api_key maps to provider-specific google_api_key
        google_api_key = self.kwargs.get("api_key") or self.kwargs.get("google_api_key")
        if google_api_key:
            llm_kwargs["google_api_key"] = google_api_key

        llm_kwargs.update(self._thinking_kwargs())
        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)

    def _get_vertex_llm(self) -> Any:
        """Vertex AI — ChatVertexAI, ADC auth (no API key)."""
        if NormalizedChatVertexAI is None:
            raise ImportError(
                "GOOGLE_GENAI_USE_VERTEXAI is set but langchain-google-vertexai "
                "is not installed. Run: pip install langchain-google-vertexai"
            )

        vkwargs = {"model": self.model, "project": _project(), "location": _location()}

        # max_retries / callbacks are standard langchain params; base_url,
        # http_client, timeout are AI-Studio specific and omitted on Vertex.
        for key in ("max_retries", "callbacks"):
            if key in self.kwargs:
                vkwargs[key] = self.kwargs[key]
        if self.kwargs.get("max_output_tokens"):
            vkwargs["max_output_tokens"] = self.kwargs["max_output_tokens"]

        thinking = self._thinking_kwargs()
        try:
            return NormalizedChatVertexAI(**vkwargs, **thinking)
        except Exception as exc:
            # Older langchain-google-vertexai may not accept thinking_*
            # kwargs. Degrade rather than break the analysis — construct
            # without them (thinking still defaults sensibly server-side).
            if thinking:
                log.warning(
                    "ChatVertexAI rejected thinking kwargs %s (%s) — "
                    "constructing without them", list(thinking), exc,
                )
                return NormalizedChatVertexAI(**vkwargs)
            raise

    def validate_model(self) -> bool:
        """Validate model for Google."""
        return validate_model("google", self.model)
