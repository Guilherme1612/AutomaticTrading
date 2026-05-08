"""Mode management engine — mode transitions with TOTP gating.

Spec ref: Architecture.md §4.6, Source.md §1, Phases.md §3
Mode ladder: INSTALLING -> SHADOW -> PAPER -> PAPER_VALIDATED -> LIVE_EARLY -> LIVE_STANDARD -> LIVE_EXPANDED
All LIVE transitions require operator TOTP verification.
"""
from __future__ import annotations

from pmacs.schemas.system import Mode, ModeTransition, VALID_MODE_TRANSITIONS

# Modes that require TOTP verification to transition into
LIVE_MODES: frozenset[Mode] = frozenset({
    Mode.LIVE_EARLY,
    Mode.LIVE_STANDARD,
    Mode.LIVE_EXPANDED,
})


def can_transition(from_mode: Mode, to_mode: Mode) -> bool:
    """Check if a mode transition is valid without performing it."""
    return to_mode in VALID_MODE_TRANSITIONS.get(from_mode, set())


def transition_mode(
    from_mode: Mode,
    to_mode: Mode,
    reason: str,
    totp_verified: bool = False,
    triggered_by: str = "OPERATOR",
) -> ModeTransition:
    """Attempt a mode transition.

    Args:
        from_mode: Current mode.
        to_mode: Target mode.
        reason: Human-readable reason for the transition.
        totp_verified: Whether operator TOTP has been verified.
        triggered_by: "OPERATOR" or "AUTO_DEMOTION".

    Returns:
        ModeTransition record on success.

    Raises:
        ValueError: If transition is invalid or TOTP is missing for LIVE modes.
    """
    if not can_transition(from_mode, to_mode):
        raise ValueError(
            f"Invalid mode transition: {from_mode.value} -> {to_mode.value}. "
            f"Valid targets: {[m.value for m in VALID_MODE_TRANSITIONS.get(from_mode, set())]}"
        )

    # LIVE transitions require TOTP verification
    if to_mode in LIVE_MODES and not totp_verified:
        raise ValueError(
            f"Mode transition to {to_mode.value} requires TOTP verification"
        )

    return ModeTransition(
        from_mode=from_mode,
        to_mode=to_mode,
        reason=reason,
        operator_totp_verified=totp_verified,
        triggered_by=triggered_by,
    )


def get_valid_transitions(mode: Mode) -> list[Mode]:
    """Get list of valid target modes from a given mode."""
    return list(VALID_MODE_TRANSITIONS.get(mode, set()))


def requires_totp(to_mode: Mode) -> bool:
    """Check if transitioning to this mode requires TOTP."""
    return to_mode in LIVE_MODES
