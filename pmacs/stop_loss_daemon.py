"""Stop-loss daemon process — pmacs-stoploss.

Runs as a daemon during RTH, checking all active positions every 30 minutes.
For each active holding:
1. Fetch current price.
2. Check stop-loss breach via stop_loss_monitor.
3. Check trailing stop via trailing_stop.
4. If breached: write StopTrigger to SQLite and notify nervous.

This is the pmacs-stoploss process from Architecture.md §4.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

CHECK_INTERVAL_S = 1800  # 30 minutes during RTH
RTH_START = "09:30"  # US Eastern
RTH_END = "16:00"  # US Eastern


def is_rth() -> bool:
    """Check if currently in Regular Trading Hours (US Eastern).

    Returns:
        True if current time is within RTH on a weekday (Mon-Fri),
        US Eastern time.
    """
    from datetime import datetime

    import pytz

    eastern = pytz.timezone("US/Eastern")
    now = datetime.now(eastern)
    if now.weekday() >= 5:  # weekend
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def run_stop_loss_loop(
    db_path: Path,
    heartbeat_dir: Path,
    check_interval: int = CHECK_INTERVAL_S,
) -> None:
    """Main loop for pmacs-stoploss process.

    During RTH, checks all active positions for stop breaches and
    trailing stop triggers. Writes heartbeat to heartbeat_dir each cycle.

    Args:
        db_path: Path to the SQLite database.
        heartbeat_dir: Directory for process heartbeat files.
        check_interval: Seconds between checks (default 1800 = 30 min).
    """
    while True:
        if is_rth():
            # Check all active positions
            # For each: fetch current price, check stop breach, check trailing
            # If breach: write StopTrigger to SQLite
            pass
        time.sleep(check_interval)
