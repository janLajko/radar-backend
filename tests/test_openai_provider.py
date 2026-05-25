from __future__ import annotations

from types import SimpleNamespace

import radar_backend.llm.providers.openai as openai_provider
from radar_backend.llm.providers.openai import (
    OpenAIProvider,
    _to_openai_tool,
)


class FakeOutputItem(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False):
        data = vars(self).copy()
        if exclude_none:
            data = {key: value for key, value in data.items() if value is not None}
        return data


class FakeResponses:
    def __init__(self, responses=None) -> None:
        self.calls = []
        self.responses = list(responses or [])

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        return SimpleNamespace(output_text="ok", output=[], status="completed")


class FakeOpenAIClient:
    def __init__(self, responses=None) -> None:
        self.responses = FakeResponses(responses)


def test_openai_provider_sends_responses_input_without_token_limit(monkeypatch) -> None:
    fake_client = FakeOpenAIClient()
    monkeypatch.setattr(openai_provider.openai, "OpenAI", lambda api_key: fake_client)

    provider = OpenAIProvider(api_key="sk-test", model="gpt-5")
    assert provider.complete("system", "user") == "ok"

    kwargs = fake_client.responses.calls[0]
    assert kwargs["input"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
    ]
    assert "messages" not in kwargs
    assert "max_output_tokens" not in kwargs


def test_to_openai_tool_uses_responses_function_schema() -> None:
    tool = {
        "name": "lookup",
        "description": "Lookup a value.",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    }

    assert _to_openai_tool(tool) == {
        "type": "function",
        "name": "lookup",
        "description": "Lookup a value.",
        "parameters": tool["input_schema"],
        "strict": False,
    }


def test_openai_provider_sends_function_call_outputs(monkeypatch) -> None:
    function_call = FakeOutputItem(
        type="function_call",
        call_id="call_123",
        name="lookup",
        arguments='{"value": "abc"}',
        id="fc_123",
        status="completed",
    )
    fake_client = FakeOpenAIClient([
        SimpleNamespace(output_text="", output=[function_call], status="completed"),
        SimpleNamespace(output_text="final", output=[], status="completed"),
    ])
    monkeypatch.setattr(openai_provider.openai, "OpenAI", lambda api_key: fake_client)

    provider = OpenAIProvider(api_key="sk-test", model="gpt-5")
    result = provider.complete_with_tools(
        "system",
        "user",
        [
            {
                "name": "lookup",
                "description": "Lookup a value.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        lambda name, inputs: f"{name}:{inputs['value']}",
    )

    assert result == "final"
    second_input = fake_client.responses.calls[1]["input"]
    assert second_input[-2] == function_call.model_dump(exclude_none=True)
    assert second_input[-1] == {
        "type": "function_call_output",
        "call_id": "call_123",
        "output": "lookup:abc",
    }
