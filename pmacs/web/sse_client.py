"""SSE client subscribing to pmacs-nervous /events stream."""

import json
import logging
import threading
import time
from typing import Callable

import httpx

from pmacs.logsys.debug_log import log_debug

logger = logging.getLogger(__name__)


class SSEClient:
    """Subscribes to pmacs-nervous /events SSE stream and dispatches to handlers."""

    def __init__(self, nervous_url: str = "http://127.0.0.1:8000"):
        self.nervous_url = nervous_url
        self.handlers: dict[str, list[Callable]] = {}
        self._running = False

    def on(self, stream: str, handler: Callable) -> None:
        """Register a handler for a named SSE stream."""
        self.handlers.setdefault(stream, []).append(handler)

    def start(self) -> None:
        """Start the SSE listener in a daemon thread."""
        self._running = True
        thread = threading.Thread(target=self._listen, daemon=True)
        thread.start()
        logger.info("SSE client started, connecting to %s/events", self.nervous_url)

    def stop(self) -> None:
        """Stop the SSE listener."""
        self._running = False
        logger.info("SSE client stopped")

    def _listen(self) -> None:
        """Main listen loop with automatic reconnection."""
        while self._running:
            try:
                with httpx.stream(
                    "GET",
                    f"{self.nervous_url}/events",
                    timeout=httpx.Timeout(None),
                ) as response:
                    for line in response.iter_lines():
                        if not self._running:
                            break
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                                stream_name = data.get("stream", "")
                                for handler in self.handlers.get(stream_name, []):
                                    try:
                                        handler(data)
                                    except Exception:
                                        logger.exception(
                                            "SSE handler error for stream=%s",
                                            stream_name,
                                        )
                            except json.JSONDecodeError:
                                log_debug(
                                    "SSE_INVALID_JSON",
                                    payload={"line": line[:200]},
                                    level="WARN",
                                    error_code="SSE_CONNECTION_FAILED",
                                    msg="SSE: invalid JSON in data line",
                                )
            except Exception:
                log_debug(
                    "SSE_CONNECTION_LOST",
                    payload={},
                    level="WARN",
                    error_code="SSE_CONNECTION_FAILED",
                    msg="SSE connection lost, reconnecting in 5s",
                )
                time.sleep(5)
