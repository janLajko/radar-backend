from __future__ import annotations

from typing import TYPE_CHECKING

from radar_backend.llm.provider import LLMProvider

if TYPE_CHECKING:
    from radar_backend.config import Settings


def build_provider(settings: "Settings", model: str | None = None) -> LLMProvider:
    """Instantiate the configured LLM provider.

    Controlled by ``settings.llm_provider``. Currently supported values:
    - ``"openai"`` — OpenAI chat completions API
    - ``"anthropic"`` or ``"claude"`` — Anthropic Messages API

    To add a new provider (e.g. Anthropic, Gemini):
    1. Create ``src/radar_backend/llm/providers/<name>.py`` implementing
       the ``LLMProvider`` protocol.
    2. Add an ``elif`` branch here.
    """
    provider = settings.llm_provider.lower()
    model_name = model or settings.llm_model
    if provider == "openai":
        from radar_backend.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.llm_api_key,
            model=model_name,
        )
    if provider in {"anthropic", "claude"}:
        from radar_backend.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key or settings.llm_api_key,
            model=model_name,
        )
    raise ValueError(f"unsupported llm_provider: {settings.llm_provider!r}")
