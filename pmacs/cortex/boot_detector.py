"""Boot cycle detector (Architecture.md §4.5).

Determines whether a new analysis cycle should be initiated on process startup.
Checks last closed cycle timing, weekend status, and EOD data availability.
On detection, calls the nervous orchestrator to actually open the cycle and
writes an audit event.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pmacs.logsys import log_debug


def _should_skip_weekend(now: datetime) -> bool:
    """Return True if it's a weekend (Saturday/Sunday)."""
    return now.weekday() >= 5


def _should_skip_before_eod(
    now: datetime,
    eod_hour: int,
    eod_minute: int,
) -> bool:
    """Return True if current ET time is before the EOD data window."""
    try:
        from zoneinfo import ZoneInfo
        now_et = now.astimezone(ZoneInfo("US/Eastern"))
    except Exception:
        from datetime import timedelta
        now_et = now - timedelta(hours=5)

    return (now_et.hour * 60 + now_et.minute) < (eod_hour * 60 + eod_minute)


def _get_last_closed_gap_hours(db_path: Path, now: datetime) -> float | None:
    """Return hours since last closed cycle, or None if no history."""
    if not db_path.exists():
        return None

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """SELECT closed_at FROM cycles
               WHERE state = 'CLOSED'
               ORDER BY closed_at DESC LIMIT 1"""
        ).fetchone()
        if row is None or not row[0]:
            return None

        last_closed = datetime.fromisoformat(row[0])
        if last_closed.tzinfo is None:
            last_closed = last_closed.replace(tzinfo=timezone.utc)
        return (now - last_closed).total_seconds() / 3600
    finally:
        conn.close()


def maybe_initiate_cycle(
    db_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    eod_time_hour: int = 16,
    eod_time_minute: int = 30,
    timezone_name: str = "US/Eastern",
) -> str | None:
    """Check if a cycle should be initiated on boot and initiate it.

    Per Architecture.md §4.5:
    - Skip if gap since last closed cycle < 24h
    - Skip if weekend
    - Skip if before EOD data time (16:30 ET)
    - Warn if gap > 168h (7 days)
    - On detection: call orchestrator.initiate_cycle and log audit event
    - Returns cycle_id if initiated, None if skipped

    Args:
        db_path: Path to SQLite database.
        audit_path: Path to audit log for recording boot initiation.
        eod_time_hour: Hour of EOD data availability (default 16).
        eod_time_minute: Minute of EOD data availability (default 30).
        timezone_name: Timezone for EOD check (default US/Eastern).

    Returns:
        cycle_id string if a cycle was initiated, None if skipped.
    """
    if db_path is None:
        from pmacs.config import data_dir
        db_path = data_dir() / "pmacs.db"
    now = datetime.now(timezone.utc)
    db = Path(db_path)

    # Check: is it a weekend?
    if _should_skip_weekend(now):
        log_debug(
            "BOOT_CYCLE_SKIPPED_WEEKEND",
            payload={"weekday": now.weekday()},
            level="INFO",
            msg="Skipping boot cycle: weekend",
        )
        return None

    # Check: is it before EOD time?
    if _should_skip_before_eod(now, eod_time_hour, eod_time_minute):
        log_debug(
            "BOOT_CYCLE_SKIPPED_BEFORE_EOD",
            payload={
                "current_time_et": now.strftime("%H:%M"),
                "eod_time": f"{eod_time_hour:02d}:{eod_time_minute:02d}",
            },
            level="INFO",
            msg="Skipping boot cycle: before EOD data time",
        )
        return None

    # Check: when was the last closed cycle?
    gap_hours = _get_last_closed_gap_hours(db, now)

    # First run — no history
    if gap_hours is None:
        log_debug(
            "BOOT_CYCLE_INIT_FIRST_RUN",
            payload={"reason": "no database or no closed cycles"},
            level="INFO",
            msg="Initiating first boot cycle",
        )
        cycle_id = _do_initiate_cycle(db, audit_path, gap_hours=0)
        return cycle_id

    # Skip if gap < 24h
    if gap_hours < 24:
        log_debug(
            "BOOT_CYCLE_SKIPPED_RECENT",
            payload={"gap_hours": round(gap_hours, 1)},
            level="INFO",
            msg=f"Skipping boot cycle: last closed {gap_hours:.1f}h ago (< 24h)",
        )
        return None

    # Warn if gap > 168h (7 days)
    if gap_hours > 168:
        log_debug(
            "BOOT_CYCLE_LONG_GAP",
            payload={"gap_hours": round(gap_hours, 1)},
            level="WARN",
            error_code="PROCESS_HEARTBEAT_MISSED",
            msg=f"WARNING: gap since last cycle is {gap_hours:.1f}h (> 168h = 7 days)",
        )

    # Gap >= 24h, not weekend, after EOD — initiate cycle
    cycle_id = _do_initiate_cycle(db, audit_path, gap_hours=gap_hours)
    return cycle_id


def _do_initiate_cycle(
    db_path: Path,
    audit_path: str | Path | None,
    gap_hours: float,
) -> str:
    """Actually initiate the cycle via the nervous orchestrator.

    Falls back to UUID generation if orchestrator is unavailable (e.g. tests).
    """
    cycle_id: str | None = None

    try:
        from pmacs.nervous.orchestrator import initiate_cycle
        cycle_id = initiate_cycle(
            trigger="BOOT_DETECTED",
            db_path=db_path,
            audit_path=Path(audit_path) if audit_path else None,
        )
    except Exception as exc:
        log_debug(
            "BOOT_CYCLE_ORCHESTRATOR_FALLBACK",
            payload={"error": str(exc)},
            level="WARN",
            error_code="PROCESS_HEARTBEAT_MISSED",
            msg=f"Orchestrator unavailable, generating cycle_id locally: {exc}",
        )
        cycle_id = str(uuid.uuid4())

    # Audit log the boot initiation
    if audit_path:
        try:
            from pmacs.storage.audit import AuditWriter
            writer = AuditWriter(audit_path)
            writer.append(
                "BOOT_CYCLE_INITIATED",
                {
                    "cycle_id": cycle_id,
                    "gap_hours": round(gap_hours, 1),
                    "trigger": "BOOT_DETECTED",
                },
                cycle_id=cycle_id,
            )
            writer.close()
        except Exception as exc:
            log_debug(
                "BOOT_CYCLE_AUDIT_FAILED",
                payload={"error": str(exc)},
                level="WARN",
                error_code="AUDIT_WRITE_FAILED",
                msg=f"Failed to write boot audit event: {exc}",
            )

    log_debug(
        "BOOT_CYCLE_INITIATED",
        payload={"cycle_id": cycle_id, "gap_hours": round(gap_hours, 1)},
        level="INFO",
        msg=f"Boot cycle {cycle_id} initiated: gap={gap_hours:.1f}h",
    )
    return cycle_id
