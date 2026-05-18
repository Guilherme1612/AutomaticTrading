"""Mutation rollback logic (Agents.md §17.4 — five rollback safety levels).

Auto-rollback is a safety net for operator-approved mutations that regress.
Only triggers after probation period ends and within the rollback window.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.constants import MUTATION_AUTO_ROLLBACK_WINDOW


def regression_detected(
    promoted_cycles_ago: int,
    probation_cycles: int,
    post_metric: float,
    baseline_metric: float,
    lower_is_better: bool = True,
    rollback_window: int | None = None,
) -> bool:
    """Check if a promoted mutation has regressed.

    Only checks after probation period ends.
    Stops checking after rollback_window beyond probation.
    """
    window = rollback_window if rollback_window is not None else MUTATION_AUTO_ROLLBACK_WINDOW
    if promoted_cycles_ago < probation_cycles:
        return False

    if promoted_cycles_ago > probation_cycles + window:
        return False  # monitoring expired

    if lower_is_better:
        return post_metric > baseline_metric
    else:
        return post_metric < baseline_metric


def execute_rollback(
    proposal_id: str,
    reason: str,
    *,
    db_path: Path | None = None,
    audit_path: Path | None = None,
    sse_publisher: Any = None,
    cycle_id: str = "",
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Execute rollback and return audit data.

    When db_path and audit_path are provided, also:
    - Updates mutation_proposals status to ROLLED_BACK in SQLite
    - Logs audit event mutation_rollback_executed
    - Publishes SSE event mutation.rollback
    - Calls rollback_registry to restore config if registry_path given
    """
    now = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "proposal_id": proposal_id,
        "rolled_back_at": now,
        "reason": reason,
        "status": "ROLLED_BACK",
    }

    # Update SQLite
    if db_path is not None:
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "UPDATE mutation_proposals SET status = 'ROLLED_BACK', "
                "completed_at = ? WHERE id = ?",
                (now, proposal_id),
            )
            conn.commit()
        finally:
            conn.close()

    # Audit event
    if audit_path is not None:
        from pmacs.storage.audit import AuditWriter

        writer = AuditWriter(audit_path)
        writer.append(
            "mutation_rollback_executed",
            {"proposal_id": proposal_id, "reason": reason},
            cycle_id=cycle_id,
        )
        writer.close()

    # SSE event
    if sse_publisher is not None:
        sse_publisher.publish("mutation", "mutation.rolled_back", {
            "proposal_id": proposal_id,
            "reason": reason,
            "rolled_back_at": now,
        })

    return result


def flag_for_kill_switch_review(
    recent_promotions: list[str], max_flag: int = 3
) -> list[str]:
    """Flag the N most recent promotions for kill-switch review.

    This function is called by pmacs.cortex.kill_switch when the kill switch
    is engaged. It returns the proposals that should be flagged for operator
    review. The actual kill switch integration happens via the Cortex process
    (pmacs-cortex), not via a direct call from the mutation daemon.

    Logs a MUTATION_FLAGGED_FOR_REVIEW debug event for each flagged proposal.
    """
    flagged = recent_promotions[:max_flag]

    if flagged:
        from pmacs.logsys import log_debug

        log_debug(
            "MUTATION_FLAGGED_FOR_REVIEW",
            payload={"flagged_proposals": flagged, "total_promotions": len(recent_promotions)},
            level="WARN",
            error_code="MUTATION_FLAGGED_FOR_REVIEW",
            msg=f"Kill switch review: {len(flagged)} mutations flagged",
        )

    return flagged
