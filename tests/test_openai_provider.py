from __future__ import annotations

from types import SimpleNamespace

import radar_backend.llm.providers.openai as openai_provider
from radar_backend.llm.providers.openai import (
    OpenAIProvider,
    _max_output_tokens,
    _token_limit_param,
)


class FakeChatCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"),
                    finish_reason="stop",
                )
            ]
        )


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.completions = FakeChatCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_token_limit_param_uses_completion_tokens_for_gpt5_and_o_models() -> None:
    assert _token_limit_param("gpt-5") == "max_completion_tokens"
    assert _token_limit_param("gpt-5.1") == "max_completion_tokens"
    assert _token_limit_param("o3") == "max_completion_tokens"
    assert _token_limit_param("gpt-4o") == "max_tokens"


def test_max_output_tokens_uses_known_model_caps() -> None:
    assert _max_output_tokens("gpt-5") == 128000
    assert _max_output_tokens("gpt-5.2") == 128000
    assert _max_output_tokens("gpt-4.1-mini") == 32768
    assert _max_output_tokens("gpt-4o") == 16384
    assert _max_output_tokens("unknown-model") is None


def test_openai_provider_sends_model_max_completion_tokens_for_gpt5(monkeypatch) -> None:
    fake_client = FakeOpenAIClient()
    monkeypatch.setattr(openai_provider.openai, "OpenAI", lambda api_key: fake_client)

    provider = OpenAIProvider(api_key="sk-test", model="gpt-5")
    assert provider.complete("system", "user") == "ok"

    # assert fake_client.completions.kwargs["max_completion_tokens"] == 128000
    assert "max_tokens" not in fake_client.completions.kwargs
