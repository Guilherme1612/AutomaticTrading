"""Structured JSONL debug log (Architecture.md §5).

Every WARN+ requires error_code from §5.5 registry.
cycle_id required on cycle-scoped events.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pmacs.logsys.error_classifier import VALID_ERROR_CODES


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


REQUIRES_ERROR_CODE = {LogLevel.WARN, LogLevel.ERROR}

# Module-level log file path (set during initialization)
_log_path: Path | None = None
_log_fd = None


def set_log_path(path: str | Path) -> None:
    """Set the debug log file path."""
    global _log_path
    _log_path = Path(path)
    _log_path.parent.mkdir(parents=True, exist_ok=True)


def _ensure_fd():
    global _log_fd
    if _log_fd is None and _log_path is not None:
        _log_fd = open(_log_path, "a")
    return _log_fd


def log_debug(
    event_type: str,
    payload: dict[str, Any] | None = None,
    level: str | LogLevel = LogLevel.INFO,
    error_code: str | None = None,
    cycle_id: str | None = None,
    msg: str = "",
) -> None:
    """Emit a structured debug event.

    Args:
        event_type: Canonical event name (e.g., 'BOOT_CYCLE_SKIPPED').
        payload: Structured data for the event.
        level: Log severity (DEBUG/INFO/WARN/ERROR).
        error_code: Required for WARN and ERROR levels. Must be from §5.5 registry.
        cycle_id: Required for cycle-scoped events.
        msg: Human-readable message.

    Raises:
        ValueError: If error_code is missing for WARN+ or is not in the registry.
    """
    level = LogLevel(level) if isinstance(level, str) else level

    # Validate error_code requirement
    if level in REQUIRES_ERROR_CODE:
        if error_code is None:
            raise ValueError(
                f"error_code REQUIRED for {level.value} level events "
                f"(Architecture.md §16.14). Event: {event_type}"
            )
        if error_code not in VALID_ERROR_CODES:
            raise ValueError(
                f"Invalid error_code '{error_code}'. Must be from §5.5 registry. "
                f"Event: {event_type}"
            )

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level.value,
        "event": event_type,
    }
    if error_code:
        entry["error_code"] = error_code
    if cycle_id:
        entry["cycle_id"] = cycle_id
    if msg:
        entry["msg"] = msg
    if payload:
        entry["payload"] = payload

    line = json.dumps(entry, sort_keys=True, default=str)

    # Write to file if configured
    fd = _ensure_fd()
    if fd is not None:
        fd.write(line + "\n")
        fd.flush()
        os.fsync(fd.fileno())

    # Also emit to stderr for development
    if level in (LogLevel.WARN, LogLevel.ERROR):
        print(line, file=sys.stderr)
