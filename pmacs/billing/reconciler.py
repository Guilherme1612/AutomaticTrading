"""Reconciler — post-flight reconciliation against OpenRouter.

PRD §11: Fetch authoritative cost from OpenRouter /generation endpoint,
update actual_cost_usd, adjust budget_state if delta is significant.
Runs in background thread to avoid blocking the cycle.
"""

from __future__ import annotations

import threading
import time

import httpx

from pmacs.billing.usage_logger import update_actual_cost, update_budget_state
from pmacs.logsys import log_debug


_OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Drift thresholds (PRD §11.4)
_SILENT_THRESHOLD = 0.001      # < $0.001: silent
_DEBUG_THRESHOLD = 0.10        # $0.001 - $0.10: update + log debug
_WARN_THRESHOLD = 1.00         # $0.10 - $1.00: update + log warning


def spawn_reconcile_call(
    call_id: str,
    generation_id: str,
    sqlite_conn_path: str,
    duckdb_path: str,
) -> None:
    """Spawn reconciliation in a background thread (non-blocking).

    Args:
        call_id: API usage call ID.
        generation_id: OpenRouter generation ID.
        sqlite_conn_path: Path to SQLite DB (thread needs its own connection).
        duckdb_path: Path to DuckDB analytics file.
    """
    if not generation_id:
        return  # Local calls have no generation_id

    def _worker():
        import sqlite3
        from pmacs.storage.duckdb import DuckDBAdapter
        from pmacs.storage.sqlite import get_connection

        try:
            sqlite_conn = get_connection(sqlite_conn_path)
            duckdb_adapter = DuckDBAdapter(duckdb_path)
            _reconcile_call_impl(call_id, generation_id, sqlite_conn, duckdb_adapter)
        except Exception as exc:
            log_debug(
                "RECONCILIATION_FAILED",
                payload={"call_id": call_id, "error": str(exc)},
                level="WARN",
                error_code="RECONCILIATION_FAILED",
                msg=f"Reconciliation thread failed for {call_id}: {exc}",
            )
        finally:
            try:
                sqlite_conn.close()
            except Exception:
                pass
            try:
                duckdb_adapter.close()
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True, name=f"reconcile-{call_id[:8]}")
    t.start()


def _reconcile_call_impl(
    call_id: str,
    generation_id: str,
    sqlite_conn,
    duckdb_adapter,
) -> bool:
    """Internal: reconcile a single call (called from background thread)."""
    cost = _fetch_authoritative_cost(generation_id)
    if cost is None:
        return False

    rows = duckdb_adapter.execute(
        "SELECT body_cost_usd FROM api_usage WHERE call_id = ?",
        [call_id],
    )
    if not rows:
        return False

    body_cost = rows[0].get("body_cost_usd", 0.0)
    delta = cost - body_cost

    update_actual_cost(sqlite_conn, duckdb_adapter, call_id, cost)
    _log_drift(call_id, body_cost, cost, delta)
    return True


def reconcile_cycle(
    cycle_id: str,
    sqlite_conn,
    duckdb_adapter,
) -> int:
    """Force-reconcile all unreconciled calls in a cycle.

    Returns count of successfully reconciled calls.
    """
    rows = duckdb_adapter.execute(
        "SELECT call_id, generation_id FROM api_usage "
        "WHERE cycle_id = ? AND actual_cost_usd IS NULL AND generation_id IS NOT NULL",
        [cycle_id],
    )

    reconciled = 0
    for row in rows:
        call_id = row["call_id"]
        gen_id = row["generation_id"]
        if _reconcile_call_impl(call_id, gen_id, sqlite_conn, duckdb_adapter):
            reconciled += 1

    return reconciled


def reconcile_daily(
    sqlite_conn,
    duckdb_adapter,
) -> int:
    """Daily sweep: reconcile any calls still missing actual_cost_usd.

    Returns count of reconciled calls.
    """
    rows = duckdb_adapter.execute(
        "SELECT call_id, generation_id FROM api_usage "
        "WHERE actual_cost_usd IS NULL AND generation_id IS NOT NULL "
        "AND called_at < current_timestamp - INTERVAL '5 minutes'"
    )

    reconciled = 0
    for row in rows:
        if _reconcile_call_impl(row["call_id"], row["generation_id"], sqlite_conn, duckdb_adapter):
            reconciled += 1

    return reconciled


def _fetch_authoritative_cost(generation_id: str) -> float | None:
    """Fetch authoritative cost from OpenRouter with retry.

    Returns cost in USD or None if all retries fail.
    Runs in background thread — time.sleep is acceptable here.
    """
    backoff = [2, 30, 300]  # PRD §11: 2s initial, then 30s, 5min

    for delay in backoff:
        time.sleep(delay)
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    f"{_OPENROUTER_BASE}/generation",
                    params={"id": generation_id},
                )
                if resp.status_code == 404:
                    continue  # Not indexed yet, retry
                resp.raise_for_status()
                data = resp.json()
                return float(data.get("total_cost", 0))
        except (httpx.HTTPError, Exception):
            continue

    log_debug(
        "RECONCILIATION_FAILED",
        payload={"generation_id": generation_id},
        level="WARN",
        error_code="RECONCILIATION_FAILED",
        msg=f"Reconciliation failed for generation {generation_id} after 3 retries",
    )
    return None


def _log_drift(call_id: str, body_cost: float, actual_cost: float, delta: float) -> None:
    """Log reconciliation drift based on size thresholds."""
    abs_delta = abs(delta)
    if abs_delta < _SILENT_THRESHOLD:
        return  # Sub-cent rounding, silent

    if abs_delta <= _DEBUG_THRESHOLD:
        log_debug(
            "RECONCILIATION_DELTA",
            payload={"call_id": call_id, "body": body_cost, "actual": actual_cost, "delta": delta},
            level="DEBUG",
        )
    elif abs_delta <= _WARN_THRESHOLD:
        log_debug(
            "RECONCILIATION_LARGE_DELTA",
            payload={"call_id": call_id, "body": body_cost, "actual": actual_cost, "delta": delta},
            level="WARN",
            error_code="RECONCILIATION_FAILED",
            msg=f"Large reconciliation delta: ${delta:.4f} for call {call_id}",
        )
    else:
        log_debug(
            "RECONCILIATION_HUGE_DELTA",
            payload={"call_id": call_id, "body": body_cost, "actual": actual_cost, "delta": delta},
            level="WARN",
            error_code="RECONCILIATION_FAILED",
            msg=f"Huge reconciliation delta: ${delta:.4f} for call {call_id} — investigate",
        )
