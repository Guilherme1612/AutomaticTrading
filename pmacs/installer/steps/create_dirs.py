"""Wizard step: create PMACS directory structure.

Creates all required directories for PMACS operation.
"""
from __future__ import annotations

from pathlib import Path

from pmacs.installer.wizard import Wizard

# Standard PMACS directories
PMACS_DIRS = [
    "data",
    "data/sqlite",
    "data/kuzudb",
    "data/qdrant",
    "data/duckdb",
    "logs",
    "config",
    "keys",
    "models",
    "audit",
]


def run(wizard: Wizard, base_path: Path | None = None) -> dict:
    """Create PMACS directory structure.

    Args:
        wizard: The wizard instance.
        base_path: Base directory for PMACS data. Defaults to /var/db/pmacs.

    Returns:
        Dict with created directories and any errors.
    """
    if base_path is None:
        base_path = Path("/var/db/pmacs")

    created: list[str] = []
    errors: list[str] = []

    for dir_name in PMACS_DIRS:
        dir_path = base_path / dir_name
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            created.append(str(dir_path))
        except OSError as e:
            errors.append(f"{dir_path}: {e}")

    return {
        "base_path": str(base_path),
        "created": created,
        "errors": errors,
        "all_ok": len(errors) == 0,
    }
