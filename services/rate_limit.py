"""In-memory sliding-window rate limiter (login lockout, etc.)."""

from __future__ import annotations

import threading
import time
from typing import Dict, List


class RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, block_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.block = block_seconds
        self._hits: Dict[str, List[float]] = {}
        self._blocked_until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        """Seconds the key must wait, or 0 if not currently blocked."""
        with self._lock:
            until = self._blocked_until.get(key, 0.0)
            remaining = until - time.monotonic()
            return int(remaining) + 1 if remaining > 0 else 0

    def record(self, key: str) -> int:
        """Record a failed attempt. Returns the block duration (s) if this trips
        the limit, else 0."""
        with self._lock:
            n = time.monotonic()
            hits = [t for t in self._hits.get(key, []) if n - t < self.window]
            hits.append(n)
            self._hits[key] = hits
            if len(hits) >= self.max_attempts:
                self._blocked_until[key] = n + self.block
                self._hits[key] = []
                return self.block
            return 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
            self._blocked_until.pop(key, None)


# Login: 3 failures in 5 min → locked for 5 min, per (ip, username).
login_limiter = RateLimiter(max_attempts=3, window_seconds=300, block_seconds=300)
