"""Boot cycle detector (Architecture.md §4.5).

Determines whether a new analysis cycle should be initiated on process startup.
Checks last closed cycle timing, weekend status, and EOD data availability.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pmacs.logsys import log_debug


def maybe_initiate_cycle(
    db_path: str | Path = "/var/db/pmacs/pmacs.db",
    eod_time_hour: int = 16,
    eod_time_minute: int = 30,
    timezone_name: str = "US/Eastern",
) -> str | None:
    """Check if a cycle should be initiated on boot.

    Per Architecture.md §4.5:
    - Skip if gap since last closed cycle < 24h
    - Skip if weekend (simple weekday check)
    - Skip if before EOD data time (16:30 ET)
    - Warn if gap > 168h (7 days)
    - Returns cycle_id if initiated, None if skipped

    Args:
        db_path: Path to SQLite database.
        eod_time_hour: Hour of EOD data availability (default 16).
        eod_time_minute: Minute of EOD data availability (default 30).
        timezone_name: Timezone for EOD check (default US/Eastern).

    Returns:
        cycle_id string if a cycle should be initiated, None to skip.
    """
    now = datetime.now(timezone.utc)

    # Check: is it a weekend? (simple check; pandas_market_calendars later)
    # Monday=0, Sunday=6
    weekday = now.weekday()
    if weekday >= 5:  # Saturday=5, Sunday=6
        log_debug(
            "BOOT_CYCLE_SKIPPED_WEEKEND",
            payload={"weekday": weekday},
            level="INFO",
            msg="Skipping boot cycle: weekend",
        )
        return None

    # Check: is it before EOD time?
    # Convert current time to Eastern for EOD check
    try:
        from zoneinfo import ZoneInfo

        eastern = ZoneInfo("US/Eastern")
        now_et = now.astimezone(eastern)
    except Exception:
        # Fallback: use UTC-5 approximation
        from datetime import timedelta

        now_et = now - timedelta(hours=5)

    current_time_et = now_et.hour * 60 + now_et.minute
    eod_time_et = eod_time_hour * 60 + eod_time_minute
    if current_time_et < eod_time_et:
        log_debug(
            "BOOT_CYCLE_SKIPPED_BEFORE_EOD",
            payload={
                "current_time_et": f"{now_et.hour:02d}:{now_et.minute:02d}",
                "eod_time": f"{eod_time_hour:02d}:{eod_time_minute:02d}",
            },
            level="INFO",
            msg="Skipping boot cycle: before EOD data time",
        )
        return None

    # Check: when was the last closed cycle?
    p = Path(db_path)
    if not p.exists():
        # No database yet — first run, initiate cycle
        log_debug(
            "BOOT_CYCLE_INIT_FIRST_RUN",
            payload={"reason": "no database"},
            level="INFO",
            msg="Initiating first boot cycle: no database found",
        )
        return str(uuid.uuid4())

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """SELECT closed_at FROM cycles
               WHERE state = 'CLOSED'
               ORDER BY closed_at DESC LIMIT 1"""
        ).fetchone()

        if row is None:
            # No closed cycles — first run
            log_debug(
                "BOOT_CYCLE_INIT_NO_HISTORY",
                payload={"reason": "no closed cycles"},
                level="INFO",
                msg="Initiating boot cycle: no closed cycles in history",
            )
            return str(uuid.uuid4())

        last_closed_str = row[0]
        if not last_closed_str:
            return str(uuid.uuid4())

        last_closed = datetime.fromisoformat(last_closed_str)
        if last_closed.tzinfo is None:
            last_closed = last_closed.replace(tzinfo=timezone.utc)

        gap_hours = (now - last_closed).total_seconds() / 3600

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

        # Gap >= 24h and not weekend and after EOD — initiate cycle
        cycle_id = str(uuid.uuid4())
        log_debug(
            "BOOT_CYCLE_INITIATED",
            payload={
                "cycle_id": cycle_id,
                "gap_hours": round(gap_hours, 1),
            },
            level="INFO",
            msg=f"Initiating boot cycle {cycle_id}: gap={gap_hours:.1f}h",
        )
        return cycle_id

    finally:
        conn.close()
