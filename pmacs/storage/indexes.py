"""SQLite index management (Architecture.md §3 repo tree, §8.5).

Centralizes all index creation logic. Ensures indexes exist for
query performance on the SQLite tables defined in pmacs.storage.sqlite.
"""
from __future__ import annotations

import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
from pathlib import Path

# All indexes that should exist on the PMACS SQLite database.
# Keep in sync with SCHEMA_SQL in pmacs.storage.sqlite.
INDEX_DEFINITIONS: list[tuple[str, str]] = [
    # Cycles
    ("idx_cycles_state", "CREATE INDEX IF NOT EXISTS idx_cycles_state ON cycles(state)"),
    ("idx_cycles_closed_at", "CREATE INDEX IF NOT EXISTS idx_cycles_closed_at ON cycles(closed_at DESC)"),
    # Holdings
    ("idx_holdings_ticker", "CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(ticker)"),
    ("idx_holdings_state", "CREATE INDEX IF NOT EXISTS idx_holdings_state ON holdings(state)"),
    # Consistency drift
    ("idx_consistency_drift_resolved", "CREATE INDEX IF NOT EXISTS idx_consistency_drift_resolved ON consistency_drift(resolved_at)"),
    # Stop events — frequently queried by holding_id and ticker
    ("idx_stop_events_holding_id", "CREATE INDEX IF NOT EXISTS idx_stop_events_holding_id ON stop_events(holding_id)"),
    ("idx_stop_events_ticker", "CREATE INDEX IF NOT EXISTS idx_stop_events_ticker ON stop_events(ticker)"),
    # Queue — frequently queried by cycle_id
    ("idx_queue_cycle_id", "CREATE INDEX IF NOT EXISTS idx_queue_cycle_id ON queue(cycle_id)"),
    # Op idempotency — queried by cycle_id for resume
    ("idx_op_idempotency_cycle_id", "CREATE INDEX IF NOT EXISTS idx_op_idempotency_cycle_id ON op_idempotency(cycle_id)"),
    # Operator overrides — queried by cycle_id
    ("idx_operator_overrides_cycle_id", "CREATE INDEX IF NOT EXISTS idx_operator_overrides_cycle_id ON operator_overrides(cycle_id)"),
    # Mutation proposals — queried by status
    ("idx_mutation_proposals_status", "CREATE INDEX IF NOT EXISTS idx_mutation_proposals_status ON mutation_proposals(status)"),
    # Paper account — queried by snapshot_at for latest
    ("idx_paper_account_snapshot_at", "CREATE INDEX IF NOT EXISTS idx_paper_account_snapshot_at ON paper_account(snapshot_at DESC)"),
]


def create_indexes(db_path: str | Path) -> list[str]:
    """Ensure all SQLite indexes exist.

    Idempotent — uses CREATE INDEX IF NOT EXISTS.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of index names that were ensured.
    """
    path = Path(db_path)
    if not path.exists():
        # Database not yet initialized — init_db from sqlite.py will
        # create indexes via SCHEMA_SQL. No-op here.
        return []

    created: list[str] = []
    conn = _sql_connect(path)
    try:
        for idx_name, idx_sql in INDEX_DEFINITIONS:
            conn.execute(idx_sql)
            created.append(idx_name)
        conn.commit()
    finally:
        conn.close()

    return created


def verify_indexes(db_path: str | Path) -> dict[str, bool]:
    """Verify that all expected indexes exist.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Dict mapping index name to existence status (True/False).
    """
    path = Path(db_path)
    if not path.exists():
        return {name: False for name, _ in INDEX_DEFINITIONS}

    conn = _sql_connect(db_path)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    finally:
        conn.close()

    return {name: name in existing for name, _ in INDEX_DEFINITIONS}
