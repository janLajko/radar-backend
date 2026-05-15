from __future__ import annotations

from typing import TYPE_CHECKING

from radar_backend.llm.provider import LLMProvider
from radar_backend.llm.providers.openai import OpenAIProvider

if TYPE_CHECKING:
    from radar_backend.config import Settings


def build_provider(settings: "Settings") -> LLMProvider:
    """Instantiate the configured LLM provider.

    Controlled by ``settings.llm_provider``. Currently supported values:
    - ``"openai"`` — OpenAI chat completions API

    To add a new provider (e.g. Anthropic, Gemini):
    1. Create ``src/radar_backend/llm/providers/<name>.py`` implementing
       the ``LLMProvider`` protocol.
    2. Add an ``elif`` branch here.
    """
    if settings.llm_provider == "openai":
        return OpenAIProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )
    raise ValueError(f"unsupported llm_provider: {settings.llm_provider!r}")
