"""Cortex daemon — main process loop (Architecture.md §4).

Responsibilities:
- Every 5s: write own heartbeat
- Every 10s: check all other process heartbeats
- Every 60s: verify audit chain integrity + replicate to cortex-owned copy
- On startup: verify all processes have heartbeats within 30s
- Kill switch trigger: audit chain failure, disk <2GB, crash loops
- Configurable intervals via config/resources.toml
"""
from __future__ import annotations

import shutil
import signal
import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pmacs.cortex.health import check_heartbeats, write_heartbeat
from pmacs.cortex.kill_switch import check_all_triggers, engage, is_engaged
from pmacs.logsys import log_debug

# All managed processes (Architecture.md §4 topology)
ALL_PROCESSES: list[str] = [
    "pmacs-inference",
    "pmacs-cortex",
    "pmacs-cortex-self-check",
    "pmacs-execution",
    "pmacs-nervous",
    "pmacs-stoploss",
    "pmacs-mutation",
    "pmacs-dashboard",
]

# Heartbeat-only processes (not started by cortex, but monitored)
MONITORED_PROCESSES: list[str] = [
    "pmacs-inference",
    "pmacs-cortex-self-check",
    "pmacs-execution",
    "pmacs-nervous",
    "pmacs-stoploss",
    "pmacs-mutation",
    "pmacs-dashboard",
]


@dataclass(frozen=True)
class DaemonConfig:
    """Cortex daemon configuration.

    All intervals in seconds. Configurable via config/resources.toml.
    """

    heartbeat_interval: int = 5
    health_check_interval: int = 10
    audit_check_interval: int = 60
    startup_grace_period: int = 30
    db_path: str | None = None
    audit_path: str | None = None
    heartbeat_dir: str | None = None

    def __post_init__(self):
        from pmacs.config import data_dir
        d = data_dir()
        if self.db_path is None:
            object.__setattr__(self, 'db_path', str(d / "pmacs.db"))
        if self.audit_path is None:
            object.__setattr__(self, 'audit_path', str(d / "audit.log"))
        if self.heartbeat_dir is None:
            object.__setattr__(self, 'heartbeat_dir', str(d / "heartbeats"))


def _replicate_audit(primary_path: str, replica_path: str | None = None) -> bool:
    """Replicate primary audit.log to cortex-owned copy (Architecture.md §5.1).

    Uses file copy. The cortex daemon then verifies the replica's hash chain
    independently, detecting any tampering of the primary.
    Returns True if replication succeeded.
    """
    if replica_path is None:
        replica_path = str(Path(primary_path).with_suffix(".cortex.log"))
    primary = Path(primary_path)
    if not primary.exists():
        return True  # nothing to replicate yet
    try:
        shutil.copy2(str(primary), replica_path)
        return True
    except OSError as exc:
        log_debug(
            "AUDIT_REPLICATION_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="AUDIT_REPLICATION_FAILED",
            msg=f"Audit replication failed: {exc}",
        )
        return False


def _load_daemon_config() -> DaemonConfig:
    """Load daemon config from resources.toml if available."""
    try:
        from pmacs.config import load_config

        config = load_config()
        res = getattr(config, 'resources', None)
        if res is not None:
            return DaemonConfig(
                heartbeat_interval=getattr(res, 'cortex_heartbeat_interval', 5),
                health_check_interval=getattr(res, 'cortex_health_check_interval', 10),
                audit_check_interval=getattr(res, 'cortex_audit_check_interval', 60),
            )
        return DaemonConfig()
    except Exception:
        return DaemonConfig()


def _startup_check(config: DaemonConfig) -> None:
    """On startup: verify all processes have heartbeats within grace period.

    Logs warnings for stale processes but does not abort startup.
    Kill switch should already be ARMED from previous session.
    """
    from datetime import datetime, timezone

    log_debug(
        "CORTEX_STARTUP_CHECK",
        payload={"processes": ALL_PROCESSES},
        level="INFO",
        msg="Cortex startup: checking process heartbeats",
    )

    statuses = check_heartbeats(
        MONITORED_PROCESSES,
        heartbeat_dir=Path(config.heartbeat_dir),
        stale_threshold=float(config.startup_grace_period),
    )

    stale_procs = [s.proc for s in statuses if s.is_stale]
    if stale_procs:
        log_debug(
            "CORTEX_STARTUP_STALE_PROCESSES",
            payload={"stale": stale_procs},
            level="WARN",
            error_code="PROCESS_HEARTBEAT_MISSED",
            msg=f"Startup: {len(stale_procs)} processes have stale/missing heartbeats: {stale_procs}",
        )
    else:
        log_debug(
            "CORTEX_STARTUP_ALL_HEALTHY",
            payload={},
            level="INFO",
            msg="Startup: all processes have fresh heartbeats",
        )

    # Mark any orphaned RUNNING cycles as INTERRUPTED (left over from previous crash/restart)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn = _sql_connect(config.db_path)
        try:
            cur = conn.execute(
                "UPDATE cycles SET state='INTERRUPTED', closed_at=? WHERE state='RUNNING'",
                (now,),
            )
            interrupted = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        if interrupted:
            log_debug(
                "CORTEX_STARTUP_INTERRUPTED_CYCLES",
                payload={"count": interrupted, "closed_at": now},
                level="WARN",
                error_code="PROCESS_HEARTBEAT_MISSED",
                msg=f"Startup: marked {interrupted} orphaned RUNNING cycles as INTERRUPTED",
            )
    except Exception as exc:
        log_debug(
            "CORTEX_STARTUP_CYCLE_CLEANUP_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="SQLITE_WRITE_FAILED",
            msg=f"Startup: failed to clean up RUNNING cycles: {exc}",
        )


def _check_triggers_and_engage(config: DaemonConfig) -> None:
    """Evaluate all kill switch triggers. Engage if any triggered."""
    if is_engaged(config.db_path):
        return

    results = check_all_triggers(
        db_path=config.db_path,
        audit_path=config.audit_path,
        heartbeat_dir=Path(config.heartbeat_dir),
    )

    triggered = [r for r in results if r.triggered]
    for t in triggered:
        log_debug(
            "CORTEX_TRIGGER_FIRED",
            payload={"trigger": t.trigger_id, "reason": t.reason},
            level="WARN",
            error_code="KILL_SWITCH_ENGAGED",
            msg=f"Kill switch trigger fired: {t.trigger_id} — {t.reason}",
        )
        engage(
            reason=t.reason,
            trigger=t.trigger_id,
            db_path=config.db_path,
            audit_path=config.audit_path,
        )
        return  # Engage on first trigger — stop checking


def run_daemon_loop(config: DaemonConfig | None = None) -> None:
    """Main Cortex daemon loop.

    Uses simple time.sleep loop (not asyncio) for deterministic timing
    and minimal dependency surface.

    Args:
        config: Optional daemon configuration. Loads from file if None.
    """
    if config is None:
        config = _load_daemon_config()

    log_debug(
        "CORTEX_DAEMON_STARTING",
        payload={
            "heartbeat_interval": config.heartbeat_interval,
            "health_check_interval": config.health_check_interval,
            "audit_check_interval": config.audit_check_interval,
            "db_path": config.db_path,
        },
        level="INFO",
        msg="Cortex daemon starting",
    )

    # Run startup checks
    _startup_check(config)

    # Timers
    last_health_check: float = 0.0
    last_audit_check: float = 0.0

    while True:
        now = time.time()

        # Check kill switch — if engaged, slow loop
        if is_engaged(config.db_path):
            log_debug(
                "CORTEX_KILL_SWITCH_ENGAGED_LOOP",
                payload={},
                level="INFO",
                msg="Kill switch ENGAGED — sleeping 30s",
            )
            write_heartbeat("pmacs-cortex", heartbeat_dir=Path(config.heartbeat_dir))
            time.sleep(30)
            continue

        # Every heartbeat_interval: write own heartbeat
        write_heartbeat("pmacs-cortex", heartbeat_dir=Path(config.heartbeat_dir))

        # Every health_check_interval: check other processes
        if now - last_health_check >= config.health_check_interval:
            last_health_check = now
            statuses = check_heartbeats(
                MONITORED_PROCESSES,
                heartbeat_dir=Path(config.heartbeat_dir),
            )
            stale = [s.proc for s in statuses if s.is_stale]
            if stale:
                log_debug(
                    "CORTEX_STALE_HEARTBEATS",
                    payload={"stale": stale},
                    level="WARN",
                    error_code="PROCESS_HEARTBEAT_MISSED",
                    msg=f"Stale heartbeats: {stale}",
                )

        # Every audit_check_interval: verify audit chain + replicate + all triggers
        if now - last_audit_check >= config.audit_check_interval:
            last_audit_check = now
            _replicate_audit(config.audit_path)
            _check_triggers_and_engage(config)

        time.sleep(config.heartbeat_interval)


def main() -> None:
    """Entry point for pmacs-cortex process."""

    def _signal_handler(signum: int, frame: object) -> None:
        log_debug(
            "CORTEX_SHUTDOWN",
            payload={"signal": signum},
            level="INFO",
            msg="Cortex daemon shutting down",
        )
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    log_debug(
        "CORTEX_STARTING",
        payload={"pid": __import__("os").getpid()},
        level="INFO",
        msg="pmacs-cortex starting",
    )

    run_daemon_loop()


if __name__ == "__main__":
    main()
