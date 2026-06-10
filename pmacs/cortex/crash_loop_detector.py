"""Crash loop detector for PMACS processes (Architecture.md §4.7).

Records process restart timestamps in the process_state SQLite table.
Detects crash loops: >= max_restarts within a time window.
On detection, marks process as BROKEN_CRASH_LOOP which triggers kill switch.
"""
from __future__ import annotations

import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
from datetime import datetime, timezone
from pathlib import Path

from pmacs.logsys import log_debug


def _resolve_db(db_path: Path | str | None) -> Path:
    if db_path is None:
        from pmacs.config import data_dir
        return data_dir() / "pmacs.db"
    return Path(db_path)

_CRASH_LOOP_DDL = """
CREATE TABLE IF NOT EXISTS process_restarts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proc TEXT NOT NULL,
    restarted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_process_restarts_proc_time
    ON process_restarts(proc, restarted_at DESC);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create process_restarts table if not exists."""
    conn.executescript(_CRASH_LOOP_DDL)
    conn.commit()


def record_restart(
    proc: str,
    db_path: str | Path | None = None,
) -> None:
    """Record a process restart event.

    Args:
        proc: Process name (e.g. 'pmacs-inference').
        db_path: Path to SQLite database.
    """
    db_path = _resolve_db(db_path)
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = _sql_connect(p)
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO process_restarts (proc, restarted_at) VALUES (?, ?)",
            (proc, now),
        )

        # Also update the process_state table (from sqlite.py schema)
        conn.execute(
            """INSERT INTO process_state (proc, last_started_at, restart_count_60s, is_broken_crash_loop)
               VALUES (?, ?, 1, 0)
               ON CONFLICT(proc) DO UPDATE SET
                   last_started_at = excluded.last_started_at,
                   restart_count_60s = restart_count_60s + 1""",
            (proc, now),
        )

        conn.commit()

        log_debug(
            "PROCESS_RESTART_RECORDED",
            payload={"proc": proc, "restarted_at": now},
            level="INFO",
            msg=f"Recorded restart for {proc}",
        )
    finally:
        conn.close()


def check_crash_loop(
    proc: str,
    db_path: str | Path | None = None,
    max_restarts: int = 5,
    window_s: int = 60,
) -> bool:
    """Check if a specific process is in a crash loop.

    Counts restarts within the last `window_s` seconds. Returns True
    if >= max_restarts in that window.

    Args:
        proc: Process name to check.
        db_path: Path to SQLite database.
        max_restarts: Maximum allowed restarts in window (default 5).
        window_s: Time window in seconds (default 60).

    Returns:
        True if crash loop detected.
    """
    db_path = _resolve_db(db_path)
    conn = _sql_connect(db_path)
    try:
        now_iso = datetime.now(timezone.utc).isoformat()

        # Count restarts in the last window_s seconds
        # Use a cutoff computed from current time
        import time as _time

        cutoff_ts = _time.time() - window_s
        cutoff_iso = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()

        row = conn.execute(
            """SELECT COUNT(*) FROM process_restarts
               WHERE proc = ? AND restarted_at > ?""",
            (proc, cutoff_iso),
        ).fetchone()

        count = row[0] if row else 0
        is_loop = count >= max_restarts

        if is_loop:
            log_debug(
                "CRASH_LOOP_DETECTED",
                payload={"proc": proc, "restart_count": count, "window_s": window_s},
                level="WARN",
                error_code="CRASH_LOOP_DETECTED",
                msg=f"Crash loop detected for {proc}: {count} restarts in {window_s}s",
            )

            # Mark as broken in process_state
            conn.execute(
                """UPDATE process_state SET is_broken_crash_loop = 1 WHERE proc = ?""",
                (proc,),
            )
            conn.commit()

        return is_loop
    finally:
        conn.close()


def check_any_crash_loop(
    db_path: str | Path | None = None,
    max_restarts: int = 5,
    window_s: int = 60,
) -> str | None:
    """Check all processes for crash loops.

    Args:
        db_path: Path to SQLite database.
        max_restarts: Maximum allowed restarts in window.
        window_s: Time window in seconds.

    Returns:
        Name of first process in crash loop, or None if all healthy.
    """
    db_path = _resolve_db(db_path)
    conn = _sql_connect(db_path)
    try:
        _ensure_table(conn)

        import time as _time

        cutoff_ts = _time.time() - window_s
        cutoff_iso = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()

        rows = conn.execute(
            """SELECT proc, COUNT(*) as cnt FROM process_restarts
               WHERE restarted_at > ?
               GROUP BY proc
               HAVING cnt >= ?""",
            (cutoff_iso, max_restarts),
        ).fetchall()

        if rows:
            return rows[0][0]  # Return first crash-looping process
        return None
    finally:
        conn.close()


def clear_crash_loop_mark(
    proc: str,
    db_path: str | Path | None = None,
) -> None:
    """Clear the BROKEN_CRASH_LOOP mark for a process after manual resolution.

    Args:
        proc: Process name.
        db_path: Path to SQLite database.
    """
    db_path = _resolve_db(db_path)
    conn = _sql_connect(db_path)
    try:
        conn.execute(
            """UPDATE process_state SET is_broken_crash_loop = 0, restart_count_60s = 0
               WHERE proc = ?""",
            (proc,),
        )
        # Also clean up old restart records to prevent false positives
        conn.execute("DELETE FROM process_restarts WHERE proc = ?", (proc,))
        conn.commit()
    finally:
        conn.close()
