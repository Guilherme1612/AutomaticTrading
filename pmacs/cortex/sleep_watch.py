"""Sleep/wake detection for cycle resume (Architecture.md §4.6, Source.md §22).

Detects macOS sleep/wake events. On wake, checks for incomplete cycles
in the op_idempotency table and triggers cycle resume if found.

Two detection strategies:
1. Primary: heartbeat timestamp gap (> 5 min gap = sleep assumed)
2. Fallback (future): PyObjC IOPMSystemPowerState notifications

Per Source.md §22: "close lid mid-cycle, resume on wake" depends on this.
"""
from __future__ import annotations

import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
import time
from dataclasses import dataclass
from pathlib import Path

from pmacs.cortex.health import HEARTBEAT_DIR
from pmacs.logsys import log_debug


def _resolve_db(db_path: Path | str | None) -> Path:
    if db_path is None:
        from pmacs.config import data_dir
        return data_dir() / "pmacs.db"
    return Path(db_path)

# Gap threshold: if heartbeat timestamps show a gap larger than this,
# we assume the machine was sleeping.
_SLEEP_GAP_THRESHOLD_S = 300.0  # 5 minutes


@dataclass(frozen=True)
class SleepWakeResult:
    """Result of a sleep/wake detection check."""

    sleep_detected: bool
    wake_detected: bool
    gap_seconds: float | None
    incomplete_cycle_id: str | None


def _detect_sleep_via_heartbeat(
    heartbeat_dir: Path = HEARTBEAT_DIR,
    gap_threshold: float = _SLEEP_GAP_THRESHOLD_S,
) -> tuple[bool, float | None]:
    """Check for sleep by looking at heartbeat timestamp gaps.

    Compares the current time to the most recent heartbeat timestamp.
    If the gap exceeds the threshold, the machine likely slept.

    Args:
        heartbeat_dir: Directory containing heartbeat timestamp files.
        gap_threshold: Seconds beyond which a gap indicates sleep.

    Returns:
        Tuple of (sleep_detected, gap_seconds).
    """
    now = time.time()
    latest_ts: float | None = None

    if not heartbeat_dir.exists():
        return (False, None)

    # Find the most recent heartbeat across all processes
    for ts_file in heartbeat_dir.glob("*.ts"):
        try:
            ts = float(ts_file.read_text().strip())
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
        except (ValueError, OSError):
            continue

    if latest_ts is None:
        return (False, None)

    gap = now - latest_ts
    return (gap > gap_threshold, gap)


def _find_incomplete_cycle(
    db_path: str | Path | None = None,
) -> str | None:
    """Find a cycle that has checkpoints but is not closed.

    Checks the op_idempotency table for cycles that have started
    but whose corresponding entry in the cycles table is not CLOSED.

    Args:
        db_path: Path to the SQLite database.

    Returns:
        cycle_id of the incomplete cycle, or None if all cycles are complete.
    """
    db_path = _resolve_db(db_path)
    p = Path(db_path)
    if not p.exists():
        return None

    conn = _sql_connect(db_path)
    try:
        # Find cycles with idempotency ops that are not CLOSED
        row = conn.execute(
            """SELECT DISTINCT oi.cycle_id
               FROM op_idempotency oi
               LEFT JOIN cycles c ON c.cycle_id = oi.cycle_id
               WHERE c.state != 'CLOSED'
                  OR c.cycle_id IS NULL
               ORDER BY oi.op_seq DESC
               LIMIT 1"""
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return row[0]


def check_sleep_wake(
    db_path: str | Path | None = None,
    heartbeat_dir: Path = HEARTBEAT_DIR,
    gap_threshold: float = _SLEEP_GAP_THRESHOLD_S,
) -> SleepWakeResult:
    """Check for sleep/wake events and incomplete cycles.

    This is the main entry point. It:
    1. Detects whether a sleep occurred (via heartbeat gap)
    2. If wake detected, looks for incomplete cycles
    3. Returns result for cortex daemon to act on

    Args:
        db_path: Path to the SQLite database.
        heartbeat_dir: Directory containing heartbeat timestamp files.
        gap_threshold: Seconds beyond which a gap indicates sleep.

    Returns:
        SleepWakeResult with detection status and incomplete cycle info.
    """
    db_path = _resolve_db(db_path)
    sleep_detected, gap_seconds = _detect_sleep_via_heartbeat(
        heartbeat_dir, gap_threshold
    )

    if not sleep_detected:
        return SleepWakeResult(
            sleep_detected=False,
            wake_detected=False,
            gap_seconds=gap_seconds,
            incomplete_cycle_id=None,
        )

    log_debug(
        "SLEEP_DETECTED",
        payload={"gap_seconds": round(gap_seconds or 0, 1)},
        level="INFO",
        error_code="SLEEP_DETECTED",
        msg=f"Sleep detected: heartbeat gap {gap_seconds:.1f}s",
    )

    # On wake: check for incomplete cycles
    incomplete_cycle = _find_incomplete_cycle(db_path)

    if incomplete_cycle is not None:
        log_debug(
            "WAKE_DETECTED",
            payload={
                "incomplete_cycle_id": incomplete_cycle,
                "gap_seconds": round(gap_seconds or 0, 1),
            },
            level="INFO",
            error_code="WAKE_DETECTED",
            msg=f"Wake detected with incomplete cycle {incomplete_cycle}, resume needed",
        )
    else:
        log_debug(
            "WAKE_DETECTED",
            payload={"gap_seconds": round(gap_seconds or 0, 1)},
            level="INFO",
            error_code="WAKE_DETECTED",
            msg="Wake detected, no incomplete cycles found",
        )

    return SleepWakeResult(
        sleep_detected=True,
        wake_detected=True,
        gap_seconds=gap_seconds,
        incomplete_cycle_id=incomplete_cycle,
    )


def resume_cycle_if_needed(
    db_path: str | Path | None = None,
    heartbeat_dir: Path = HEARTBEAT_DIR,
) -> bool:
    """Convenience function: check for sleep/wake and log resume decision.

    Returns True if a cycle resume should be triggered. The actual
    resume logic is in pmacs.nervous.orchestrator.

    Args:
        db_path: Path to the SQLite database.
        heartbeat_dir: Directory containing heartbeat timestamp files.

    Returns:
        True if resume should be triggered, False otherwise.
    """
    result = check_sleep_wake(db_path, heartbeat_dir)

    if result.wake_detected and result.incomplete_cycle_id is not None:
        from pmacs.nervous.checkpoint import load_checkpoint

        checkpoint = load_checkpoint(
            result.incomplete_cycle_id, Path(db_path)
        )
        if checkpoint is not None:
            log_debug(
                "CYCLE_RESUME_TRIGGERED",
                payload={
                    "cycle_id": result.incomplete_cycle_id,
                    "last_op_seq": checkpoint.op_seq,
                    "last_op_type": checkpoint.op_type,
                },
                level="INFO",
                msg=f"Cycle resume triggered for {result.incomplete_cycle_id} "
                    f"at op_seq={checkpoint.op_seq}",
            )
            return True

    return False
