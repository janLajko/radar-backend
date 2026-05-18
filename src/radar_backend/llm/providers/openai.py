from __future__ import annotations

import json
import logging
import time
from typing import Any

import openai

from radar_backend.llm.provider import ToolDefinition, ToolDispatcher

logger = logging.getLogger(__name__)

_MODEL_MAX_OUTPUT_TOKENS = {
    "gpt-5.5": 128000,
    "gpt-5.4": 128000,
    "gpt-5.3": 128000,
    "gpt-5.2": 128000,
    "gpt-5.1": 128000,
    "gpt-5": 128000,
    "gpt-4.1": 32768,
    "gpt-4o": 16384,
}


class OpenAIProvider:
    """LLMProvider implementation backed by the OpenAI chat completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_attempts: int = 3,
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model
        self._max_attempts = max_attempts

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        response = self._create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("LLM returned empty content")
        return content

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[ToolDefinition],
        dispatch_tool: ToolDispatcher,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        openai_tools = [_to_openai_tool(tool) for tool in tools]

        for iteration in range(max_iterations):
            response = self._create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                tools=openai_tools,
            )
            message = response.choices[0].message
            tool_calls = message.tool_calls or []

            logger.debug(
                "openai_provider: tool_loop iteration=%d finish_reason=%s tool_calls=%d model=%s",
                iteration,
                response.choices[0].finish_reason,
                len(tool_calls),
                self._model,
            )

            if not tool_calls:
                if message.content is None:
                    raise ValueError("LLM returned empty content")
                return message.content

            messages.append(message.model_dump(exclude_none=True))
            for tool_call in tool_calls:
                if tool_call.type != "function":
                    continue
                try:
                    inputs = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid tool arguments for {tool_call.function.name}: {exc}"
                    ) from exc
                logger.info(
                    "openai_provider: tool=%s inputs=%s model=%s",
                    tool_call.function.name,
                    json.dumps(inputs)[:200],
                    self._model,
                )
                result = dispatch_tool(tool_call.function.name, inputs)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

        raise ValueError(f"tool loop exceeded {max_iterations} iterations without final text")

    def _create_chat_completion(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ):
        last_exc: Exception = RuntimeError("no attempts made")
        token_param = _token_limit_param(self._model)
        token_limit = _max_output_tokens(self._model) or max_tokens
        for attempt in range(self._max_attempts):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                }
                # kwargs[token_param] = token_limit
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                return self._client.chat.completions.create(**kwargs)
            except openai.APIConnectionError as exc:
                last_exc = exc
                if attempt < self._max_attempts - 1:
                    time.sleep(2**attempt)
            except openai.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_exc = exc
                    if attempt < self._max_attempts - 1:
                        time.sleep(2**attempt)
                else:
                    if _is_unsupported_token_param(exc, token_param):
                        token_param = _alternate_token_param(token_param)
                        last_exc = exc
                        continue
                    raise
        raise last_exc


def _to_openai_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


def _token_limit_param(model: str) -> str:
    model = model.lower()
    if model.startswith("gpt-5") or model.startswith("o"):
        return "max_completion_tokens"
    return "max_tokens"


def _max_output_tokens(model: str) -> int | None:
    model = model.lower()
    matches = (
        (prefix, max_tokens)
        for prefix, max_tokens in _MODEL_MAX_OUTPUT_TOKENS.items()
        if model == prefix or model.startswith(f"{prefix}-")
    )
    return next(matches, (None, None))[1]


def _alternate_token_param(param: str) -> str:
    if param == "max_tokens":
        return "max_completion_tokens"
    return "max_tokens"


def _is_unsupported_token_param(exc: openai.APIStatusError, param: str) -> bool:
    if exc.status_code != 400:
        return False
    message = str(exc).lower()
    return "unsupported parameter" in message and param.lower() in message
