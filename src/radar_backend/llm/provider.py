from __future__ import annotations

from typing import Protocol


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
