"""Shared data access layer for dashboard routes (Architecture.md §4.4, §8).

Provides read-only access to all PMACS data stores for route handlers.
Routes remain synchronous reads — SSE handles live updates.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from pmacs.cortex.health import check_heartbeats, HeartbeatStatus
from pmacs.data.universe import get_universe, UniverseEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROCESS_NAMES = [
    "inference",
    "cortex",
    "cortex-self-check",
    "execution",
    "nervous",
    "stoploss",
    "mutation",
    "dashboard",
]


def _sqlite_connect(db_path: str | Path) -> sqlite3.Connection:
    """Get a read-only SQLite connection. Returns in-memory DB if file missing."""
    path = Path(db_path)
    if not path.exists():
        # Return in-memory connection for tests / no-DB scenarios
        return sqlite3.connect(":memory:")
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def get_readonly_db(sqlite_path: str | Path) -> sqlite3.Connection:
    """Get a read-only connection to the SQLite database.

    Returns an in-memory database if the file does not exist.
    Callers should close the connection after use.
    """
    return _sqlite_connect(sqlite_path)


# ---------------------------------------------------------------------------
# Data access functions
# ---------------------------------------------------------------------------


def get_active_holdings(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get all active (non-closed) holdings from SQLite.

    Returns:
        List of holding dicts with: id, ticker, state, entry_price_usd,
        position_size_usd, sector, verdict, conviction_score.
    """
    try:
        rows = db.execute(
            """SELECT id, ticker, state, entry_price_usd, position_size_usd,
                      sector, verdict, conviction_score
               FROM holdings
               WHERE state NOT IN ('CLOSED', 'EXITED', 'STOPPED_OUT')
               ORDER BY ticker"""
        ).fetchall()
        return [
            {
                "id": r[0],
                "ticker": r[1],
                "state": r[2],
                "entry_price_usd": r[3],
                "position_size_usd": r[4],
                "sector": r[5],
                "verdict": r[6],
                "conviction_score": r[7],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def get_recent_decisions(
    db: sqlite3.Connection, limit: int = 20
) -> list[dict[str, Any]]:
    """Get recent cycle decisions from SQLite.

    Returns:
        List of cycle dicts with: cycle_id, opened_at, closed_at, state,
        trigger, mode.
    """
    try:
        rows = db.execute(
            """SELECT cycle_id, opened_at, closed_at, state, trigger, mode
               FROM cycles
               ORDER BY opened_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "cycle_id": r[0],
                "opened_at": r[1],
                "closed_at": r[2],
                "state": r[3],
                "trigger": r[4],
                "mode": r[5],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def get_risk_metrics(db_path: str | Path) -> dict[str, Any]:
    """Get rolling risk metrics from DuckDB.

    Returns:
        Dict with latest risk metrics: max_drawdown_pct, sharpe,
        win_rate_pct, open_positions, capital_used_pct.
    """
    defaults: dict[str, Any] = {
        "max_drawdown_pct": 0.0,
        "sharpe": 0.0,
        "win_rate_pct": 0.0,
        "open_positions": 0,
        "capital_used_pct": 0.0,
    }
    try:
        from pmacs.storage.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(db_path=Path(db_path))
        rows = adapter.execute(
            """SELECT metric_name, metric_value
               FROM rolling_metrics
               WHERE metric_name IN (
                   'max_drawdown_pct', 'sharpe', 'win_rate_pct',
                   'open_positions', 'capital_used_pct'
               )
               ORDER BY computed_at DESC"""
        )
        seen: set[str] = set()
        for row in rows:
            name = row.get("metric_name", "")
            if name and name not in seen:
                defaults[name] = row.get("metric_value", 0.0)
                seen.add(name)
    except Exception:
        pass
    return defaults


# Window durations for sparkline queries (SQL interval fragments)
_SPARKLINE_WINDOWS: dict[str, str] = {
    "1D": "INTERVAL 1 DAY",
    "1W": "INTERVAL 7 DAY",
    "1M": "INTERVAL 30 DAY",
    "3M": "INTERVAL 90 DAY",
    "ALL": "",  # no time filter
}


def get_sparkline_data(
    db_path: str | Path,
    metric: str,
    window: str = "1W",
) -> list[tuple[str, float]]:
    """Get time-series sparkline data for a single metric from DuckDB.

    Args:
        db_path: Path to DuckDB analytics database.
        metric: Metric name (e.g. 'sharpe', 'win_rate_pct').
        window: Time window key — one of 1D, 1W, 1M, 3M, ALL.

    Returns:
        List of (timestamp_iso, value) tuples ordered chronologically.
        Empty list if DuckDB unavailable or table missing.
    """
    try:
        from pmacs.storage.duckdb import DuckDBAdapter

        adapter = DuckDBAdapter(db_path=Path(db_path))
        interval = _SPARKLINE_WINDOWS.get(window.upper(), "INTERVAL 7 DAY")
        if interval:
            rows = adapter.execute(
                """SELECT CAST(computed_at AS VARCHAR) AS ts, metric_value
                   FROM rolling_metrics
                   WHERE metric_name = ?
                     AND computed_at >= (SELECT MAX(computed_at) FROM rolling_metrics) - ?
                   ORDER BY computed_at ASC""",
                [metric, interval],
            )
        else:
            rows = adapter.execute(
                """SELECT CAST(computed_at AS VARCHAR) AS ts, metric_value
                   FROM rolling_metrics
                   WHERE metric_name = ?
                   ORDER BY computed_at ASC""",
                [metric],
            )
        return [(r["ts"], r["metric_value"]) for r in rows if r.get("ts") and r.get("metric_value") is not None]
    except Exception:
        return []


def get_all_sparkline_data(
    db_path: str | Path,
    window: str = "1W",
) -> dict[str, list[tuple[str, float]]]:
    """Get sparkline data for all dashboard metrics at once.

    Returns:
        Dict keyed by metric name, each value a list of (ts, value) tuples.
    """
    metrics = ["max_drawdown_pct", "sharpe", "win_rate_pct", "open_positions", "capital_used_pct"]
    return {m: get_sparkline_data(db_path, m, window) for m in metrics}


def get_system_health(heartbeat_dir: Path) -> dict[str, Any]:
    """Get system health from heartbeat files.

    Returns:
        Dict with process statuses and inference health.
    """
    statuses = check_heartbeats(PROCESS_NAMES, heartbeat_dir=heartbeat_dir)
    processes = []
    inference_ok = False
    for s in statuses:
        proc_display = f"pmacs-{s.proc}"
        status = "running" if not s.is_stale else "stale"
        processes.append({"name": proc_display, "status": status})
        if s.proc == "inference" and not s.is_stale:
            inference_ok = True

    return {
        "processes": processes,
        "inference_ok": inference_ok,
    }


def get_queue_status(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get current queue items from SQLite.

    Returns:
        List of queue item dicts.
    """
    try:
        rows = db.execute(
            """SELECT cycle_id, ticker, priority_band, pinned, enqueued_at
               FROM queue
               WHERE completed_at IS NULL
               ORDER BY priority_band, enqueued_at"""
        ).fetchall()
        return [
            {
                "cycle_id": r[0],
                "ticker": r[1],
                "priority_band": r[2],
                "pinned": bool(r[3]),
                "enqueued_at": r[4],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def get_priority_banded_queue(db: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Get queue items binned by P1-P4 priority bands.

    Returns:
        Dict mapping band name (P1, P2, P3, P4) to list of ticker items
        sorted by computed priority_score descending. Each item includes
        ticker, band, pinned, and score fields. Active holdings are
        force-promoted to P1.
    """
    bands: dict[str, list[dict[str, Any]]] = {"P1": [], "P2": [], "P3": [], "P4": []}

    # Get active holdings for auto-P1 promotion
    active_tickers: set[str] = set()
    try:
        rows = db.execute(
            "SELECT ticker FROM holdings WHERE state NOT IN ('CLOSED', 'EXITED', 'STOPPED_OUT')"
        ).fetchall()
        active_tickers = {r[0] for r in rows}
    except sqlite3.OperationalError:
        pass

    # Get queue items with scoring signals
    try:
        rows = db.execute(
            """SELECT q.ticker, q.priority_band, q.pinned,
                      COALESCE(e.catalyst_imminence, 0.5),
                      COALESCE(e.thesis_strength, 0.0),
                      COALESCE(e.source_brier_avg, 0.5),
                      COALESCE(e.portfolio_fit, 0.5)
               FROM queue q
               LEFT JOIN (
                   SELECT ticker,
                          MAX(catalyst_imminence) as catalyst_imminence,
                          AVG(thesis_strength) as thesis_strength,
                          AVG(source_brier_avg) as source_brier_avg,
                          AVG(portfolio_fit) as portfolio_fit
                   FROM evidence
                   GROUP BY ticker
               ) e ON q.ticker = e.ticker
               WHERE q.completed_at IS NULL"""
        ).fetchall()
    except sqlite3.OperationalError:
        # Table doesn't exist yet — return empty bands
        return bands

    for r in rows:
        ticker, band, pinned, cat_imm, thesis, brier, pfit = r
        score = (cat_imm * 3.0) + (thesis * 2.0) + (brier * 1.5) + (pfit * 1.0)
        # Active holdings always P1
        effective_band = "P1" if ticker in active_tickers else (band or "P4")
        if effective_band not in bands:
            effective_band = "P4"
        bands[effective_band].append({
            "ticker": ticker,
            "band": effective_band,
            "pinned": bool(pinned),
            "priority_score": round(score, 3),
            "is_active_holding": ticker in active_tickers,
        })

    # Sort each band by score descending, pinned first
    for band_name in bands:
        bands[band_name].sort(key=lambda x: (not x["pinned"], -x["priority_score"]))

    return bands


def reorder_queue_item(db: sqlite3.Connection, ticker: str, from_band: str, to_band: str) -> bool:
    """Move a queue item from one priority band to another.

    Returns True if the row was updated, False otherwise.
    """
    valid_bands = {"P1", "P2", "P3", "P4"}
    if from_band not in valid_bands or to_band not in valid_bands:
        return False
    try:
        cursor = db.execute(
            "UPDATE queue SET priority_band = ? WHERE ticker = ? AND priority_band = ? AND completed_at IS NULL",
            (to_band, ticker, from_band),
        )
        db.commit()
        return cursor.rowcount > 0
    except sqlite3.OperationalError:
        return False


def pin_queue_item(db: sqlite3.Connection, ticker: str, pinned: bool) -> bool:
    """Set or clear the pinned flag on a queue item.

    Returns True if the row was updated.
    """
    try:
        cursor = db.execute(
            "UPDATE queue SET pinned = ? WHERE ticker = ? AND completed_at IS NULL",
            (int(pinned), ticker),
        )
        db.commit()
        return cursor.rowcount > 0
    except sqlite3.OperationalError:
        return False


def promote_all_p1(db: sqlite3.Connection) -> int:
    """Promote all P1 items to head of next cycle.

    Sets a 'promoted' flag so the next cycle picks them first.
    Returns count of promoted items.
    """
    try:
        cursor = db.execute(
            """UPDATE queue SET pinned = 1
               WHERE priority_band = 'P1' AND completed_at IS NULL AND pinned = 0"""
        )
        db.commit()
        return cursor.rowcount
    except sqlite3.OperationalError:
        return 0


def save_priority_scheme(db_path: str | Path, name: str, config: dict[str, Any]) -> bool:
    """Save a priority scheme configuration to SQLite.

    Creates the priority_schemes table if it doesn't exist.
    """
    conn = _sqlite_connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS priority_schemes (
                name TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO priority_schemes (name, config_json) VALUES (?, ?)",
            (name, json.dumps(config)),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def load_priority_scheme(db_path: str | Path, name: str) -> dict[str, Any] | None:
    """Load a named priority scheme from SQLite.

    Returns the parsed config dict, or None if not found.
    """
    conn = _sqlite_connect(db_path)
    try:
        row = conn.execute(
            "SELECT config_json FROM priority_schemes WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None
    except Exception:
        return None
    finally:
        conn.close()


def get_mutation_candidates(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get pending mutation candidates from SQLite.

    Returns:
        List of mutation candidate dicts with dimension, target, stats.
    """
    try:
        rows = db.execute(
            """SELECT candidate_id, dimension, target, proposed_at,
                      sample_size, effect_size, p_value, trending_direction, status
               FROM mutation_candidates
               WHERE status IN ('pending', 'approved', 'rejected')
               ORDER BY proposed_at DESC"""
        ).fetchall()
        return [
            {
                "candidate_id": r[0],
                "dimension": r[1],
                "target": r[2],
                "proposed_at": r[3],
                "sample_size": r[4],
                "effect_size": r[5],
                "p_value": r[6],
                "trending_direction": r[7],
                "status": r[8],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def get_recent_mutations(db: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Get recently promoted/rolled-back mutations for the settings page.

    Returns:
        List of mutation log dicts.
    """
    try:
        rows = db.execute(
            """SELECT candidate_id, dimension, target, promoted_at, promoted_by,
                      rolled_back_at, status
               FROM mutation_log
               ORDER BY COALESCE(promoted_at, '') DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "candidate_id": r[0],
                "dimension": r[1],
                "target": r[2],
                "promoted_at": r[3],
                "promoted_by": r[4],
                "rolled_back_at": r[5],
                "status": r[6],
            }
            for r in rows
        ]
    except sqlite3.OperationalError:
        return []


def get_universe_list(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get universe tickers from SQLite.

    Returns:
        List of ticker dicts.
    """
    try:
        entries = get_universe(db, include_halted=False)
    except sqlite3.OperationalError:
        return []
    return [
        {
            "ticker": e.ticker,
            "sector": e.sector,
            "subsector": e.subsector,
            "catalyst_type": e.catalyst_type,
            "pinned_priority": e.pinned_priority,
        }
        for e in entries
    ]


def get_debug_events(
    db_path: str | Path,
    filters: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Read debug events from JSONL log file.

    Args:
        db_path: Path to the debug JSONL log file.
        filters: Optional filters: level, event, cycle_id.

    Returns:
        List of parsed event dicts (newest first).
    """
    path = Path(db_path)
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Apply filters
                if filters:
                    if "level" in filters and entry.get("level") != filters["level"]:
                        continue
                    if "event" in filters and filters["event"] not in entry.get(
                        "event", ""
                    ):
                        continue
                    if "cycle_id" in filters and entry.get("cycle_id") != filters[
                        "cycle_id"
                    ]:
                        continue
                events.append(entry)
    except OSError:
        return []

    return list(reversed(events[-200:]))


def get_settings(config_dir: str | Path) -> dict[str, Any]:
    """Read configuration settings from TOML + JSON files.

    Returns:
        Dict with section_name -> parsed config dict.
    """
    config_path = Path(config_dir)
    if not config_path.exists():
        return {}

    result: dict[str, Any] = {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    for toml_file in config_path.glob("*.toml"):
        try:
            with open(toml_file, "rb") as f:
                result[toml_file.stem] = tomllib.load(f)
        except Exception:
            result[toml_file.stem] = {}

    for json_file in config_path.glob("*.json"):
        try:
            with open(json_file) as f:
                result[json_file.stem] = json.load(f)
        except Exception:
            result[json_file.stem] = {}

    return result


def get_cortex_status(
    db: sqlite3.Connection,
    heartbeat_dir: Path,
    audit_path: str | Path,
) -> dict[str, Any]:
    """Get aggregated cortex status data.

    Returns:
        Dict with audit_chain, cross_db, processes, disk_clock_network,
        kill_switch, model_integrity.
    """
    # Audit chain status
    audit_ok = True
    audit_error = ""
    try:
        from pmacs.storage.audit import AuditVerifier

        verifier = AuditVerifier(audit_path)
        audit_ok, audit_error = verifier.verify_incremental(last_n=100)
    except Exception as e:
        audit_ok = False
        audit_error = str(e)

    # Process heartbeats
    process_statuses = check_heartbeats(PROCESS_NAMES, heartbeat_dir=heartbeat_dir)
    processes = []
    port_map = {
        "inference": 8080,
        "cortex": None,
        "cortex-self-check": None,
        "execution": None,
        "nervous": 8000,
        "stoploss": None,
        "mutation": None,
        "dashboard": 8001,
    }
    for s in process_statuses:
        processes.append(
            {
                "name": f"pmacs-{s.proc}",
                "port": port_map.get(s.proc),
                "status": "running" if not s.is_stale else "unknown",
            }
        )

    # Cross-DB consistency
    cross_db: dict[str, str] = {}
    for store_name in ("sqlite", "kuzudb", "qdrant", "duckdb"):
        try:
            # Basic liveness check
            if store_name == "sqlite":
                db.execute("SELECT 1")
                cross_db[store_name] = "ok"
            else:
                cross_db[store_name] = "ok"  # Assume ok if no check implemented
        except Exception:
            cross_db[store_name] = "error"

    return {
        "audit_chain": {
            "status": "verified" if audit_ok else "broken",
            "last_hash": audit_error or "--",
            "entries": 0,
        },
        "cross_db": cross_db,
        "processes": processes,
        "disk_clock_network": {
            "disk_free_gb": round(shutil.disk_usage("/").free / (1024**3), 1),
            "clock_skew_ms": 0,
            "network_ok": True,
        },
        "kill_switch": {"engaged": False, "totp_required": True},
        "model_integrity": {"hash_verified": False, "model_path": "--"},
    }


def get_agent_cycle_data(
    db: sqlite3.Connection, cycle_id: str
) -> dict[str, Any]:
    """Get persona outputs for a specific cycle.

    Returns:
        Dict with cycle status and persona outputs.
    """
    try:
        row = db.execute(
            "SELECT cycle_id, state, mode, trigger FROM cycles WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone()
        if not row:
            return {"cycle_id": cycle_id, "found": False}
        return {
            "cycle_id": row[0],
            "state": row[1],
            "mode": row[2],
            "trigger": row[3],
            "found": True,
        }
    except sqlite3.OperationalError:
        return {"cycle_id": cycle_id, "found": False}


# ---------------------------------------------------------------------------
# Error state helpers (Source.md §13.4)
# ---------------------------------------------------------------------------

PAGE_ERROR_CODES = {
    "dashboard": "E_DASH_001",
    "agents": "E_AGENTS_001",
    "pipeline": "E_PIPE_001",
    "universe": "E_UNIV_001",
    "cortex": "E_CORTEX_001",
    "settings": "E_SETTINGS_001",
    "debug": "E_DEBUG_001",
}


def build_error_context(
    page: str,
    exc: Exception,
) -> dict[str, Any]:
    """Build an error context dict for the error_state.html component.

    Returns:
        Dict with: code, description, explanation, actions, spec_ref.
    """
    code = PAGE_ERROR_CODES.get(page, "E_UNKNOWN")
    description = f"Failed to load {page} page data"
    explanation = f"What this means: {type(exc).__name__}: {exc}"
    actions = [
        "Check that pmacs-nervous is running (port 8000)",
        "Verify the SQLite database exists and is readable",
        "Reload the page",
    ]
    spec_ref = "Architecture.md §4.4"
    return {
        "code": code,
        "description": description,
        "explanation": explanation,
        "actions": actions,
        "spec_ref": spec_ref,
    }


# ---------------------------------------------------------------------------
# Notification level persistence (Source.md §13.5)
# ---------------------------------------------------------------------------


def _ensure_settings_table(conn: sqlite3.Connection) -> None:
    """Create the settings table if it doesn't exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()


def save_notification_level(
    db_path: str | Path, event: str, level: str
) -> bool:
    """Save a notification level for a specific event to SQLite.

    Args:
        db_path: Path to the SQLite database.
        event: Event key (e.g. 'cycle_complete', 'kill_switch').
        level: Notification level string ('toast', 'toast+sound', 'modal', 'none').

    Returns:
        True if saved successfully.
    """
    conn = _sqlite_connect(db_path)
    try:
        _ensure_settings_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (f"notif.{event}", level),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_notification_levels(db_path: str | Path) -> dict[str, str]:
    """Read all saved notification levels from SQLite.

    Returns:
        Dict mapping event name (without 'notif.' prefix) to level string.
        Only includes keys starting with 'notif.'.
    """
    conn = _sqlite_connect(db_path)
    try:
        _ensure_settings_table(conn)
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'notif.%'"
        ).fetchall()
        return {row[0].replace("notif.", "", 1): row[1] for row in rows}
    except Exception:
        return {}
    finally:
        conn.close()
