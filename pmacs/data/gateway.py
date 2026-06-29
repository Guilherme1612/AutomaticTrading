"""Rate-limited HTTP gateway with TokenBucket per source (Architecture.md §6).
Also contains sanitize_evidence() — Layer 1 prompt-injection defense (Agents.md §19.2).
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import httpx

from pmacs.logsys import log_debug

# -- Prompt-injection patterns (Agents.md §19.2) --
INJECTION_PATTERNS: list[str] = [
    r"(?i)ignore\s+(all\s+)?(previous\s+)?instructions",
    r"(?i)disregard\s+(all\s+)?(your\s+)?system\s+prompt",
    r"(?i)you\s+are\s+now\s+a",
    r"(?i)output\s+the\s+following",
    r"(?i)override\s+(your\s+)?safety",
    r"(?i)p_up\s*=\s*1\.0",
    r"(?i)p_down\s*=\s*0\.0",
]

_COMPILED_PATTERNS: list[re.Pattern[str]] = [re.compile(p) for p in INJECTION_PATTERNS]


def sanitize_evidence(
    text: str,
    source: str = "",
    cycle_id: str = "",
) -> str:
    """Strip common injection patterns from evidence text (Agents.md §19.2).

    On match, logs PROMPT_INJECTION_DETECTED (§19.3).
    Evidence is still used after sanitization — does not reject on pattern match alone.
    Never logs full evidence text (anti-pattern §16.13).
    """
    if not text:
        return text

    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            log_debug(
                "PROMPT_INJECTION_DETECTED",
                payload={
                    "matched_pattern": match.group(),
                    "source": source,
                },
                level="WARN",
                error_code="PROMPT_INJECTION_DETECTED",
                cycle_id=cycle_id,
                msg=f"Injection pattern detected from source {source}",
            )
            text = pattern.sub("[SANITIZED]", text)

    return text


class TokenBucket:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, rate: float, capacity: int | None = None):
        """
        Args:
            rate: Tokens added per second.
            capacity: Max tokens (defaults to rate * 2 for burst).
        """
        self.rate = rate
        self.capacity = capacity or int(rate * 2)
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """Wait until tokens are available. Returns True if acquired."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now


# Default rate limits per source (requests per second)
DEFAULT_RATES: dict[str, float] = {
    "polygon": 5.0,
    "finnhub": 1.0,
    "alpaca_data": 2.0,
    "edgar": 0.5,
    "openfda": 2.0,
    "finra": 1.0,
    "form4": 0.5,
    "ir_pages": 1.0,
    "press": 1.0,
    "fomc": 0.5,
    "fred": 1.0,
    "ecb": 0.5,
    "fundamentals": 2.0,
    "edgar_kpi": 0.5,  # SEC EDGAR filing-narrative KPI extraction (same host as edgar)
}


class DataGateway:
    """Rate-limited HTTP client for data sources."""

    def __init__(
        self,
        rates: dict[str, float] | None = None,
        timeout: float = 10.0,
        user_agent: str = "PMACS/0.1",
    ):
        self._rates = rates or DEFAULT_RATES
        self._buckets: dict[str, TokenBucket] = {}
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )
        for source, rate in self._rates.items():
            self._buckets[source] = TokenBucket(rate=rate)

    def fetch(
        self,
        source: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        api_key: str | None = None,
    ) -> httpx.Response:
        """Fetch URL with rate limiting and retry.

        Args:
            source: Source name for rate limiting.
            url: URL to fetch.
            params: Query parameters.
            headers: Additional headers.
            api_key: API key (added as query param or header depending on source).

        Returns:
            httpx.Response

        Raises:
            httpx.HTTPStatusError: On non-2xx after retries.
            TimeoutError: If rate limit timeout exceeded.
        """
        bucket = self._buckets.get(source)
        if bucket and not bucket.acquire():
            raise TimeoutError(f"Rate limit timeout for source: {source}")

        # Merge headers
        req_headers = dict(headers or {})
        if api_key:
            # Most APIs use query param; some use header
            if source in ("polygon", "finnhub"):
                params = dict(params or {})
                params["apiKey"] = api_key
            elif source == "alpaca_data":
                req_headers["APCA-API-KEY-ID"] = api_key

        # Retry logic for 429/5xx
        max_retries = 3
        for attempt in range(max_retries):
            response = self._client.get(url, params=params, headers=req_headers)
            if response.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
            if response.status_code >= 500:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
            response.raise_for_status()
            return response

        return response  # type: ignore

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
