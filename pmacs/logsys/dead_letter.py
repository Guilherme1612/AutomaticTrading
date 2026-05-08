"""Dead-letter queue for failed write operations (Architecture.md §9).

When a write to Qdrant, KuzuDB, or another store fails after retries,
the operation is enqueued here for later re-processing or operator
investigation.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DeadLetterEntry:
    """A single dead-letter entry."""

    id: str
    target: str  # e.g. "qdrant_write", "kuzu_execute"
    payload: str  # JSON-serialized payload that failed
    error: str
    attempts: int = 0
    max_attempts: int = 3
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_attempt_at: datetime | None = None
    status: str = "PENDING"  # PENDING | RETRYING | COMPLETED | EXHAUSTED


class DeadLetterQueue:
    """In-memory dead-letter queue with retry logic.

    Not persisted to disk — entries survive only for the lifetime of the
    process.  Exhausted entries should be surfaced to the operator via the
    dashboard for manual investigation.
    """

    def __init__(self, max_attempts: int = 3, retry_delay_s: float = 60.0) -> None:
        self.max_attempts = max_attempts
        self.retry_delay_s = retry_delay_s
        self._queue: list[DeadLetterEntry] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, target: str, payload: dict, error: str) -> DeadLetterEntry:
        """Add a failed operation to the queue."""
        entry = DeadLetterEntry(
            id=f"dl_{int(time.time())}_{len(self._queue)}",
            target=target,
            payload=json.dumps(payload),
            error=error,
            max_attempts=self.max_attempts,
        )
        self._queue.append(entry)
        return entry

    def get_pending(self) -> list[DeadLetterEntry]:
        """Return entries that are ready for a retry attempt."""
        now = time.time()
        pending: list[DeadLetterEntry] = []
        for entry in self._queue:
            if entry.status not in ("PENDING", "RETRYING"):
                continue
            if entry.attempts >= entry.max_attempts:
                continue
            if entry.last_attempt_at is None:
                pending.append(entry)
            elif (now - entry.last_attempt_at.timestamp()) >= self.retry_delay_s:
                pending.append(entry)
        return pending

    def mark_retry(self, entry_id: str) -> None:
        """Record a retry attempt for *entry_id*."""
        for entry in self._queue:
            if entry.id == entry_id:
                entry.attempts += 1
                entry.last_attempt_at = datetime.utcnow()
                if entry.attempts >= entry.max_attempts:
                    entry.status = "EXHAUSTED"
                else:
                    entry.status = "RETRYING"
                break

    def mark_completed(self, entry_id: str) -> None:
        """Mark *entry_id* as successfully completed."""
        for entry in self._queue:
            if entry.id == entry_id:
                entry.status = "COMPLETED"
                break

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        return sum(1 for e in self._queue if e.status in ("PENDING", "RETRYING"))

    @property
    def exhausted_count(self) -> int:
        return sum(1 for e in self._queue if e.status == "EXHAUSTED")

    @property
    def total_count(self) -> int:
        return len(self._queue)
