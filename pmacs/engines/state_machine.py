"""Holding state machine — the ONE place state changes (Architecture.md §8.2, §16.1).

Direct mutation of holding.state is FORBIDDEN outside this module.
CI grep-fails on `holding.state =` outside state_machine.py.

Architecture.md §5.1: Every state transition is hash-chained.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pmacs.logsys import log_debug
from pmacs.schemas.contracts import (
    ABORT_REASON_STATES,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    Holding,
    HoldingState,
    InvalidStateTransition,
)


def transition(
    holding: Holding,
    new_state: HoldingState,
    reason: str,
    cycle_id: str,
    op_seq: int,
    audit_path: Path | None = None,
) -> Holding:
    """Transition a holding to a new state.

    This is the ONLY place Holding.state changes. Direct mutation is forbidden.
    Every transition is logged to the audit chain (Architecture.md §5.1).

    Args:
        holding: The holding to transition.
        new_state: The target state.
        reason: Human-readable reason for the transition.
        cycle_id: The current cycle ID.
        op_seq: Operation sequence number within the cycle.
        audit_path: Optional path to audit.log for hash-chained recording.

    Returns:
        The updated holding (state changed).

    Raises:
        InvalidStateTransition: If the transition is not valid.
    """
    current = holding.state

    # Terminal states are immutable
    if current in TERMINAL_STATES:
        raise InvalidStateTransition(
            f"Holding {holding.id} is terminal at {current.value}. "
            f"No transitions allowed from terminal states."
        )

    # Check if transition is valid
    valid = VALID_TRANSITIONS.get(current, frozenset())
    if new_state not in valid:
        raise InvalidStateTransition(
            f"Invalid transition: {current.value} -> {new_state.value} "
            f"for holding {holding.id}. "
            f"Valid: {[s.value for s in valid]}. Reason: {reason}"
        )

    # Apply transition (mutate the holding object)
    holding.state = new_state
    holding.updated_at = datetime.now(timezone.utc)

    # Capture abort reason
    if new_state in ABORT_REASON_STATES:
        holding.abort_reason = reason

    # Auto-fill exit date for terminal states
    if new_state in TERMINAL_STATES and holding.exit_date is None:
        from datetime import date as date_type
        holding.exit_date = date_type.today()

    # Record the transition in cycle_id_closed for closing transitions
    if new_state in TERMINAL_STATES and holding.cycle_id_closed is None:
        holding.cycle_id_closed = cycle_id

    # Hash-chained audit log (Architecture.md §5.1)
    transition_event = {
        "holding_id": holding.id,
        "from": current.value,
        "to": new_state.value,
        "reason": reason,
        "op_seq": op_seq,
    }
    if audit_path is not None:
        from pmacs.storage.audit import AuditWriter
        writer = AuditWriter(audit_path)
        writer.append(
            "state_transition",
            transition_event,
            cycle_id=cycle_id,
        )

    log_debug(
        "STATE_TRANSITION",
        payload=transition_event,
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Holding {holding.id[:8]}: {current.value} -> {new_state.value} ({reason})",
    )

    return holding


def is_valid_transition(current: HoldingState, target: HoldingState) -> bool:
    """Check if a transition is valid without performing it."""
    if current in TERMINAL_STATES:
        return False
    valid = VALID_TRANSITIONS.get(current, frozenset())
    return target in valid


def get_valid_transitions(state: HoldingState) -> list[HoldingState]:
    """Get list of valid next states from a given state."""
    return list(VALID_TRANSITIONS.get(state, frozenset()))
