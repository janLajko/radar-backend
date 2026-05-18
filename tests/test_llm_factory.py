from __future__ import annotations

import sys
import types

from radar_backend.config import Settings
from radar_backend.llm.factory import build_provider


class FakeOpenAIProvider:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model


def test_build_provider_accepts_model_override(monkeypatch) -> None:
    module = types.ModuleType("radar_backend.llm.providers.openai")
    module.OpenAIProvider = FakeOpenAIProvider
    monkeypatch.setitem(sys.modules, "radar_backend.llm.providers.openai", module)

    provider = build_provider(
        Settings(
            database_dsn_radar="postgresql://example/test",
            source_config_path="/etc/radar/sources.yaml",
            llm_api_key="sk-test",
            llm_model="default-model",
        ),
        model="stage-model",
    )

    assert isinstance(provider, FakeOpenAIProvider)
    assert provider.model == "stage-model"
