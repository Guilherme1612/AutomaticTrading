"""Disk space monitor (Architecture.md §13.1 trigger #6).

Checks free space on the volume containing PMACS_HOME.
<2GB free triggers kill switch.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from pmacs.logsys import log_debug

_MIN_FREE_GB = 2.0
_PMACS_HOME = Path("/var/db/pmacs")


def check_disk_space(
    path: Path | None = None,
    min_free_gb: float = _MIN_FREE_GB,
) -> tuple[bool, float]:
    """Check free disk space.

    Args:
        path: Path on the volume to check. Defaults to PMACS_HOME.
        min_free_gb: Minimum free space in GB (default 2.0).

    Returns:
        Tuple of (is_triggered, free_gb).
        is_triggered is True if free space < min_free_gb.
    """
    if path is None:
        path = _PMACS_HOME

    # Ensure path exists for disk_usage to work
    check_path = path
    if not check_path.exists():
        check_path = check_path.parent
        while not check_path.exists() and check_path != check_path.parent:
            check_path = check_path.parent

    try:
        usage = shutil.disk_usage(str(check_path))
        free_gb = usage.free / (1024**3)
    except OSError as exc:
        log_debug(
            "DISK_CHECK_FAILED",
            payload={"path": str(path), "error": str(exc)},
            level="WARN",
            error_code="DISK_SPACE_LOW",
            msg=f"Failed to check disk space: {exc}",
        )
        return (True, 0.0)  # Assume triggered on failure

    is_triggered = free_gb < min_free_gb

    if is_triggered:
        log_debug(
            "DISK_SPACE_LOW",
            payload={"free_gb": round(free_gb, 2), "threshold_gb": min_free_gb},
            level="WARN",
            error_code="DISK_SPACE_LOW",
            msg=f"Disk space low: {free_gb:.2f}GB free (threshold: {min_free_gb}GB)",
        )

    return (is_triggered, free_gb)
