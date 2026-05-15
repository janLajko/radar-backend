from __future__ import annotations

import time

import httpx


class HttpClient:
    """Thin httpx wrapper with per-request retry (up to 3 attempts, exponential backoff)."""

    def __init__(self, timeout: float = 30.0, max_attempts: int = 3) -> None:
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)
        self._max_attempts = max_attempts

    def get(self, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(self._max_attempts):
            try:
                resp = self._client.get(url, **kwargs)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < self._max_attempts - 1:
                    time.sleep(2**attempt)
        raise last_exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
