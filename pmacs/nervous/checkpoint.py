"""Checkpoint system for cycle resume/idempotency (Architecture.md §4.4, §8.5).

Uses the op_idempotency SQLite table to track completed operations
within a cycle. Enables crash-safe resume: after restart, completed
ops are skipped.
"""
from __future__ import annotations

import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class CheckpointState:
    """State of a completed operation checkpoint."""

    cycle_id: str
    op_seq: int
    op_type: str
    completed_at: str
    result_hash: str | None


def save_checkpoint(
    cycle_id: str,
    op_seq: int,
    op_type: str,
    db_path: Path,
    result_hash: str | None = None,
) -> None:
    """Record a completed operation in the idempotency table.

    Args:
        cycle_id: The cycle this operation belongs to.
        op_seq: Sequence number of the operation within the cycle.
        op_type: Type of operation (e.g. 'fetch_data', 'run_persona').
        db_path: Path to the SQLite database.
        result_hash: Optional hash of the operation result.
    """
    conn = _sql_connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO op_idempotency (cycle_id, op_seq, op_type, completed_at, result_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                cycle_id,
                op_seq,
                op_type,
                datetime.now(timezone.utc).isoformat(),
                result_hash,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_checkpoint(cycle_id: str, db_path: Path) -> CheckpointState | None:
    """Load the last checkpoint for a cycle.

    Args:
        cycle_id: The cycle to load checkpoint for.
        db_path: Path to the SQLite database.

    Returns:
        CheckpointState of the last completed op, or None if no checkpoints.
    """
    conn = _sql_connect(db_path)
    try:
        row = conn.execute(
            "SELECT cycle_id, op_seq, op_type, completed_at, result_hash "
            "FROM op_idempotency "
            "WHERE cycle_id = ? "
            "ORDER BY op_seq DESC LIMIT 1",
            (cycle_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return CheckpointState(
        cycle_id=row[0],
        op_seq=row[1],
        op_type=row[2],
        completed_at=row[3],
        result_hash=row[4],
    )


def is_completed(cycle_id: str, op_seq: int, db_path: Path) -> bool:
    """Check if a specific operation was already completed (idempotency guard).

    Args:
        cycle_id: The cycle this operation belongs to.
        op_seq: Sequence number of the operation.
        db_path: Path to the SQLite database.

    Returns:
        True if the operation has a checkpoint record.
    """
    conn = _sql_connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM op_idempotency WHERE cycle_id = ? AND op_seq = ?",
            (cycle_id, op_seq),
        ).fetchone()
    finally:
        conn.close()

    return row is not None
