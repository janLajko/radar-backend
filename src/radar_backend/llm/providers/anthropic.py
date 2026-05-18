from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic

from radar_backend.llm.provider import ToolDefinition, ToolDispatcher

logger = logging.getLogger(__name__)


class AnthropicProvider:
    """LLMProvider implementation backed by Anthropic Messages API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_attempts: int = 3,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_attempts = max_attempts

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        response = self._create_message(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
        )
        text = _anthropic_text(response)
        if not text:
            raise ValueError("LLM returned empty content")
        return text

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[ToolDefinition],
        dispatch_tool: ToolDispatcher,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

        for iteration in range(max_iterations):
            response = self._create_message(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                tools=tools,
            )

            logger.debug(
                "anthropic_provider: tool_loop iteration=%d stop_reason=%s",
                iteration,
                response.stop_reason,
            )

            if response.stop_reason == "end_turn":
                text = _anthropic_text(response)
                if not text:
                    raise ValueError("LLM returned empty content")
                return text

            tool_results: list[dict[str, Any]] = []
            has_tool_use = False
            for block in response.content:
                if block.type != "tool_use":
                    continue
                has_tool_use = True
                logger.info(
                    "anthropic_provider: tool=%s inputs=%s",
                    block.name,
                    json.dumps(block.input)[:200],
                )
                result = dispatch_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            if not has_tool_use:
                text = _anthropic_text(response)
                if not text:
                    raise ValueError("LLM returned empty content")
                return text

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        raise ValueError(f"tool loop exceeded {max_iterations} iterations without final text")

    def _create_message(
        self,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[ToolDefinition] | None = None,
    ) -> anthropic.types.Message:
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(self._max_attempts):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": messages,
                }
                if tools:
                    kwargs["tools"] = tools
                return self._client.messages.create(**kwargs)
            except anthropic.APIConnectionError as exc:
                last_exc = exc
                if attempt < self._max_attempts - 1:
                    time.sleep(2**attempt)
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_exc = exc
                    if attempt < self._max_attempts - 1:
                        time.sleep(2**attempt)
                else:
                    raise
        raise last_exc


def _anthropic_text(response: anthropic.types.Message) -> str:
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text
    return text
