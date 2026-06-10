"""SQLite initialization — all tables from Architecture.md §8.5."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Default busy-wait timeout (ms) for WAL lock contention between the 8
# PMACS processes.  Without this, concurrent writers get SQLITE_BUSY
# immediately (default 0 ms) and silently drop writes.
_BUSY_TIMEOUT_MS = 5000


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with PMACS defaults (busy_timeout, WAL).

    All production code should use this instead of raw sqlite3.connect().
    """
    path_str = str(path)
    if read_only:
        uri = f"file:{path_str}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(path_str)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    return conn

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
    conviction_score REAL,
    thesis_summary TEXT,
    current_price_usd REAL,
    price_target_usd REAL
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
    processed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    stop_type_category TEXT NOT NULL DEFAULT 'FIXED',
    updated_at TEXT
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

-- Dead letter queue (Architecture.md §9, §14.1)
CREATE TABLE IF NOT EXISTS dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type TEXT NOT NULL,
    target_db TEXT NOT NULL,
    payload TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING'
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

-- Mutation candidates view (compatibility alias for dashboard)
CREATE VIEW IF NOT EXISTS mutation_candidates AS
SELECT
    id AS candidate_id,
    dimension,
    target,
    baseline_value,
    candidate_value,
    status,
    fde_cluster_trigger,
    proposed_at,
    started_at,
    completed_at,
    effect_size,
    p_value,
    sample_size,
    NULL AS trending_direction
FROM mutation_proposals;

-- Mutation log (promotion/rejection/rollback audit trail)
CREATE TABLE IF NOT EXISTS mutation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    target TEXT NOT NULL,
    promoted_at TEXT,
    rolled_back_at TEXT,
    status TEXT NOT NULL DEFAULT 'promoted'
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
    result_hash TEXT,
    PRIMARY KEY (cycle_id, op_seq, op_type)
);

-- Scan records (Phase 9 step 13n)
CREATE TABLE IF NOT EXISTS scan_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    conviction_score REAL,
    direction TEXT,
    created_at TEXT NOT NULL,
    price_usd REAL
);

-- Lessons (Phase 9 step 23)
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT,
    lesson_type TEXT,
    text TEXT,
    evidence_ids TEXT,
    cycle_id TEXT,
    created_at TEXT
);

-- Failure classifications (Phase 9 step 25)
CREATE TABLE IF NOT EXISTS failure_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holding_id TEXT,
    taxonomy TEXT,
    severity REAL,
    summary TEXT,
    cycle_id TEXT,
    classified_at TEXT
);

-- Pricing table (Phase 16: Token-Cost Accounting)
CREATE TABLE IF NOT EXISTS pricing_table (
    model_id TEXT PRIMARY KEY,
    input_price_per_token REAL NOT NULL,
    output_price_per_token REAL NOT NULL,
    cached_input_price_per_token REAL,
    per_request_fee REAL NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'openrouter'
);

-- Budget state (Phase 16: current period tracking)
CREATE TABLE IF NOT EXISTS budget_state (
    period TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    cap_usd REAL NOT NULL,
    updated_at TEXT NOT NULL
);

-- Budget history (Phase 16: archived period totals)
CREATE TABLE IF NOT EXISTS budget_history (
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    period_type TEXT NOT NULL,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    cap_usd REAL NOT NULL,
    breached INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (period_type, period_start)
);

-- Universe (operator-curated, Source.md §8, Architecture.md §6.2)
CREATE TABLE IF NOT EXISTS universe (
    ticker TEXT PRIMARY KEY,
    sector TEXT,
    subsector TEXT,
    halted INTEGER NOT NULL DEFAULT 0,
    delisted INTEGER NOT NULL DEFAULT 0,
    catalyst_type TEXT,
    pinned_priority INTEGER,
    added_at TEXT NOT NULL DEFAULT ''
);

-- Per-ticker cycle decisions
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    verdict TEXT NOT NULL,
    conviction_score REAL NOT NULL DEFAULT 0.0,
    thesis_summary TEXT,
    decided_at TEXT NOT NULL,
    priority_band INTEGER,
    FOREIGN KEY (cycle_id) REFERENCES cycles(cycle_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON decisions(cycle_id);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker);

-- Per-ticker investment memos (structured JSON, one per decision)
CREATE TABLE IF NOT EXISTS memos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    verdict TEXT NOT NULL,
    conviction_score REAL NOT NULL DEFAULT 0.0,
    memo_json TEXT NOT NULL,
    raw_text TEXT,
    decided_at TEXT NOT NULL,
    FOREIGN KEY (cycle_id) REFERENCES cycles(cycle_id)
);
CREATE INDEX IF NOT EXISTS idx_memos_ticker ON memos(ticker);
CREATE INDEX IF NOT EXISTS idx_memos_cycle ON memos(cycle_id);
CREATE INDEX IF NOT EXISTS idx_memos_decided ON memos(decided_at DESC);

-- Wizard state (first-run setup progress)
CREATE TABLE IF NOT EXISTS wizard_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def default_db_path() -> Path:
    """Return the default SQLite database path.

    Respects PMACS_DATA_DIR env var; falls back to <project_root>/data.
    """
    from pmacs.config import data_dir

    return data_dir() / "pmacs.db"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table.

    WARNING: table name is interpolated directly into SQL.
    Only call with hardcoded table names -- never user input.
    """
    import re
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table):
        raise ValueError(f"Invalid table name: {table}")
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run schema migrations for existing databases.

    Adds new columns to stop_events and op_idempotency tables if they
    don't exist yet. Converts legacy processed flag to status enum.
    """
    # stop_events migrations
    if not _column_exists(conn, "stop_events", "status"):
        conn.execute("ALTER TABLE stop_events ADD COLUMN status TEXT NOT NULL DEFAULT 'PENDING'")
        conn.execute("UPDATE stop_events SET status = CASE WHEN processed = 1 THEN 'FILLED' ELSE 'PENDING' END")
    if not _column_exists(conn, "stop_events", "stop_type_category"):
        conn.execute("ALTER TABLE stop_events ADD COLUMN stop_type_category TEXT NOT NULL DEFAULT 'FIXED'")
    if not _column_exists(conn, "stop_events", "updated_at"):
        conn.execute("ALTER TABLE stop_events ADD COLUMN updated_at TEXT")

    # holdings migrations — columns needed for post-cycle flywheel (Phase 9 Wave 4)
    if not _column_exists(conn, "holdings", "last_reeval_at"):
        conn.execute("ALTER TABLE holdings ADD COLUMN last_reeval_at TEXT")
    if not _column_exists(conn, "holdings", "abort_reason"):
        conn.execute("ALTER TABLE holdings ADD COLUMN abort_reason TEXT")
    if not _column_exists(conn, "holdings", "stop_price_usd"):
        conn.execute("ALTER TABLE holdings ADD COLUMN stop_price_usd REAL")
    if not _column_exists(conn, "holdings", "cycle_id_closed"):
        conn.execute("ALTER TABLE holdings ADD COLUMN cycle_id_closed TEXT")
    if not _column_exists(conn, "holdings", "thesis_review_due_date"):
        conn.execute("ALTER TABLE holdings ADD COLUMN thesis_review_due_date TEXT")

    # op_idempotency migrations
    if not _column_exists(conn, "op_idempotency", "result_hash"):
        conn.execute("ALTER TABLE op_idempotency ADD COLUMN result_hash TEXT")

    # dead_letter migrations — old schema had target_store/operation/resolved
    if _column_exists(conn, "dead_letter", "target_store") and not _column_exists(conn, "dead_letter", "target_db"):
        conn.execute("ALTER TABLE dead_letter ADD COLUMN op_type TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN target_db TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN queued_at TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN last_attempt_at TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN last_error TEXT")
        conn.execute("ALTER TABLE dead_letter ADD COLUMN status TEXT NOT NULL DEFAULT 'PENDING'")
        conn.execute("UPDATE dead_letter SET op_type = operation, target_db = target_store, queued_at = created_at, status = CASE WHEN resolved = 1 THEN 'RESOLVED' ELSE 'PENDING' END WHERE op_type IS NULL")
    if _column_exists(conn, "dead_letter", "last_retry_at") and not _column_exists(conn, "dead_letter", "last_attempt_at"):
        conn.execute("ALTER TABLE dead_letter ADD COLUMN last_attempt_at TEXT")
        conn.execute("UPDATE dead_letter SET last_attempt_at = last_retry_at WHERE last_attempt_at IS NULL")

    # Phase 16: Seed budget_state rows if empty, or refresh stale "today" row
    row = conn.execute("SELECT COUNT(*) FROM budget_state").fetchone()
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    month_start = datetime.now(timezone.utc).replace(day=1).strftime("%Y-%m-%d")
    if row[0] == 0:
        conn.execute(
            "INSERT INTO budget_state (period, period_start, total_cost_usd, cap_usd, updated_at) "
            "VALUES ('today', ?, 0.0, 2.00, ?)",
            [today, now],
        )
        conn.execute(
            "INSERT INTO budget_state (period, period_start, total_cost_usd, cap_usd, updated_at) "
            "VALUES ('this_month', ?, 0.0, 30.00, ?)",
            [month_start, now],
        )
    else:
        # Refresh "today" period_start if it's stale (from a previous day)
        conn.execute(
            "UPDATE budget_state SET period_start = ?, total_cost_usd = 0.0, updated_at = ? "
            "WHERE period = 'today' AND period_start != ?",
            [today, now, today],
        )
        # Refresh "this_month" period_start if stale
        conn.execute(
            "UPDATE budget_state SET period_start = ?, total_cost_usd = 0.0, updated_at = ? "
            "WHERE period = 'this_month' AND period_start != ?",
            [month_start, now, month_start],
        )

    # Wizard state table (wizard-first startup)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wizard_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()


def init_db(path: str | Path) -> sqlite3.Connection:
    """Initialize SQLite database with all PMACS tables.

    Idempotent — uses CREATE TABLE IF NOT EXISTS.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    _run_migrations(conn)
    return conn


def get_connection(path: str | Path, read_only: bool = False) -> sqlite3.Connection:
    """Get a connection to the SQLite database.

    Thin wrapper around :func:`connect` for backward compatibility.
    """
    return connect(path, read_only=read_only)
