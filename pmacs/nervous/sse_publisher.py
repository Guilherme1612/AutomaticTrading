"""SSE publisher — fan-out event stream to all connected clients (Architecture.md §4.4).

Thread-safe publish() callable from any thread.
Each client gets its own asyncio.Queue; events are JSON-serialized per SSE frame.
Auto-incrementing event IDs for Last-Event-ID reconnection support.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any


class SSEPublisher:
    """Publish SSE events to all connected clients.

    Usage:
        publisher = SSEPublisher()
        publisher.publish("cycle", "cycle.open", {"cycle_id": "..."})

    Supports Last-Event-ID reconnection via an internal ring buffer
    of the last 1000 events.
    """

    RING_BUFFER_SIZE = 1000

    def __init__(self) -> None:
        self._clients: dict[int, asyncio.Queue[str]] = {}
        self._lock = threading.Lock()
        self._next_id: int = 1
        # Ring buffer: list of (event_id_int, frame_str) for Last-Event-ID resume
        self._event_log: list[tuple[int, str]] = []

    def _next_event_id(self) -> str:
        with self._lock:
            eid = self._next_id
            self._next_id += 1
            return str(eid)

    def subscribe(self) -> tuple[int, asyncio.Queue[str]]:
        """Register a new client. Returns (client_id, queue)."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1024)
        with self._lock:
            cid = id(queue)
            self._clients[cid] = queue
        return cid, queue

    def unsubscribe(self, client_id: int) -> None:
        """Remove a client."""
        with self._lock:
            self._clients.pop(client_id, None)

    def publish(self, stream: str, event_type: str, data: dict[str, Any]) -> str:
        """Emit an event to all connected clients.

        Thread-safe — can be called from any thread.

        Args:
            stream: Stream category (cycle, agent, decision, trade, mutation, system).
            event_type: Specific event type (e.g. cycle.open, cycle.close).
            data: Event payload.

        Returns:
            The event ID assigned to this event.
        """
        event_id = self._next_event_id()
        event = {
            "stream": stream,
            "type": event_type,
            "data": data,
            "id": event_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        frame = json.dumps(event, separators=(",", ":"))

        with self._lock:
            # Append to ring buffer for Last-Event-ID resume
            self._event_log.append((int(event_id), frame))
            if len(self._event_log) > self.RING_BUFFER_SIZE:
                self._event_log = self._event_log[-self.RING_BUFFER_SIZE:]

            dead: list[int] = []
            for cid, queue in self._clients.items():
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    dead.append(cid)
            for cid in dead:
                self._clients.pop(cid, None)

        return event_id

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def last_event_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    def get_events_since(self, last_id: int) -> list[str]:
        """Return frames with event_id > last_id from the ring buffer.

        Used for Last-Event-ID reconnection: the client sends the last
        event ID it received, and this method returns all missed events.

        Args:
            last_id: The last event ID the client received.

        Returns:
            List of JSON-serialized event frames in order.
        """
        with self._lock:
            return [frame for eid, frame in self._event_log if eid > last_id]


# ── Module-level singleton for cross-module system events ────────────────────
# Set once by the nervous API on startup; other modules (kill switch, cortex)
# can publish system events without holding a direct reference.

_global_publisher: "SSEPublisher | None" = None


def set_global_publisher(publisher: "SSEPublisher") -> None:
    """Register the process-wide SSE publisher for system events."""
    global _global_publisher
    _global_publisher = publisher


def publish_system_event(event_type: str, data: dict[str, Any]) -> None:
    """Emit a system.* SSE event from any module.

    No-op if the global publisher has not been set (e.g. during tests
    or when running outside the nervous process).
    """
    if _global_publisher is not None:
        _global_publisher.publish("system", event_type, data)
