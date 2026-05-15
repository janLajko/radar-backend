from __future__ import annotations

import time

import openai


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
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(self._max_attempts):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
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
