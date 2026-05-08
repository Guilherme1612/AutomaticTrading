"""Cortex daemon — main process loop (Architecture.md §4).

Responsibilities:
- Every 5s: write own heartbeat
- Every 10s: check all other process heartbeats
- Every 60s: verify audit chain integrity
- On startup: verify all processes have heartbeats within 30s
- Kill switch trigger: audit chain failure, disk <2GB, crash loops
- Configurable intervals via config/resources.toml
"""
from __future__ import annotations

import signal
import sqlite3
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
    db_path: str = "/var/db/pmacs/pmacs.db"
    audit_path: str = "/var/db/pmacs/audit.log"
    heartbeat_dir: str = "/var/db/pmacs/heartbeat"


def _load_daemon_config() -> DaemonConfig:
    """Load daemon config from resources.toml if available."""
    try:
        from pmacs.config import load_config

        config = load_config()
        return DaemonConfig(
            heartbeat_interval=5,
            health_check_interval=10,
            audit_check_interval=60,
        )
    except Exception:
        return DaemonConfig()


def _startup_check(config: DaemonConfig) -> None:
    """On startup: verify all processes have heartbeats within grace period.

    Logs warnings for stale processes but does not abort startup.
    Kill switch should already be ARMED from previous session.
    """
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

        # Every audit_check_interval: verify audit chain + all triggers
        if now - last_audit_check >= config.audit_check_interval:
            last_audit_check = now
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
