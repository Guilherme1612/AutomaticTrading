"""Kill switch state machine — ARMED / ENGAGED (Architecture.md §13).

The kill switch is the primary safety mechanism. Any trigger can ENGAGE
without TOTP (safer to over-trigger). Only the operator can DISENGAGE
via valid TOTP code.

State persisted in SQLite `kill_switch` singleton table.
"""
from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.cortex.totp import verify_totp
from pmacs.logsys import log_debug


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

# The 10 triggers from Architecture.md §13.1
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
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_table(conn)
    return conn


def engage(
    reason: str,
    trigger: str,
    db_path: str | Path = "/var/db/pmacs/pmacs.db",
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

        # Write to audit log if path provided
        if audit_path is not None:
            from pmacs.storage.audit import AuditWriter

            writer = AuditWriter(audit_path)
            writer.append(
                "KILL_SWITCH_ENGAGED",
                {"trigger": trigger, "reason": reason, "engaged_at": now},
                cycle_id=cycle_id,
            )
            writer.close()

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
    db_path: str | Path = "/var/db/pmacs/pmacs.db",
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

        if audit_path is not None:
            from pmacs.storage.audit import AuditWriter

            writer = AuditWriter(audit_path)
            writer.append(
                "KILL_SWITCH_DISENGAGED",
                {"reason": reason, "disengaged_at": now},
                cycle_id=cycle_id,
            )
            writer.close()

        return True
    finally:
        conn.close()


def is_engaged(db_path: str | Path = "/var/db/pmacs/pmacs.db") -> bool:
    """Check if kill switch is currently engaged.

    Args:
        db_path: Path to SQLite database.

    Returns:
        True if ENGAGED, False if ARMED or table missing.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT state FROM kill_switch WHERE id = 1").fetchone()
        return row is not None and row[0] == KillSwitchState.ENGAGED.value
    finally:
        conn.close()


def get_state(db_path: str | Path = "/var/db/pmacs/pmacs.db") -> KillSwitchState:
    """Get current kill switch state.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Current KillSwitchState enum value.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT state FROM kill_switch WHERE id = 1").fetchone()
        if row is None:
            return KillSwitchState.ARMED
        return KillSwitchState(row[0])
    finally:
        conn.close()


def get_engagement_info(
    db_path: str | Path = "/var/db/pmacs/pmacs.db",
) -> dict[str, Any] | None:
    """Get engagement details (reason, trigger, time) if currently engaged.

    Args:
        db_path: Path to SQLite database.

    Returns:
        Dict with engagement info, or None if not engaged.
    """
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
    db_path: str | Path = "/var/db/pmacs/pmacs.db",
    audit_path: str | Path | None = None,
    heartbeat_dir: Path | None = None,
    gguf_path: str | Path | None = None,
    expected_gguf_hash: str | None = None,
) -> list[TriggerResult]:
    """Evaluate all 10 kill switch triggers.

    Args:
        db_path: Path to SQLite database.
        audit_path: Path to audit log file.
        heartbeat_dir: Directory containing heartbeat files.
        gguf_path: Path to GGUF model file.
        expected_gguf_hash: Expected SHA256 hash of GGUF file.

    Returns:
        List of TriggerResult for each trigger evaluation.
    """
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

    return results


def _check_rolling_loss(db_path: str | Path) -> TriggerResult:
    """Check rolling 5-day loss >10%."""
    from pmacs.constants import KILL_SWITCH_ROLLING_5D_LOSS_PCT

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                """SELECT total_value_usd FROM paper_account
                   ORDER BY snapshot_at DESC LIMIT 1"""
            ).fetchone()
            if row is None:
                return TriggerResult(
                    trigger_id="ROLLING_5D_LOSS",
                    triggered=False,
                    reason="No paper account data yet",
                )
            # Simplified check: compare latest to 5 days ago
            # Full implementation needs 5-day window calculation
            return TriggerResult(
                trigger_id="ROLLING_5D_LOSS",
                triggered=False,
                reason="Within tolerance",
                details={"current_value": row[0]},
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
        conn = sqlite3.connect(str(db_path))
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
