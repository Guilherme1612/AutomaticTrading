"""Cortex self-check / meta-monitor (Architecture.md §4.4, §13.1 trigger #8).

Runs as a separate process (pmacs-cortex-self-check).
Pings the Nervous health endpoint every 60s.
If Cortex is unresponsive for >120s, engages kill switch directly
by writing to the kill_switch SQLite table.
"""
from __future__ import annotations

import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path

from pmacs.logsys import log_debug

_DEFAULT_HEALTH_URL = "http://127.0.0.1:8000/health"
_DEFAULT_DB_PATH = Path("/var/db/pmacs/pmacs.db")
_CHECK_INTERVAL_S = 60
_STALE_THRESHOLD_S = 120
_REQUEST_TIMEOUT_S = 5


def check_health_endpoint(
    url: str = _DEFAULT_HEALTH_URL,
    timeout: float = _REQUEST_TIMEOUT_S,
) -> bool:
    """Ping a health endpoint.

    Args:
        url: Health check URL.
        timeout: Request timeout in seconds.

    Returns:
        True if endpoint responded with 2xx, False otherwise.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
        return False


def engage_kill_switch_direct(
    db_path: Path | str = _DEFAULT_DB_PATH,
    reason: str = "Meta-monitor: Cortex unresponsive >120s",
) -> None:
    """Engage kill switch by writing directly to SQLite.

    Used when the normal engage() path is unavailable (e.g. module
    import issues) or as a direct safety mechanism.

    Args:
        db_path: Path to SQLite database.
        reason: Reason for engagement.
    """
    p = Path(db_path)
    if not p.exists():
        # No database — can't engage. Write to stderr.
        import sys

        print(
            f"SELF_CHECK_CRITICAL: Cannot engage kill switch, no DB at {db_path}: {reason}",
            file=sys.stderr,
        )
        return

    try:
        conn = sqlite3.connect(str(p))
        try:
            now_iso = _now_iso()
            # Ensure kill_switch table exists
            conn.execute(
                """CREATE TABLE IF NOT EXISTS kill_switch (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state TEXT NOT NULL DEFAULT 'ARMED',
                    reason TEXT,
                    trigger_name TEXT,
                    engaged_at TEXT,
                    disengaged_at TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
            row = conn.execute("SELECT COUNT(*) FROM kill_switch WHERE id = 1").fetchone()
            if row[0] == 0:
                conn.execute(
                    "INSERT INTO kill_switch (id, state, updated_at) VALUES (1, 'ARMED', ?)",
                    (now_iso,),
                )

            conn.execute(
                """UPDATE kill_switch
                   SET state = 'ENGAGED', reason = ?, trigger_name = 'META_MONITOR_UNRESPONSIVE',
                       engaged_at = ?, updated_at = ?
                   WHERE id = 1 AND state = 'ARMED'""",
                (reason, now_iso, now_iso),
            )
            conn.commit()
        finally:
            conn.close()

        log_debug(
            "SELF_CHECK_ENGAGED_KILL_SWITCH",
            payload={"reason": reason},
            level="WARN",
            error_code="KILL_SWITCH_ENGAGED",
            msg=f"Self-check engaged kill switch: {reason}",
        )
    except Exception as exc:
        import sys

        print(
            f"SELF_CHECK_CRITICAL: Failed to engage kill switch: {exc}",
            file=sys.stderr,
        )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def run_self_check_loop(
    health_url: str = _DEFAULT_HEALTH_URL,
    db_path: Path | str = _DEFAULT_DB_PATH,
    heartbeat_dir: Path | None = None,
    check_interval: int = _CHECK_INTERVAL_S,
    stale_threshold: int = _STALE_THRESHOLD_S,
) -> None:
    """Main loop for the self-check process.

    Every `check_interval` seconds:
    1. Write own heartbeat
    2. Ping health endpoint
    3. If health check fails and Cortex has been stale > threshold, engage kill switch

    Args:
        health_url: Health endpoint URL to check.
        db_path: Path to SQLite database.
        heartbeat_dir: Directory for heartbeat files.
        check_interval: Seconds between health checks.
        stale_threshold: Seconds of failure before engaging kill switch.
    """
    from pmacs.cortex.health import write_heartbeat

    last_success_time: float | None = None

    while True:
        try:
            # Write own heartbeat
            if heartbeat_dir is not None:
                write_heartbeat("cortex-self-check", heartbeat_dir)

            # Check health
            healthy = check_health_endpoint(health_url)

            if healthy:
                last_success_time = time.time()
            else:
                now = time.time()
                if last_success_time is None:
                    # First check failed — start timer
                    last_success_time = now

                stale_seconds = now - last_success_time
                if stale_seconds > stale_threshold:
                    engage_kill_switch_direct(db_path)

        except Exception as exc:
            log_debug(
                "SELF_CHECK_ERROR",
                payload={"error": str(exc)},
                level="WARN",
                error_code="PROCESS_HEARTBEAT_MISSED",
                msg=f"Self-check loop error: {exc}",
            )

        time.sleep(check_interval)


def main() -> None:
    """Entry point for pmacs-cortex-self-check process."""
    import signal
    import sys

    def _signal_handler(signum: int, frame: object) -> None:
        log_debug(
            "SELF_CHECK_SHUTDOWN",
            payload={"signal": signum},
            level="INFO",
            msg="Self-check process shutting down",
        )
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    heartbeat_dir = Path("/var/db/pmacs/heartbeat")

    log_debug(
        "SELF_CHECK_STARTING",
        payload={"pid": __import__("os").getpid()},
        level="INFO",
        msg="pmacs-cortex-self-check starting",
    )

    run_self_check_loop(heartbeat_dir=heartbeat_dir)


if __name__ == "__main__":
    main()
