"""SQLite initialization — all tables from Architecture.md §8.5."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
-- Cycles
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    state TEXT NOT NULL,
    trigger TEXT NOT NULL,
    mode TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cycles_state ON cycles(state);
CREATE INDEX IF NOT EXISTS idx_cycles_closed_at ON cycles(closed_at DESC);

-- Mode history
CREATE TABLE IF NOT EXISTS mode_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_mode TEXT NOT NULL,
    to_mode TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    reason TEXT,
    operator_totp_verified INTEGER NOT NULL DEFAULT 0,
    triggered_by TEXT NOT NULL
);

-- Queue (per-cycle)
CREATE TABLE IF NOT EXISTS queue (
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    priority_band INTEGER NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    enqueued_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    operator_initiated INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cycle_id, ticker)
);

-- Persistent priority pins
CREATE TABLE IF NOT EXISTS persistent_pins (
    ticker TEXT PRIMARY KEY,
    priority_band INTEGER NOT NULL,
    pinned_at TEXT NOT NULL,
    pinned_by_operator INTEGER NOT NULL DEFAULT 1
);

-- Holdings (key fields; full data in KuzuDB)
CREATE TABLE IF NOT EXISTS holdings (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    state TEXT NOT NULL,
    cycle_id_opened TEXT NOT NULL,
    cycle_id_closed TEXT,
    entry_date TEXT,
    exit_date TEXT,
    entry_price_usd REAL,
    exit_price_usd REAL,
    position_size_usd REAL,
    sector TEXT,
    verdict TEXT,
    conviction_score REAL
);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(ticker);
CREATE INDEX IF NOT EXISTS idx_holdings_state ON holdings(state);

-- Stop events
CREATE TABLE IF NOT EXISTS stop_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holding_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    stop_type TEXT NOT NULL,
    trigger_price_usd REAL NOT NULL,
    stop_price_usd REAL NOT NULL,
    detected_at TEXT NOT NULL,
    cycle_id TEXT,
    processed INTEGER NOT NULL DEFAULT 0
);

-- Process state (crash loop detector)
CREATE TABLE IF NOT EXISTS process_state (
    proc TEXT PRIMARY KEY,
    last_started_at TEXT NOT NULL,
    restart_count_60s INTEGER NOT NULL DEFAULT 0,
    is_broken_crash_loop INTEGER NOT NULL DEFAULT 0
);

-- Paper account ledger
CREATE TABLE IF NOT EXISTS paper_account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT NOT NULL,
    cash_usd REAL NOT NULL,
    positions_value_usd REAL NOT NULL,
    total_value_usd REAL NOT NULL
);

-- FX snapshots
CREATE TABLE IF NOT EXISTS fx_snapshots (
    cycle_id TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    business_date TEXT NOT NULL,
    usd_per_eur REAL NOT NULL
);

-- Consistency drift log
CREATE TABLE IF NOT EXISTS consistency_drift (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    source_db TEXT NOT NULL,
    target_db TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    drift_type TEXT NOT NULL,
    details TEXT,
    resolved_at TEXT,
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_consistency_drift_resolved ON consistency_drift(resolved_at);

-- Operator overrides
CREATE TABLE IF NOT EXISTS operator_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    original_verdict TEXT NOT NULL,
    override_verdict TEXT NOT NULL,
    reason TEXT,
    cluster_id TEXT
);

-- Dead letter queue
CREATE TABLE IF NOT EXISTS dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    target_store TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_retry_at TEXT,
    resolved INTEGER NOT NULL DEFAULT 0
);

-- Mutation proposals
CREATE TABLE IF NOT EXISTS mutation_proposals (
    id TEXT PRIMARY KEY,
    dimension TEXT NOT NULL,
    target TEXT NOT NULL,
    baseline_value TEXT NOT NULL,
    candidate_value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    fde_cluster_trigger TEXT,
    proposed_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    effect_size REAL,
    p_value REAL,
    sample_size INTEGER
);

-- Mutation outcomes
CREATE TABLE IF NOT EXISTS mutation_outcomes (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES mutation_proposals(id),
    result TEXT NOT NULL,
    baseline_metric REAL,
    candidate_metric REAL,
    recorded_at TEXT NOT NULL
);

-- Op idempotency (cycle resume)
CREATE TABLE IF NOT EXISTS op_idempotency (
    cycle_id TEXT NOT NULL,
    op_seq INTEGER NOT NULL,
    op_type TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (cycle_id, op_seq, op_type)
);
"""


def init_db(path: str | Path) -> sqlite3.Connection:
    """Initialize SQLite database with all PMACS tables.

    Idempotent — uses CREATE TABLE IF NOT EXISTS.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_connection(path: str | Path, read_only: bool = False) -> sqlite3.Connection:
    """Get a connection to the SQLite database.

    Args:
        path: Path to the SQLite database file.
        read_only: If True, open in read-only mode (for dashboard).
    """
    path = Path(path)
    if read_only:
        uri = f"file:{path}?mode=ro"
        return sqlite3.connect(uri, uri=True)
    return sqlite3.connect(str(path))
