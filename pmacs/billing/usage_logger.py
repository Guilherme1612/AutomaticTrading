"""Usage logger — persist API call costs to DuckDB + SQLite budget state.

PRD §7.2, §9.4: Writes api_usage to DuckDB, updates budget_state in SQLite.
Handles DuckDB stub mode gracefully.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pmacs.logsys import log_debug
from pmacs.nervous.sse_publisher import publish_system_event
from pmacs.schemas.billing import BodyCost, EstimatedCost


def log_usage(
    sqlite_conn,
    duckdb_adapter,
    call_record: BodyCost,
    estimated: EstimatedCost,
) -> None:
    """Log an LLM call's cost data to DuckDB and update budget state.

    DuckDB write follows stub-safe pattern: if DuckDB unavailable, logs stub.
    SQLite budget_state update always succeeds (SQLite is always available).
    """
    # DuckDB write (stub-safe)
    duckdb_adapter.insert_api_usage(
        call_id=call_record.call_id,
        cycle_id=call_record.cycle_id,
        persona=call_record.persona,
        model_id=call_record.model_id,
        generation_id=call_record.generation_id,
        prompt_tokens=call_record.prompt_tokens,
        completion_tokens=call_record.completion_tokens,
        estimated_cost_usd=estimated.estimated_cost_usd,
        body_cost_usd=call_record.body_cost_usd,
        latency_ms=call_record.latency_ms,
        succeeded=True,
    )

    # SQLite budget state update
    update_budget_state(sqlite_conn, call_record.body_cost_usd)

    # SSE: cost.call_completed
    publish_system_event("cost.call_completed", {
        "call_id": call_record.call_id,
        "cycle_id": call_record.cycle_id,
        "persona": call_record.persona,
        "body_cost_usd": round(call_record.body_cost_usd, 6),
        "prompt_tokens": call_record.prompt_tokens,
        "completion_tokens": call_record.completion_tokens,
        "latency_ms": call_record.latency_ms,
    })


def update_budget_state(sqlite_conn, cost_usd: float) -> None:
    """Atomically add cost to today and this_month budget totals.

    Uses explicit transaction to ensure atomicity.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        sqlite_conn.execute(
            "UPDATE budget_state SET total_cost_usd = total_cost_usd + ?, updated_at = ? "
            "WHERE period = 'today'",
            [cost_usd, now],
        )
        sqlite_conn.execute(
            "UPDATE budget_state SET total_cost_usd = total_cost_usd + ?, updated_at = ? "
            "WHERE period = 'this_month'",
            [cost_usd, now],
        )
        sqlite_conn.commit()

        # SSE: cost.budget_update
        totals = get_budget_totals(sqlite_conn)
        publish_system_event("cost.budget_update", {
            "today": totals.get("today", {}),
            "this_month": totals.get("this_month", {}),
        })
    except Exception as exc:
        try:
            sqlite_conn.rollback()
        except Exception:
            pass
        log_debug(
            "BUDGET_STATE_UPDATE_FAILED",
            payload={"cost_usd": cost_usd, "error": str(exc)},
            level="WARN",
            error_code="BUDGET_STATE_UPDATE_FAILED",
            msg=f"Failed to update budget state: {exc}",
        )


def update_actual_cost(
    sqlite_conn,
    duckdb_adapter,
    call_id: str,
    actual_cost_usd: float,
) -> None:
    """Update actual cost after reconciliation and adjust budget state by delta.

    1. Get current body_cost from DuckDB
    2. Update actual_cost_usd in DuckDB
    3. Adjust budget_state by (actual - body) delta
    """
    # Get current body cost to compute delta
    rows = duckdb_adapter.execute(
        "SELECT body_cost_usd FROM api_usage WHERE call_id = ?",
        [call_id],
    )
    if not rows:
        log_debug(
            "RECONCILIATION_CALL_NOT_FOUND",
            payload={"call_id": call_id},
            level="WARN",
            error_code="RECONCILIATION_FAILED",
            msg=f"Cannot reconcile: call_id {call_id} not found",
        )
        return

    body_cost = rows[0].get("body_cost_usd", 0.0)
    delta = actual_cost_usd - body_cost

    # Update DuckDB
    duckdb_adapter.update_actual_cost(call_id, actual_cost_usd)

    # Adjust budget state if delta is significant
    if abs(delta) > 0.000001:  # sub-cent threshold
        update_budget_state(sqlite_conn, delta)

    log_debug(
        "RECONCILIATION_DELTA",
        payload={
            "call_id": call_id,
            "body_cost": body_cost,
            "actual_cost": actual_cost_usd,
            "delta": delta,
        },
        level="DEBUG",
    )


def get_budget_totals(sqlite_conn) -> dict:
    """Get current budget state for both periods."""
    rows = sqlite_conn.execute(
        "SELECT period, total_cost_usd, cap_usd FROM budget_state"
    ).fetchall()
    result = {}
    for row in rows:
        result[row[0]] = {"total_cost_usd": row[1], "cap_usd": row[2]}
    return result


def compute_persona_retry_rate(persona: str, duckdb_adapter) -> float:
    """Compute retry rate for a persona over recent calls.

    Returns fraction of calls that had retry_count > 0.
    """
    rows = duckdb_adapter.execute(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END) as retried "
        "FROM api_usage WHERE persona = ? AND called_at > current_timestamp - INTERVAL '7 days'",
        [persona],
    )
    if not rows or rows[0].get("total", 0) == 0:
        return 0.0
    return rows[0].get("retried", 0) / rows[0]["total"]


def check_quality_regression(persona: str, duckdb_adapter) -> None:
    """Check if persona shows quality regression (high retry + Brier delta).

    PRD §12.4: if retry_rate > 10% AND Brier delta > 0.05,
    emit PERSONA_QUALITY_REGRESSION alert.
    """
    retry_rate = compute_persona_retry_rate(persona, duckdb_adapter)
    if retry_rate <= 0.10:
        return

    # Check Brier delta (simplified: check if persona has degraded scores)
    rows = duckdb_adapter.execute(
        "SELECT AVG(brier) as avg_brier FROM persona_performance "
        "WHERE persona = ? AND computed_at > current_timestamp - INTERVAL '7 days'",
        [persona],
    )
    if not rows or rows[0].get("avg_brier") is None:
        return

    # For now, just log the high retry rate — Brier comparison would need
    # baseline data from before retries started
    log_debug(
        "PERSONA_QUALITY_REGRESSION",
        payload={
            "persona": persona,
            "retry_rate": round(retry_rate, 4),
        },
        level="WARN",
        error_code="PERSONA_QUALITY_REGRESSION",
        msg=f"Persona {persona} has high retry rate: {retry_rate:.1%}",
    )
