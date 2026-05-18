#!/usr/bin/env python3
"""DB schema migration tool (Architecture.md §3, §8.5).

Tracks migration version in SQLite `schema_version` table.
Applies pending migrations in order. Supports dry-run and rollback.

Spec: Architecture.md §8.5 (SQLite schema), §1.11 (schema versioning).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk upward from cwd to find directory containing pmacs/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pmacs").is_dir():
            return parent
    return cwd


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    """Create the schema_version tracking table if it does not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        )
    """)
    conn.commit()


def _current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    _ensure_version_table(conn)
    row = conn.execute(
        "SELECT MAX(version) FROM schema_version"
    ).fetchone()
    return int(row[0]) if row[0] is not None else 0


def _applied_versions(conn: sqlite3.Connection) -> list[int]:
    """Return all applied migration versions in order."""
    _ensure_version_table(conn)
    rows = conn.execute(
        "SELECT version FROM schema_version ORDER BY version"
    ).fetchall()
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Migration type
# ---------------------------------------------------------------------------

class Migration:
    """A single named migration with up() and down() SQL callbacks."""

    def __init__(
        self,
        version: int,
        name: str,
        up: Callable[[sqlite3.Connection], None],
        down: Callable[[sqlite3.Connection], None],
    ):
        self.version = version
        self.name = name
        self._up = up
        self._down = down

    def apply(self, conn: sqlite3.Connection) -> None:
        self._up(conn)

    def rollback(self, conn: sqlite3.Connection) -> None:
        self._down(conn)

    @property
    def checksum(self) -> str:
        """Deterministic identity for this migration (name-based)."""
        import hashlib
        return hashlib.sha256(f"{self.version}:{self.name}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------

MIGRATIONS: list[Migration] = []


def _migration(version: int, name: str):
    """Decorator to register a migration with up/down functions."""
    def decorator(cls):
        instance = Migration(version, name, cls.up, cls.down)
        MIGRATIONS.append(instance)
        # Keep sorted by version
        MIGRATIONS.sort(key=lambda m: m.version)
        return cls
    return decorator


# ---------------------------------------------------------------------------
# Migrations — add new migrations here
# ---------------------------------------------------------------------------

@_migration(1, "add_reeval_columns")
class _M001:
    """Add last_reeval_at and abort_reason to holdings."""

    @staticmethod
    def up(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE holdings ADD COLUMN last_reeval_at TEXT")
        conn.execute("ALTER TABLE holdings ADD COLUMN abort_reason TEXT")

    @staticmethod
    def down(conn: sqlite3.Connection) -> None:
        # SQLite does not support DROP COLUMN before 3.35.0.
        # For older SQLite, the column remains but is ignored.
        conn.execute("ALTER TABLE holdings DROP COLUMN IF EXISTS last_reeval_at")
        conn.execute("ALTER TABLE holdings DROP COLUMN IF EXISTS abort_reason")


@_migration(2, "add_stop_price_to_holdings")
class _M002:
    """Add stop_price_usd and cycle_id_closed to holdings."""

    @staticmethod
    def up(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE holdings ADD COLUMN stop_price_usd REAL")
        conn.execute("ALTER TABLE holdings ADD COLUMN cycle_id_closed TEXT")

    @staticmethod
    def down(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE holdings DROP COLUMN IF EXISTS stop_price_usd")
        conn.execute("ALTER TABLE holdings DROP COLUMN IF EXISTS cycle_id_closed")


@_migration(3, "add_result_hash_to_idempotency")
class _M003:
    """Add result_hash column to op_idempotency."""

    @staticmethod
    def up(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE op_idempotency ADD COLUMN result_hash TEXT")

    @staticmethod
    def down(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE op_idempotency DROP COLUMN IF EXISTS result_hash")


@_migration(4, "add_dead_letter_status_columns")
class _M004:
    """Add status tracking columns to dead_letter."""

    @staticmethod
    def up(conn: sqlite3.Connection) -> None:
        conn.execute("ALTER TABLE dead_letter ADD COLUMN op_type TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN target_db TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN queued_at TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN last_attempt_at TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN last_error TEXT")
        conn.execute(
            "ALTER TABLE dead_letter ADD COLUMN status TEXT NOT NULL DEFAULT 'PENDING'"
        )

    @staticmethod
    def down(conn: sqlite3.Connection) -> None:
        for col in ("op_type", "target_db", "queued_at", "last_attempt_at", "last_error", "status"):
            conn.execute(f"ALTER TABLE dead_letter DROP COLUMN IF EXISTS {col}")


@_migration(5, "add_stop_events_status")
class _M005:
    """Add status, stop_type_category, updated_at to stop_events."""

    @staticmethod
    def up(conn: sqlite3.Connection) -> None:
        conn.execute(
            "ALTER TABLE stop_events ADD COLUMN status TEXT NOT NULL DEFAULT 'PENDING'"
        )
        conn.execute(
            "UPDATE stop_events SET status = CASE "
            "WHEN processed = 1 THEN 'FILLED' ELSE 'PENDING' END"
        )
        conn.execute(
            "ALTER TABLE stop_events ADD COLUMN stop_type_category TEXT NOT NULL DEFAULT 'FIXED'"
        )
        conn.execute("ALTER TABLE stop_events ADD COLUMN updated_at TEXT")

    @staticmethod
    def down(conn: sqlite3.Connection) -> None:
        for col in ("status", "stop_type_category", "updated_at"):
            conn.execute(f"ALTER TABLE stop_events DROP COLUMN IF EXISTS {col}")


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def apply_migrations(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Apply all pending migrations. Returns list of applied migration names."""
    current = _current_version(conn)
    pending = [m for m in MIGRATIONS if m.version > current]

    if not pending:
        if verbose:
            print(f"Schema at version {current}. No pending migrations.")
        return []

    applied: list[str] = []
    for m in pending:
        if verbose:
            action = "Would apply" if dry_run else "Applying"
            print(f"  {action}: v{m.version} — {m.name}")

        if not dry_run:
            m.apply(conn)
            conn.execute(
                "INSERT INTO schema_version (version, name, applied_at, checksum) "
                "VALUES (?, ?, ?, ?)",
                (m.version, m.name, datetime.now(timezone.utc).isoformat(), m.checksum),
            )
            conn.commit()
        applied.append(m.name)

    return applied


def rollback_migrations(
    conn: sqlite3.Connection,
    count: int = 1,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Rollback the last N applied migrations. Returns list of rolled-back names."""
    applied_vers = _applied_versions(conn)
    if not applied_vers:
        if verbose:
            print("No applied migrations to rollback.")
        return []

    # Find migration objects for applied versions
    applied_map = {m.version: m for m in MIGRATIONS}
    to_rollback = []
    for v in reversed(applied_vers):
        if v in applied_map:
            to_rollback.append(applied_map[v])
        if len(to_rollback) >= count:
            break

    if not to_rollback:
        if verbose:
            print("No matching migrations found to rollback.")
        return []

    rolled: list[str] = []
    for m in to_rollback:
        if verbose:
            action = "Would rollback" if dry_run else "Rolling back"
            print(f"  {action}: v{m.version} — {m.name}")

        if not dry_run:
            m.rollback(conn)
            conn.execute(
                "DELETE FROM schema_version WHERE version = ?",
                (m.version,),
            )
            conn.commit()
        rolled.append(m.name)

    return rolled


def show_status(conn: sqlite3.Connection) -> dict:
    """Return migration status as a dict."""
    current = _current_version(conn)
    applied = _applied_versions(conn)
    pending = [m.version for m in MIGRATIONS if m.version > current]

    return {
        "current_version": current,
        "applied_count": len(applied),
        "pending_count": len(pending),
        "applied": applied,
        "pending": pending,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PMACS DB schema migration tool (Architecture.md §3)"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to SQLite database (default: data/pmacs.db)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # -- apply --
    p_apply = sub.add_parser("apply", help="Apply pending migrations")
    p_apply.add_argument("--dry-run", action="store_true", help="Show what would be applied")
    p_apply.add_argument("--verbose", "-v", action="store_true")

    # -- rollback --
    p_rollback = sub.add_parser("rollback", help="Rollback applied migrations")
    p_rollback.add_argument("--count", "-n", type=int, default=1, help="Number of migrations to rollback")
    p_rollback.add_argument("--dry-run", action="store_true", help="Show what would be rolled back")
    p_rollback.add_argument("--verbose", "-v", action="store_true")

    # -- status --
    p_status = sub.add_parser("status", help="Show migration status")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Resolve DB path
    if args.db_path:
        db_path = args.db_path
    else:
        project_root = _find_project_root()
        db_path = project_root / "data" / "pmacs.db"

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
        sys.exit(2)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        if args.command == "apply":
            applied = apply_migrations(conn, dry_run=args.dry_run, verbose=args.verbose)
            if args.dry_run:
                print(f"Would apply {len(applied)} migration(s)")
            else:
                print(f"Applied {len(applied)} migration(s)")
            if not applied:
                sys.exit(0)

        elif args.command == "rollback":
            rolled = rollback_migrations(
                conn, count=args.count, dry_run=args.dry_run, verbose=args.verbose
            )
            if args.dry_run:
                print(f"Would rollback {len(rolled)} migration(s)")
            else:
                print(f"Rolled back {len(rolled)} migration(s)")
            if not rolled:
                sys.exit(0)

        elif args.command == "status":
            status = show_status(conn)
            if args.json:
                print(json.dumps(status, indent=2))
            else:
                print(f"Current version: {status['current_version']}")
                print(f"Applied: {status['applied_count']}")
                print(f"Pending: {status['pending_count']}")
                if status["pending"]:
                    for v in status["pending"]:
                        m = next((x for x in MIGRATIONS if x.version == v), None)
                        name = m.name if m else "unknown"
                        print(f"  v{v} — {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
