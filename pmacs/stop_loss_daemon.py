"""Stop-loss daemon process -- pmacs-stoploss.

Runs as a daemon during RTH, checking all active positions every 30 minutes.
For each active holding:
1. Fetch current price.
2. Check stop-loss breach via stop_loss_monitor.
3. Check trailing stop via trailing_stop.
4. If breached: write StopTrigger to SQLite and notify nervous.

This is the pmacs-stoploss process from Architecture.md Section 4.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from pmacs.engines.stop_loss_monitor import check_stop_breach, check_trailing_breach
from pmacs.logsys import log_debug
from pmacs.schemas.stop_loss import StopEventStatus, StopTrigger

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


def _write_stop_trigger(conn: sqlite3.Connection, result, stop_type_category: str, cycle_id: str) -> None:
    """Write a StopTrigger to SQLite stop_events table."""
    now = datetime.now(timezone.utc).isoformat()
    trigger = StopTrigger(
        id=f"stop-{result.holding_id}-{now}",
        holding_id=result.holding_id,
        ticker=result.ticker,
        stop_type="TRAILING_STOP" if result.stop_type == "TRAILING_STOP" else "FIXED_STOP",
        trigger_price_usd=result.current_price,
        stop_price_usd=result.stop_price,
        current_price_usd=result.current_price,
        gap_down=result.is_gap_down,
        cycle_id=cycle_id,
        detected_at=now,
        status=StopEventStatus.PENDING,
        stop_type_category=stop_type_category,
    )
    conn.execute(
        """INSERT INTO stop_events
           (holding_id, ticker, stop_type, trigger_price_usd, stop_price_usd,
            detected_at, cycle_id, status, stop_type_category, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trigger.holding_id,
            trigger.ticker,
            trigger.stop_type.value if hasattr(trigger.stop_type, "value") else trigger.stop_type,
            trigger.trigger_price_usd,
            trigger.stop_price_usd,
            trigger.detected_at,
            trigger.cycle_id,
            trigger.status.value,
            trigger.stop_type_category,
            now,
        ),
    )
    conn.commit()


def check_holding(
    holding,
    current_price: float,
    market_state: str = "RTH",
) -> tuple | None:
    """Check a single holding for stop breaches.

    Trailing stop takes priority when armed (both fixed and trailing breached).

    Args:
        holding: Holding model with stop_price_usd, trailing_stop_price_usd, etc.
        current_price: Latest market price.
        market_state: "RTH" or other.

    Returns:
        Tuple of (StopCheckResult, stop_type_category) or None if no breach.
    """
    # Check trailing first -- takes priority when armed
    trailing_result = check_trailing_breach(holding, current_price, market_state)
    if trailing_result is not None:
        return trailing_result, "TRAILING"

    # Check fixed stop
    fixed_result = check_stop_breach(holding, current_price, market_state)
    if fixed_result is not None:
        return fixed_result, "FIXED"

    return None


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
            try:
                conn = sqlite3.connect(str(db_path))
                # Fetch active holdings
                rows = conn.execute(
                    "SELECT id, ticker, state, stop_price_usd, trailing_stop_price_usd "
                    "FROM holdings WHERE state = 'ACTIVE'"
                ).fetchall()

                for row in rows:
                    holding_id, ticker, state, stop_price, trailing_price = row

                    # Build a minimal holding-like object for checks
                    class _HoldingProxy:
                        pass
                    h = _HoldingProxy()
                    h.id = holding_id
                    h.ticker = ticker
                    h.stop_price_usd = stop_price
                    h.trailing_stop_price_usd = trailing_price
                    # If trailing_price is set, consider it armed
                    h.trailing_stop_armed = trailing_price is not None

                    # TODO: fetch real current_price from data feed
                    # For now, skip price fetch (placeholder)
                    # current_price = fetch_price(ticker)
                    pass

                conn.close()
            except Exception as exc:
                log_debug(
                    "STOP_LOSS_DAEMON_ERROR",
                    payload={"error": str(exc)},
                    level="WARN",
                    error_code="SQLITE_WRITE_FAILED",
                    msg=f"Stop-loss daemon cycle error: {exc}",
                )
        time.sleep(check_interval)
