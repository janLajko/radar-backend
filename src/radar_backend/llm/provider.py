from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

ToolDispatcher = Callable[[str, dict[str, Any]], str]
ToolDefinition = dict[str, Any]


class LLMProvider(Protocol):
    """Strategy interface for LLM backends.

    All providers must implement a single ``complete`` method that sends a
    system + user message and returns the model's text reply. Retry logic is
    the provider's responsibility; callers treat any raised exception as a
    non-retryable failure at the stage level.
    """

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        """Return the model's text reply for the given system/user messages."""
        ...

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[ToolDefinition],
        dispatch_tool: ToolDispatcher,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        """Run a tool-calling loop and return the final model text reply."""
        ...
