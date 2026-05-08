"""Nervous orchestrator — cycle lifecycle management (Architecture.md §4.4, §9).

Stub implementation for Phase 2 Wave 5: open/close cycle with
kill switch guard, SQLite persistence, SSE events, and audit writes.
No symbols processed in this phase.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pmacs.cortex.kill_switch import is_engaged
from pmacs.logsys import log_debug
from pmacs.schemas.system import Mode


class KillSwitchEngagedError(Exception):
    """Raised when attempting to initiate a cycle while kill switch is engaged."""


def _current_mode(db_path: Path) -> str:
    """Read current mode from mode_history or default to INSTALLING."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT to_mode FROM mode_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return row[0]
    finally:
        conn.close()
    return Mode.INSTALLING.value


def initiate_cycle(trigger: str, db_path: Path, audit_path: Path | None = None) -> str:
    """Open a new cycle.

    Checks kill switch first — raises KillSwitchEngagedError if engaged.
    Creates cycle_id (UUID4), inserts into SQLite, emits SSE and audit events.

    Args:
        trigger: What triggered this cycle (e.g. 'TIMER', 'OPERATOR').
        db_path: Path to the SQLite database.
        audit_path: Optional path to the audit log file.

    Returns:
        The newly created cycle_id.

    Raises:
        KillSwitchEngagedError: If the kill switch is currently engaged.
    """
    # Kill switch gate — must check BEFORE any state mutation
    if is_engaged(db_path):
        log_debug(
            "CYCLE_OPEN_BLOCKED_KILL_SWITCH",
            payload={"trigger": trigger},
            level="WARN",
            error_code="KILL_SWITCH_ENGAGED",
            msg="Cycle initiation blocked: kill switch is engaged",
        )
        raise KillSwitchEngagedError("Cannot initiate cycle: kill switch is engaged")

    cycle_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mode = _current_mode(db_path)

    # Insert into SQLite
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode) "
            "VALUES (?, ?, NULL, 'OPEN', ?, ?)",
            (cycle_id, now, trigger, mode),
        )
        conn.commit()
    finally:
        conn.close()

    # Emit SSE event via the publisher (import here to avoid circular imports)
    from pmacs.nervous.sse_publisher import SSEPublisher

    _publisher: SSEPublisher | None = getattr(initiate_cycle, "_publisher", None)  # type: ignore[attr-defined]
    if _publisher is not None:
        _publisher.publish("cycle", "cycle.open", {
            "cycle_id": cycle_id,
            "trigger": trigger,
            "mode": mode,
            "opened_at": now,
        })

    # Write audit event
    if audit_path is not None:
        from pmacs.storage.audit import AuditWriter

        writer = AuditWriter(audit_path)
        writer.append("cycle_opened", {
            "cycle_id": cycle_id,
            "trigger": trigger,
            "mode": mode,
            "opened_at": now,
        })
        writer.close()

    log_debug(
        "CYCLE_OPENED",
        payload={"cycle_id": cycle_id, "trigger": trigger, "mode": mode},
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Cycle opened: {cycle_id[:8]} trigger={trigger}",
    )

    return cycle_id


def close_cycle(cycle_id: str, db_path: Path, audit_path: Path | None = None) -> None:
    """Close an open cycle.

    Updates SQLite state to CLOSED, emits SSE and audit events.

    Args:
        cycle_id: The cycle to close.
        db_path: Path to the SQLite database.
        audit_path: Optional path to the audit log file.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Update SQLite
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE cycles SET state = 'CLOSED', closed_at = ? WHERE cycle_id = ?",
            (now, cycle_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Emit SSE event
    from pmacs.nervous.sse_publisher import SSEPublisher

    _publisher: SSEPublisher | None = getattr(close_cycle, "_publisher", None)  # type: ignore[attr-defined]
    if _publisher is not None:
        _publisher.publish("cycle", "cycle.close", {
            "cycle_id": cycle_id,
            "closed_at": now,
        })

    # Write audit event
    if audit_path is not None:
        from pmacs.storage.audit import AuditWriter

        writer = AuditWriter(audit_path)
        writer.append("cycle_closed", {
            "cycle_id": cycle_id,
            "closed_at": now,
        }, cycle_id=cycle_id)
        writer.close()

    log_debug(
        "CYCLE_CLOSED",
        payload={"cycle_id": cycle_id, "closed_at": now},
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Cycle closed: {cycle_id[:8]}",
    )
