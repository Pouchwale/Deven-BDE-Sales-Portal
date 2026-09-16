"""A small in-process rate limiter for the login endpoint.

Deliberately in-memory: it protects a single uvicorn process against
credential stuffing and nothing more. Running several workers, or more than
one host, needs a shared store (Redis) - noted in the README rather than
pretended away here.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Record an attempt. Returns (allowed, seconds_until_retry)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                return False, int(hits[0] + self.window_seconds - now) + 1
            hits.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        """Called on a successful login so one typo does not cost a lockout."""
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
