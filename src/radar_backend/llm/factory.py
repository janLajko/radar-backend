from __future__ import annotations

from radar_backend import config
from radar_backend.llm.provider import LLMProvider


def build_provider(model: str | None = None) -> LLMProvider:
    """Instantiate the configured LLM provider.

    Controlled by ``LLM_PROVIDER``. Currently supported values:
    - ``"openai"`` — OpenAI Responses API
    - ``"anthropic"`` or ``"claude"`` — Anthropic Messages API

    To add a new provider (e.g. Anthropic, Gemini):
    1. Create ``src/radar_backend/llm/providers/<name>.py`` implementing
       the ``LLMProvider`` protocol.
    2. Add an ``elif`` branch here.
    """
    provider = config.llm_provider().lower()
    model_name = model or config.llm_model()
    if provider == "openai":
        from radar_backend.llm.providers.openai import OpenAIProvider

        return OpenAIProvider(
            api_key=config.llm_api_key(),
            model=model_name,
        )
    if provider in {"anthropic", "claude"}:
        from radar_backend.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            api_key=config.anthropic_api_key() or config.llm_api_key(),
            model=model_name,
        )
    raise ValueError(f"unsupported llm_provider: {provider!r}")
