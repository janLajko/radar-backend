from __future__ import annotations

import json
import logging
import time
from typing import Any

import openai

from radar_backend.llm.provider import ToolDefinition, ToolDispatcher

logger = logging.getLogger(__name__)


class OpenAIProvider:
    """LLMProvider implementation backed by the OpenAI Responses API."""

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
        response = self._create_response(
            input_items=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return _response_text(response)

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[ToolDefinition],
        dispatch_tool: ToolDispatcher,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        input_items: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        openai_tools = [_to_openai_tool(tool) for tool in tools]

        for iteration in range(max_iterations):
            response = self._create_response(
                input_items=input_items,
                tools=openai_tools,
            )
            tool_calls = _function_calls(response)

            logger.debug(
                "openai_provider: tool_loop iteration=%d status=%s tool_calls=%d model=%s",
                iteration,
                getattr(response, "status", None),
                len(tool_calls),
                self._model,
            )

            if not tool_calls:
                return _response_text(response)

            input_items.extend(_response_output_items(response))
            for tool_call in tool_calls:
                try:
                    inputs = json.loads(tool_call.arguments or "{}")
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid tool arguments for {tool_call.name}: {exc}"
                    ) from exc
                logger.info(
                    "openai_provider: tool=%s inputs=%s model=%s",
                    tool_call.name,
                    json.dumps(inputs)[:200],
                    self._model,
                )
                result = dispatch_tool(tool_call.name, inputs)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tool_call.call_id,
                    "output": result,
                })

        raise ValueError(f"tool loop exceeded {max_iterations} iterations without final text")

    def _create_response(
        self,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ):
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(self._max_attempts):
            try:
                kwargs: dict[str, Any] = {
                    "model": self._model,
                    "input": input_items,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                return self._client.responses.create(**kwargs)
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
                    raise
        raise last_exc


def _to_openai_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": tool["input_schema"],
        "strict": tool.get("strict", False),
    }


def _function_calls(response: Any) -> list[Any]:
    return [
        item
        for item in getattr(response, "output", []) or []
        if getattr(item, "type", None) == "function_call"
    ]


def _response_output_items(response: Any) -> list[dict[str, Any]]:
    return [
        item.model_dump(exclude_none=True)
        for item in getattr(response, "output", []) or []
    ]


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    text_parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "output_text":
                text_parts.append(getattr(content, "text", ""))

    content = "".join(text_parts)
    if content:
        return content
    raise ValueError("LLM returned empty content")
