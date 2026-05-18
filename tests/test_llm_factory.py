from __future__ import annotations

import sys
import types

from radar_backend.llm.factory import build_provider


class FakeOpenAIProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model


def test_build_provider_accepts_model_override(monkeypatch) -> None:
    module = types.ModuleType("radar_backend.llm.providers.openai")
    module.OpenAIProvider = FakeOpenAIProvider
    monkeypatch.setitem(sys.modules, "radar_backend.llm.providers.openai", module)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "default-model")

    provider = build_provider(model="stage-model")

    assert isinstance(provider, FakeOpenAIProvider)
    assert provider.model == "stage-model"
