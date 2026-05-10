from typing import Any, Optional

from langchain_google_genai import ChatGoogleGenerativeAI

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model


class NormalizedChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI with normalized content output.

    Gemini 3 models return content as list of typed blocks.
    This normalizes to string for consistent downstream handling.
    """

    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class GoogleClient(BaseLLMClient):
    """Client for Google Gemini models."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatGoogleGenerativeAI instance."""
        self.warn_if_unknown_model()
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

        # Map thinking_level to appropriate API param based on model
        # Gemini 3 Pro: low, high
        # Gemini 3 Flash: minimal, low, medium, high
        # Gemini 2.5 Pro: thinking required (rejects thinking_budget=0)
        # Gemini 2.5 Flash/Flash-Lite: thinking_budget (0=disable, -1=dynamic)
        thinking_level = self.kwargs.get("thinking_level")
        if thinking_level:
            model_lower = self.model.lower()
            if "gemini-3" in model_lower:
                # Gemini 3 Pro doesn't support "minimal", use "low" instead
                if "pro" in model_lower and thinking_level == "minimal":
                    thinking_level = "low"
                llm_kwargs["thinking_level"] = thinking_level
            elif "pro" in model_lower:
                # Gemini 2.5 Pro requires thinking — thinking_budget=0
                # raises 400 'Budget 0 is invalid. This model only works
                # in thinking mode.' Previously we used dynamic budget
                # (-1) which let the model think for as long as it
                # wanted; on the AMAT 2026-05-10 batch this routinely
                # consumed thousands of thinking tokens per Pro call,
                # making the decision tier ~50% of total analysis cost.
                # Capping at 4096 keeps enough room for the bull/bear
                # synthesis the decision nodes actually need (research
                # manager / trader / portfolio manager outputs are
                # rarely longer than 1.5K tokens) while cutting the
                # silent thinking overhead by ~70%. If a future case
                # genuinely needs more thinking, raise the cap rather
                # than going back to unbounded dynamic.
                llm_kwargs["thinking_budget"] = 4096
            else:
                # Gemini 2.5 Flash / Flash-Lite: map to thinking_budget
                llm_kwargs["thinking_budget"] = -1 if thinking_level == "high" else 0

        return NormalizedChatGoogleGenerativeAI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Google."""
        return validate_model("google", self.model)
