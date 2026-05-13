"""SQLite-backed dead-letter queue for failed write operations (Architecture.md §9, §14.1).

When a write to Qdrant, KuzuDB, or another store fails after retries,
the operation is persisted here for later re-processing or operator
investigation.  Entries survive process restarts.

Backoff schedule: 1s, 5s, 30s, 5min, 1h, 1d (6 steps).
After 6 attempts: status=FAILED, DEAD_LETTER_EXHAUSTED debug event emitted.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Architecture.md §14.1 backoff schedule
DEFAULT_BACKOFF_SCHEDULE: list[float] = [1, 5, 30, 300, 3600, 86400]

MAX_ATTEMPTS = 6


@dataclass
class DeadLetterEntry:
    """A single dead-letter entry."""

    id: int | None
    op_type: str
    target_db: str
    payload: str
    queued_at: str
    retry_count: int = 0
    last_attempt_at: str | None = None
    last_error: str | None = None
    status: str = "PENDING"  # PENDING | RETRYING | RESOLVED | FAILED


class DeadLetterStore:
    """SQLite-persisted dead-letter queue with retry logic.

    Usage::

        store = DeadLetterStore(conn)
        store.enqueue("qdrant_upsert", "qdrant", {"collection": "theses", ...})
        entry = store.process_next()
        if entry and attempt_succeeded:
            store.mark_resolved(entry.id)
        else:
            store.mark_failed(entry.id, "connection refused")
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        backoff_schedule: list[float] | None = None,
    ) -> None:
        self._conn = conn
        self._backoff = backoff_schedule or list(DEFAULT_BACKOFF_SCHEDULE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, op_type: str, target_db: str, payload: dict[str, Any]) -> DeadLetterEntry:
        """Persist a failed operation to the dead-letter queue.

        Args:
            op_type: Operation type (e.g. "qdrant_upsert", "kuzu_execute").
            target_db: Target store name (e.g. "qdrant", "kuzudb").
            payload: The operation payload that failed.

        Returns:
            The persisted DeadLetterEntry with its assigned id.
        """
        import json

        now = datetime.now(timezone.utc).isoformat()
        entry = DeadLetterEntry(
            id=None,
            op_type=op_type,
            target_db=target_db,
            payload=json.dumps(payload, sort_keys=True),
            queued_at=now,
        )

        cursor = self._conn.execute(
            "INSERT INTO dead_letter (op_type, target_db, payload, queued_at, retry_count, status) "
            "VALUES (?, ?, ?, ?, 0, 'PENDING')",
            (entry.op_type, entry.target_db, entry.payload, entry.queued_at),
        )
        self._conn.commit()
        entry.id = cursor.lastrowid
        return entry

    def process_next(self) -> DeadLetterEntry | None:
        """Return the next entry ready for a retry attempt.

        Uses the backoff schedule to determine readiness.
        Returns None if no entries are ready.
        """
        now = time.time()
        rows = self._conn.execute(
            "SELECT id, op_type, target_db, payload, queued_at, retry_count, "
            "last_attempt_at, last_error, status "
            "FROM dead_letter WHERE status IN ('PENDING', 'RETRYING') "
            "ORDER BY queued_at ASC"
        ).fetchall()

        for row in rows:
            entry = self._row_to_entry(row)
            if entry.retry_count >= MAX_ATTEMPTS:
                continue
            if entry.last_attempt_at is None:
                return entry
            # Check backoff delay
            idx = min(entry.retry_count, len(self._backoff) - 1)
            delay = self._backoff[idx]
            last_ts = datetime.fromisoformat(entry.last_attempt_at).timestamp()
            if (now - last_ts) >= delay:
                return entry
        return None

    def mark_failed(self, entry_id: int, error: str = "") -> None:
        """Record a failed retry attempt for *entry_id*.

        Increments retry_count. If max attempts reached, sets status=FAILED
        and emits DEAD_LETTER_EXHAUSTED debug event.
        """
        now = datetime.now(timezone.utc).isoformat()
        row = self._conn.execute(
            "SELECT retry_count FROM dead_letter WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return

        new_count = row[0] + 1
        if new_count >= MAX_ATTEMPTS:
            self._conn.execute(
                "UPDATE dead_letter SET retry_count = ?, last_attempt_at = ?, "
                "last_error = ?, status = 'FAILED' WHERE id = ?",
                (new_count, now, error, entry_id),
            )
            self._conn.commit()
            self._emit_exhausted(entry_id, error)
        else:
            self._conn.execute(
                "UPDATE dead_letter SET retry_count = ?, last_attempt_at = ?, "
                "last_error = ?, status = 'RETRYING' WHERE id = ?",
                (new_count, now, error, entry_id),
            )
            self._conn.commit()

    def mark_resolved(self, entry_id: int) -> None:
        """Mark *entry_id* as successfully resolved."""
        self._conn.execute(
            "UPDATE dead_letter SET status = 'RESOLVED' WHERE id = ?",
            (entry_id,),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM dead_letter WHERE status IN ('PENDING', 'RETRYING')"
        ).fetchone()
        return row[0] if row else 0

    @property
    def failed_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM dead_letter WHERE status = 'FAILED'"
        ).fetchone()
        return row[0] if row else 0

    @property
    def total_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM dead_letter").fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _row_to_entry(self, row: tuple[Any, ...]) -> DeadLetterEntry:
        return DeadLetterEntry(
            id=row[0],
            op_type=row[1],
            target_db=row[2],
            payload=row[3],
            queued_at=row[4],
            retry_count=row[5],
            last_attempt_at=row[6],
            last_error=row[7],
            status=row[8],
        )

    def _emit_exhausted(self, entry_id: int, error: str) -> None:
        """Emit DEAD_LETTER_EXHAUSTED debug event (Architecture.md §5)."""
        try:
            from pmacs.logsys.debug_log import log_debug, LogLevel

            log_debug(
                "DEAD_LETTER_EXHAUSTED",
                payload={"entry_id": entry_id, "error": error},
                level=LogLevel.WARN,
                error_code="DLQ_EXHAUSTED",
                msg=f"Dead letter entry {entry_id} exhausted after {MAX_ATTEMPTS} attempts",
            )
        except Exception:
            pass  # Debug log failure must not block the store
