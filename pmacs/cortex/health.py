"""Heartbeat monitoring for PMACS processes (Architecture.md §4.6, §13.1).

Each process writes a timestamp to a shared heartbeat directory.
Cortex checks all heartbeats every 10s to detect stale/dead processes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from pmacs.config import data_dir as _data_dir

HEARTBEAT_DIR = _data_dir() / "heartbeats"
STALE_THRESHOLD_S = 30.0


@dataclass(frozen=True)
class HeartbeatStatus:
    """Heartbeat freshness result for a single process."""

    proc: str
    last_ts: float | None
    is_stale: bool


def write_heartbeat(proc_name: str, heartbeat_dir: Path = HEARTBEAT_DIR) -> None:
    """Write current timestamp as heartbeat file.

    Args:
        proc_name: Process identifier (e.g. 'cortex', 'inference').
        heartbeat_dir: Directory containing heartbeat files.
    """
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    ts_file = heartbeat_dir / f"{proc_name}.ts"
    ts_file.write_text(str(int(time.time())))


def check_heartbeats(
    processes: list[str],
    heartbeat_dir: Path = HEARTBEAT_DIR,
    stale_threshold: float = STALE_THRESHOLD_S,
) -> list[HeartbeatStatus]:
    """Check all processes for heartbeat freshness.

    Args:
        processes: List of process names to check.
        heartbeat_dir: Directory containing heartbeat files.
        stale_threshold: Seconds after which a heartbeat is considered stale.

    Returns:
        List of HeartbeatStatus, one per process.
    """
    now = time.time()
    results: list[HeartbeatStatus] = []
    for proc in processes:
        ts_file = heartbeat_dir / f"{proc}.ts"
        last_ts: float | None = None
        is_stale = True
        if ts_file.exists():
            try:
                last_ts = float(ts_file.read_text().strip())
                is_stale = (now - last_ts) > stale_threshold
            except (ValueError, OSError):
                pass
        results.append(HeartbeatStatus(proc=proc, last_ts=last_ts, is_stale=is_stale))
    return results
