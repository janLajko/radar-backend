import logging
import threading
import time
from typing import Tuple


logger = logging.getLogger(__name__)


class TokenBucket:
    def __init__(self, *, name: str, rpm: int, burst: int) -> None:
        rpm = int(rpm)
        burst = int(burst)
        if rpm <= 0:
            rpm = 1
        if burst <= 0:
            burst = 1

        self._name = name
        self._rpm = rpm
        self._burst = burst
        self._rate_per_sec = rpm / 60.0
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last_ts = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last_ts
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_sec)
        self._last_ts = now

    def try_acquire(self, tokens: int = 1) -> Tuple[bool, float]:
        if tokens <= 0:
            return True, 0.0

        limited = False
        wait_seconds = 0.0
        available = 0.0
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True, 0.0
            needed = tokens - self._tokens
            wait_seconds = needed / self._rate_per_sec if self._rate_per_sec > 0 else float("inf")
            available = self._tokens
            limited = True

        if limited:
            logger.warning(
                "rate_limit hit: bucket=%s rpm=%s burst=%s wait_seconds=%.3f available=%.3f",
                self._name,
                self._rpm,
                self._burst,
                wait_seconds,
                available,
            )
        return False, wait_seconds

    def acquire(self, tokens: int = 1) -> None:
        while True:
            ok, wait_seconds = self.try_acquire(tokens)
            if ok:
                return
            if wait_seconds <= 0:
                time.sleep(0.01)
            else:
                time.sleep(wait_seconds)
