"""Kill switch state machine — ARMED / ENGAGED (Architecture.md §13).

The kill switch is the primary safety mechanism. Any trigger can ENGAGE
without TOTP (safer to over-trigger). Only the operator can DISENGAGE
via valid TOTP code.

State persisted in SQLite `kill_switch` singleton table.
"""
from __future__ import annotations

import enum
import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.cortex.totp import verify_totp
from pmacs.logsys import log_debug
from pmacs.nervous.sse_publisher import publish_system_event


def _resolve_db(db_path: Path | str | None) -> Path:
    if db_path is None:
        from pmacs.config import data_dir
        return data_dir() / "pmacs.db"
    return Path(db_path)


class KillSwitchState(str, enum.Enum):
    """Kill switch states per Architecture.md §13."""

    ARMED = "ARMED"
    ENGAGED = "ENGAGED"


@dataclass(frozen=True)
class TriggerResult:
    """Result of evaluating a single kill switch trigger."""

    trigger_id: str
    triggered: bool
    reason: str
    details: dict[str, Any] | None = None


_KILL_SWITCH_DDL = """
CREATE TABLE IF NOT EXISTS kill_switch (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL DEFAULT 'ARMED',
    reason TEXT,
    trigger_name TEXT,
    engaged_at TEXT,
    disengaged_at TEXT,
    updated_at TEXT NOT NULL
);
"""

# The triggers from Architecture.md §13.1 + Phase 16 budget triggers (12 total)
TRIGGER_IDS: tuple[str, ...] = (
    "AUDIT_CHAIN_INTEGRITY",
    "ROLLING_5D_LOSS",
    "SINGLE_DAY_MTM_LOSS",
    "RECONCILIATION_MISMATCH",
    "BROKER_AUTH_FAILURE",
    "DISK_SPACE_LOW",
    "NTP_DRIFT",
    "META_MONITOR_UNRESPONSIVE",
    "CRASH_LOOP",
    "MODEL_INTEGRITY",
    "CYCLE_BLOCKED_BUDGET_DAILY",
    "CYCLE_BLOCKED_BUDGET_MONTHLY",
    "MANUAL",
    "CATASTROPHE_CANCEL_FAILED",
)


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create kill_switch table if not exists and ensure singleton row."""
    conn.executescript(_KILL_SWITCH_DDL)
    row = conn.execute("SELECT COUNT(*) FROM kill_switch WHERE id = 1").fetchone()
    if row[0] == 0:
        conn.execute(
            "INSERT INTO kill_switch (id, state, updated_at) VALUES (1, 'ARMED', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()


def _get_db(db_path: str | Path) -> sqlite3.Connection:
    """Open SQLite connection and ensure kill_switch table exists."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = _sql_connect(p)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_table(conn)
    return conn


def engage(
    reason: str,
    trigger: str,
    db_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    cycle_id: str = "",
) -> None:
    """Engage the kill switch. Does NOT require TOTP.

    Any trigger can engage — it is safer to over-trigger than under-trigger.
    Sets state to ENGAGED, logs to audit and debug.

    Args:
        reason: Human-readable reason for engagement.
        trigger: Trigger identifier (one of TRIGGER_IDS).
        db_path: Path to SQLite database.
        audit_path: Optional path to audit log file.
        cycle_id: Optional cycle ID for audit traceability.
    """
    db_path = _resolve_db(db_path)
    conn = _get_db(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        current = conn.execute("SELECT state FROM kill_switch WHERE id = 1").fetchone()
        if current and current[0] == KillSwitchState.ENGAGED.value:
            log_debug(
                "KILL_SWITCH_ENGAGE_ALREADY_ENGAGED",
                payload={"trigger": trigger, "reason": reason},
                level="INFO",
                cycle_id=cycle_id or None,
                msg=f"Kill switch already ENGAGED, ignoring trigger={trigger}",
            )
            return

        conn.execute(
            """UPDATE kill_switch
               SET state = ?, reason = ?, trigger_name = ?, engaged_at = ?, updated_at = ?
               WHERE id = 1""",
            (KillSwitchState.ENGAGED.value, reason, trigger, now, now),
        )
        conn.commit()

        log_debug(
            "KILL_SWITCH_ENGAGED",
            payload={"trigger": trigger, "reason": reason, "engaged_at": now},
            level="WARN",
            error_code="KILL_SWITCH_ENGAGED",
            cycle_id=cycle_id or None,
            msg=f"KILL SWITCH ENGAGED: trigger={trigger}, reason={reason}",
        )

        # Publish system SSE event (no-op if nervous not running)
        publish_system_event("system.kill_switch_engaged", {
            "trigger": trigger, "reason": reason, "engaged_at": now,
        })

        # Write to audit log if path provided
        if audit_path is not None:
            try:
                from pmacs.storage.audit import AuditWriter

                writer = AuditWriter(audit_path)
                writer.append(
                    "KILL_SWITCH_ENGAGED",
                    {"trigger": trigger, "reason": reason, "engaged_at": now},
                    cycle_id=cycle_id,
                )
                writer.close()
            except Exception as audit_exc:
                # Never block kill switch engagement, but log the failure
                log_debug(
                    "KILL_SWITCH_AUDIT_WRITE_FAILED",
                    payload={"trigger": trigger, "audit_error": str(audit_exc)},
                    level="ERROR",
                    error_code="AUDIT_REPLICATION_FAILED",
                    msg=f"Kill switch engaged but audit write failed: {audit_exc}",
                )

        # Flag recent promoted mutations for operator review (Agents.md §17.4 Level 5)
        try:
            from pmacs.mutation.rollback import flag_for_kill_switch_review

            recent = conn.execute(
                "SELECT id FROM mutation_proposals "
                "WHERE status = 'OPERATOR_PROMOTED' "
                "ORDER BY proposed_at DESC LIMIT 3"
            ).fetchall()
            proposal_ids = [row[0] for row in recent]
            flagged = flag_for_kill_switch_review(proposal_ids, max_flag=3)
            if flagged:
                log_debug(
                    "KILL_SWITCH_MUTATION_REVIEW",
                    payload={"flagged_proposals": flagged, "trigger": trigger},
                    level="WARN",
                    error_code="KILL_SWITCH_MUTATION_REVIEW",
                    cycle_id=cycle_id or None,
                    msg=f"Kill switch: {len(flagged)} mutations flagged for review",
                )
        except Exception:
            pass  # Never block kill switch engagement
    finally:
        conn.close()


def disengage(
    totp_secret: str,
    totp_code: str,
    reason: str,
    db_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    cycle_id: str = "",
) -> bool:
    """Disengage the kill switch. Requires valid TOTP.

    Only the operator can disengage. Verifies TOTP code before clearing.

    Args:
        totp_secret: The TOTP secret (base32) for verification.
        totp_code: The 6-digit TOTP code provided by operator.
        reason: Human-readable reason for disengagement.
        db_path: Path to SQLite database.
        audit_path: Optional path to audit log file.
        cycle_id: Optional cycle ID for audit traceability.

    Returns:
        True if disengaged successfully, False if TOTP invalid.

    Raises:
        ValueError: If kill switch is not currently ENGAGED.
    """
    db_path = _resolve_db(db_path)
    if not verify_totp(totp_secret, totp_code):
        log_debug(
            "KILL_SWITCH_DISENGAGE_TOTP_FAILED",
            payload={"reason": reason},
            level="WARN",
            error_code="KILL_SWITCH_ENGAGED",
            cycle_id=cycle_id or None,
            msg="Kill switch disengage FAILED: invalid TOTP code",
        )
        return False

    conn = _get_db(db_path)
    try:
        current = conn.execute("SELECT state FROM kill_switch WHERE id = 1").fetchone()
        if not current or current[0] != KillSwitchState.ENGAGED.value:
            raise ValueError("Kill switch is not ENGAGED — cannot disengage")

        # Architecture.md §13.2: Cortex confirms underlying condition resolved
        trigger_name = conn.execute(
            "SELECT trigger_name FROM kill_switch WHERE id = 1"
        ).fetchone()
        if trigger_name and trigger_name[0]:
            # Re-run the specific trigger check to verify condition cleared
            try:
                trigger_checks = check_all_triggers(db_path=db_path)
                still_triggered = [r for r in trigger_checks
                                   if r.triggered and r.trigger_id == trigger_name[0]]
                if still_triggered:
                    reasons = "; ".join(r.reason for r in still_triggered)
                    log_debug(
                        "KILL_SWITCH_DISENGAGE_CONDITION_UNRESOLVED",
                        payload={"trigger": trigger_name[0], "reasons": reasons},
                        level="WARN",
                        error_code="KILL_SWITCH_ENGAGED",
                        cycle_id=cycle_id or None,
                        msg=f"Kill switch disengaged but condition {trigger_name[0]} may still be active: {reasons}",
                    )
                    # Operator TOTP takes precedence — log warning but allow disengage
            except Exception:
                # If re-check fails, allow disengage (operator explicitly overriding)
                pass

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE kill_switch
               SET state = ?, reason = ?, trigger_name = NULL, disengaged_at = ?, updated_at = ?
               WHERE id = 1""",
            (KillSwitchState.ARMED.value, reason, now, now),
        )
        conn.commit()

        log_debug(
            "KILL_SWITCH_DISENGAGED",
            payload={"reason": reason, "disengaged_at": now},
            level="INFO",
            cycle_id=cycle_id or None,
            msg=f"Kill switch DISENGAGED: reason={reason}",
        )

        # Publish system SSE event (no-op if nervous not running)
        publish_system_event("system.kill_switch_disengaged", {
            "reason": reason, "disengaged_at": now,
        })

        if audit_path is not None:
            try:
                from pmacs.storage.audit import AuditWriter

                writer = AuditWriter(audit_path)
                writer.append(
                    "KILL_SWITCH_DISENGAGED",
                    {"reason": reason, "disengaged_at": now},
                    cycle_id=cycle_id,
                )
                writer.close()
            except Exception as audit_exc:
                log_debug(
                    "KILL_SWITCH_AUDIT_WRITE_FAILED",
                    payload={"reason": reason, "audit_error": str(audit_exc)},
                    level="ERROR",
                    error_code="AUDIT_REPLICATION_FAILED",
                    msg=f"Kill switch disengaged but audit write failed: {audit_exc}",
                )

        return True
    finally:
        conn.close()


def is_engaged(db_path: str | Path | None = None) -> bool:
    """Check if kill switch is currently engaged.

    Args:
        db_path: Path to SQLite database.

    Returns:
        True if ENGAGED, False if ARMED or table missing.
    """
    db_path = _resolve_db(db_path)
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT state FROM kill_switch WHERE id = 1").fetchone()
        return row is not None and row[0] == KillSwitchState.ENGAGED.value
    finally:
        conn.close()


def get_state(db_path: str | Path | None = None) -> KillSwitchState:
    """Get current kill switch state.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Current KillSwitchState enum value.
    """
    db_path = _resolve_db(db_path)
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT state FROM kill_switch WHERE id = 1").fetchone()
        if row is None:
            return KillSwitchState.ARMED
        return KillSwitchState(row[0])
    finally:
        conn.close()


def get_engagement_info(
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Get engagement details (reason, trigger, time) if currently engaged.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dict with engagement info, or None if not engaged.
    """
    db_path = _resolve_db(db_path)
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT state, reason, trigger_name, engaged_at, updated_at FROM kill_switch WHERE id = 1"
        ).fetchone()
        if row is None or row[0] != KillSwitchState.ENGAGED.value:
            return None
        return {
            "state": row[0],
            "reason": row[1],
            "trigger_name": row[2],
            "engaged_at": row[3],
            "updated_at": row[4],
        }
    finally:
        conn.close()


def check_all_triggers(
    db_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    heartbeat_dir: Path | None = None,
    gguf_path: str | Path | None = None,
    expected_gguf_hash: str | None = None,
) -> list[TriggerResult]:
    """Evaluate all 12 kill switch triggers.

    Args:
        db_path: Path to SQLite database.
        audit_path: Path to audit log file.
        heartbeat_dir: Directory containing heartbeat files.
        gguf_path: Path to GGUF model file.
        expected_gguf_hash: Expected SHA256 hash of GGUF file.

    Returns:
        List of TriggerResult for each trigger evaluation.
    """
    db_path = _resolve_db(db_path)
    results: list[TriggerResult] = []

    # 1. Audit chain integrity
    if audit_path is not None:
        from pmacs.storage.audit import AuditVerifier

        verifier = AuditVerifier(audit_path)
        ok, error = verifier.verify_full()
        results.append(
            TriggerResult(
                trigger_id="AUDIT_CHAIN_INTEGRITY",
                triggered=not ok,
                reason=error or "Audit chain verified",
            )
        )
    else:
        results.append(
            TriggerResult(
                trigger_id="AUDIT_CHAIN_INTEGRITY",
                triggered=False,
                reason="No audit path configured",
            )
        )

    # 2. Rolling 5-day loss >10%
    # Requires paper_account table data — check if available
    results.append(
        _check_rolling_loss(db_path)
    )

    # 3. Single-day MtM loss >5%
    results.append(
        _check_daily_mtm_loss(db_path)
    )

    # 4. Reconciliation mismatch
    # Requires broker connection — skip in paper mode
    results.append(
        TriggerResult(
            trigger_id="RECONCILIATION_MISMATCH",
            triggered=False,
            reason="Not applicable in PAPER mode",
        )
    )

    # 5. Broker auth failure
    # Requires broker connection — skip in paper mode
    results.append(
        TriggerResult(
            trigger_id="BROKER_AUTH_FAILURE",
            triggered=False,
            reason="Not applicable in PAPER mode",
        )
    )

    # 6. Disk <2GB
    results.append(
        _check_disk_space()
    )

    # 7. NTP drift >60s
    results.append(
        _check_ntp_drift()
    )

    # 8. Meta-monitor >120s unresponsive
    if heartbeat_dir is not None:
        results.append(
            _check_meta_monitor(heartbeat_dir)
        )
    else:
        results.append(
            TriggerResult(
                trigger_id="META_MONITOR_UNRESPONSIVE",
                triggered=False,
                reason="No heartbeat dir configured",
            )
        )

    # 9. Crash loop detected
    results.append(
        _check_crash_loop(db_path)
    )

    # 10. Model integrity check failed
    results.append(
        _check_model_integrity(gguf_path, expected_gguf_hash)
    )

    # 11. Daily budget hard cap exceeded
    results.append(
        _check_budget_daily(db_path)
    )

    # 12. Monthly budget hard cap exceeded
    results.append(
        _check_budget_monthly(db_path)
    )

    # 13. Manual trigger — operator-initiated only, never auto-evaluated
    results.append(
        TriggerResult(
            trigger_id="MANUAL",
            triggered=False,
            reason="Manual trigger is operator-initiated only",
        )
    )

    # 14. Catastrophe cancel failed — runtime-only, checked during execution
    results.append(
        TriggerResult(
            trigger_id="CATASTROPHE_CANCEL_FAILED",
            triggered=False,
            reason="Checked during trade execution",
        )
    )

    return results


def _check_rolling_loss(db_path: str | Path) -> TriggerResult:
    """Check rolling 5-day loss >10% (Architecture.md §13)."""
    from pmacs.constants import KILL_SWITCH_ROLLING_5D_LOSS_PCT

    try:
        conn = _sql_connect(db_path)
        try:
            # Get current value
            current_row = conn.execute(
                """SELECT total_value_usd FROM paper_account
                   ORDER BY snapshot_at DESC LIMIT 1"""
            ).fetchone()
            if current_row is None:
                return TriggerResult(
                    trigger_id="ROLLING_5D_LOSS",
                    triggered=False,
                    reason="No paper account data yet",
                )
            current_value = float(current_row[0])

            # Get value from 5 days ago
            five_day_row = conn.execute(
                """SELECT total_value_usd FROM paper_account
                   WHERE snapshot_at <= datetime('now', '-5 days')
                   ORDER BY snapshot_at DESC LIMIT 1"""
            ).fetchone()

            if five_day_row is None:
                return TriggerResult(
                    trigger_id="ROLLING_5D_LOSS",
                    triggered=False,
                    reason="Insufficient history for 5-day comparison",
                    details={"current_value": current_value},
                )

            prior_value = float(five_day_row[0])
            if prior_value <= 0:
                return TriggerResult(
                    trigger_id="ROLLING_5D_LOSS",
                    triggered=False,
                    reason="Prior value zero or negative",
                    details={"current_value": current_value, "prior_value": prior_value},
                )

            loss_pct = (prior_value - current_value) / prior_value
            triggered = loss_pct > KILL_SWITCH_ROLLING_5D_LOSS_PCT

            return TriggerResult(
                trigger_id="ROLLING_5D_LOSS",
                triggered=triggered,
                reason=f"5-day loss {loss_pct:.1%} {'EXCEEDS' if triggered else 'within'} {KILL_SWITCH_ROLLING_5D_LOSS_PCT:.0%} threshold",
                details={
                    "current_value": current_value,
                    "prior_value": prior_value,
                    "loss_pct": round(loss_pct, 4),
                    "threshold": KILL_SWITCH_ROLLING_5D_LOSS_PCT,
                },
            )
        finally:
            conn.close()
    except Exception as exc:
        return TriggerResult(
            trigger_id="ROLLING_5D_LOSS",
            triggered=False,
            reason=f"Check failed: {exc}",
        )


def _check_daily_mtm_loss(db_path: str | Path) -> TriggerResult:
    """Check single-day MtM loss >5%."""
    from pmacs.constants import KILL_SWITCH_DAILY_LOSS_PCT

    try:
        conn = _sql_connect(db_path)
        try:
            row = conn.execute(
                """SELECT total_value_usd FROM paper_account
                   ORDER BY snapshot_at DESC LIMIT 2"""
            ).fetchall()
            if len(row) < 2:
                return TriggerResult(
                    trigger_id="SINGLE_DAY_MTM_LOSS",
                    triggered=False,
                    reason="Insufficient data for daily comparison",
                )
            latest = row[0][0]
            previous = row[1][0]
            if previous > 0:
                loss_pct = (previous - latest) / previous
                return TriggerResult(
                    trigger_id="SINGLE_DAY_MTM_LOSS",
                    triggered=loss_pct > KILL_SWITCH_DAILY_LOSS_PCT,
                    reason=f"Daily change: {loss_pct:.4f}",
                    details={"loss_pct": loss_pct},
                )
            return TriggerResult(
                trigger_id="SINGLE_DAY_MTM_LOSS",
                triggered=False,
                reason="Previous value zero",
            )
        finally:
            conn.close()
    except Exception as exc:
        return TriggerResult(
            trigger_id="SINGLE_DAY_MTM_LOSS",
            triggered=False,
            reason=f"Check failed: {exc}",
        )


def _check_disk_space() -> TriggerResult:
    """Check disk free space < 2GB."""
    import shutil

    try:
        from pmacs.cortex.disk_monitor import check_disk_space

        triggered, free_gb = check_disk_space()
        return TriggerResult(
            trigger_id="DISK_SPACE_LOW",
            triggered=triggered,
            reason=f"Free space: {free_gb:.2f}GB",
            details={"free_gb": free_gb},
        )
    except Exception as exc:
        return TriggerResult(
            trigger_id="DISK_SPACE_LOW",
            triggered=False,
            reason=f"Check failed: {exc}",
        )


def _check_ntp_drift() -> TriggerResult:
    """Check NTP drift >60s."""
    try:
        from pmacs.cortex.clock_monitor import check_ntp_drift

        triggered, drift_s = check_ntp_drift()
        return TriggerResult(
            trigger_id="NTP_DRIFT",
            triggered=triggered,
            reason=f"Drift: {drift_s:.1f}s" if drift_s is not None else "NTP check skipped",
            details={"drift_s": drift_s},
        )
    except Exception as exc:
        return TriggerResult(
            trigger_id="NTP_DRIFT",
            triggered=False,
            reason=f"Check failed: {exc}",
        )


def _check_meta_monitor(heartbeat_dir: Path) -> TriggerResult:
    """Check if cortex-self-check heartbeat is >120s stale."""
    from pmacs.cortex.health import check_heartbeats

    statuses = check_heartbeats(["cortex-self-check"], heartbeat_dir=heartbeat_dir, stale_threshold=120.0)
    if statuses and statuses[0].is_stale:
        return TriggerResult(
            trigger_id="META_MONITOR_UNRESPONSIVE",
            triggered=True,
            reason="cortex-self-check heartbeat stale >120s",
        )
    return TriggerResult(
        trigger_id="META_MONITOR_UNRESPONSIVE",
        triggered=False,
        reason="cortex-self-check responsive",
    )


def _check_crash_loop(db_path: str | Path) -> TriggerResult:
    """Check if any process is in crash loop."""
    try:
        from pmacs.cortex.crash_loop_detector import check_any_crash_loop

        crashed_proc = check_any_crash_loop(db_path)
        if crashed_proc:
            return TriggerResult(
                trigger_id="CRASH_LOOP",
                triggered=True,
                reason=f"Process {crashed_proc} in crash loop",
                details={"proc": crashed_proc},
            )
        return TriggerResult(
            trigger_id="CRASH_LOOP",
            triggered=False,
            reason="No crash loops detected",
        )
    except Exception as exc:
        return TriggerResult(
            trigger_id="CRASH_LOOP",
            triggered=False,
            reason=f"Check failed: {exc}",
        )


def _check_budget_daily(
    db_path: str | Path | None = None,
) -> TriggerResult:
    """Check if daily budget hard cap has been exceeded."""
    db_path = _resolve_db(db_path)
    try:
        from pmacs.billing.budget_enforcer import DEFAULT_DAILY_HARD_CAP, _get_period_total

        conn = _sql_connect(db_path)
        try:
            current = _get_period_total(conn, "today")
        finally:
            conn.close()
        triggered = current >= DEFAULT_DAILY_HARD_CAP
        return TriggerResult(
            trigger_id="CYCLE_BLOCKED_BUDGET_DAILY",
            triggered=triggered,
            reason=f"Daily spend ${current:.4f}/{DEFAULT_DAILY_HARD_CAP:.2f}",
        )
    except Exception as exc:
        return TriggerResult(
            trigger_id="CYCLE_BLOCKED_BUDGET_DAILY",
            triggered=False,
            reason=f"Check skipped: {exc}",
        )


def _check_budget_monthly(
    db_path: str | Path | None = None,
) -> TriggerResult:
    """Check if monthly budget hard cap has been exceeded."""
    db_path = _resolve_db(db_path)
    try:
        from pmacs.billing.budget_enforcer import DEFAULT_MONTHLY_HARD_CAP, _get_period_total

        conn = _sql_connect(db_path)
        try:
            current = _get_period_total(conn, "this_month")
        finally:
            conn.close()
        triggered = current >= DEFAULT_MONTHLY_HARD_CAP
        return TriggerResult(
            trigger_id="CYCLE_BLOCKED_BUDGET_MONTHLY",
            triggered=triggered,
            reason=f"Monthly spend ${current:.4f}/{DEFAULT_MONTHLY_HARD_CAP:.2f}",
        )
    except Exception as exc:
        return TriggerResult(
            trigger_id="CYCLE_BLOCKED_BUDGET_MONTHLY",
            triggered=False,
            reason=f"Check skipped: {exc}",
        )


def _check_model_integrity(
    gguf_path: str | Path | None,
    expected_hash: str | None,
) -> TriggerResult:
    """Check model GGUF hash integrity."""
    if gguf_path is None or expected_hash is None:
        return TriggerResult(
            trigger_id="MODEL_INTEGRITY",
            triggered=False,
            reason="No model hash configured for check",
        )
    try:
        from pmacs.cortex.model_integrity import verify_gguf_hash

        ok = verify_gguf_hash(Path(gguf_path), expected_hash)
        return TriggerResult(
            trigger_id="MODEL_INTEGRITY",
            triggered=not ok,
            reason="Hash verified" if ok else "Hash mismatch",
        )
    except Exception as exc:
        return TriggerResult(
            trigger_id="MODEL_INTEGRITY",
            triggered=False,
            reason=f"Check failed: {exc}",
        )
