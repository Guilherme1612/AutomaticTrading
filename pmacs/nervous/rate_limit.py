"""Rate limiting — token bucket implementation (Architecture.md §16.3).

MUST use BUCKETS["source"].acquire() for all rate limiting.
Custom rate-limit logic is an anti-pattern.
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    """Thread-safe token bucket rate limiter.

    Args:
        rate: Maximum number of tokens per period.
        period: Time period in seconds.
    """

    def __init__(self, rate: int = 5, period: float = 60.0) -> None:
        self._rate = rate
        self._period = period
        self._tokens: float = float(rate)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        tokens_to_add = elapsed * (self._rate / self._period)
        self._tokens = min(float(self._rate), self._tokens + tokens_to_add)
        self._last_refill = now

    def acquire(self) -> bool:
        """Try to acquire one token. Returns True if allowed, False if rate limited."""
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    @property
    def available_tokens(self) -> float:
        """Current number of available tokens (for diagnostics)."""
        with self._lock:
            self._refill()
            return self._tokens


# Named bucket instances for different rate limit categories
BUCKETS: dict[str, TokenBucket] = {}
