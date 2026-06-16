"""Mode management engine — mode transitions with operator-confirmation gating.

Spec ref: Architecture.md §4.6, Source.md §1, Phases.md §3
Mode ladder: INSTALLING -> SHADOW -> PAPER -> PAPER_VALIDATED -> LIVE_EARLY -> LIVE_STANDARD -> LIVE_EXPANDED
All LIVE transitions require an explicit operator confirmation.
PAPER → PAPER_VALIDATED also requires operator confirmation per Source.md.
"""
from __future__ import annotations

from pmacs.schemas.system import Mode, ModeTransition, VALID_MODE_TRANSITIONS

# Mode ladder ranking — higher = more privileged
MODE_RANK: dict[Mode, int] = {
    Mode.INSTALLING: 0,
    Mode.SHADOW: 1,
    Mode.PAPER: 2,
    Mode.PAPER_VALIDATED: 3,
    Mode.LIVE_EARLY: 4,
    Mode.LIVE_STANDARD: 5,
    Mode.LIVE_EXPANDED: 6,
}

# Modes that require explicit operator confirmation to transition INTO (promotion only)
# Spec: Source.md — PAPER_VALIDATED and all LIVE modes require operator confirmation
CONFIRMATION_REQUIRED_MODES: frozenset[Mode] = frozenset({
    Mode.PAPER_VALIDATED,
    Mode.LIVE_EARLY,
    Mode.LIVE_STANDARD,
    Mode.LIVE_EXPANDED,
})

# Backward compat alias
LIVE_MODES = CONFIRMATION_REQUIRED_MODES


def can_transition(from_mode: Mode, to_mode: Mode) -> bool:
    """Check if a mode transition is valid without performing it."""
    return to_mode in VALID_MODE_TRANSITIONS.get(from_mode, set())


def transition_mode(
    from_mode: Mode,
    to_mode: Mode,
    reason: str,
    operator_confirmed: bool = False,
    triggered_by: str = "OPERATOR",
) -> ModeTransition:
    """Attempt a mode transition.

    Args:
        from_mode: Current mode.
        to_mode: Target mode.
        reason: Human-readable reason for the transition.
        operator_confirmed: Whether the operator explicitly confirmed the action.
        triggered_by: "OPERATOR" or "AUTO_DEMOTION".

    Returns:
        ModeTransition record on success.

    Raises:
        ValueError: If transition is invalid or operator confirmation is missing for required modes.
    """
    if not can_transition(from_mode, to_mode):
        raise ValueError(
            f"Invalid mode transition: {from_mode.value} -> {to_mode.value}. "
            f"Valid targets: {[m.value for m in VALID_MODE_TRANSITIONS.get(from_mode, set())]}"
        )

    # Operator confirmation required for promotions to CONFIRMATION_REQUIRED_MODES (not demotions)
    is_promotion = MODE_RANK.get(to_mode, 0) > MODE_RANK.get(from_mode, 0)
    if to_mode in CONFIRMATION_REQUIRED_MODES and is_promotion and not operator_confirmed:
        raise ValueError(
            f"Mode transition to {to_mode.value} requires explicit operator confirmation"
        )

    return ModeTransition(
        from_mode=from_mode,
        to_mode=to_mode,
        reason=reason,
        operator_confirmed=operator_confirmed,
        triggered_by=triggered_by,
    )


def get_valid_transitions(mode: Mode) -> list[Mode]:
    """Get list of valid target modes from a given mode."""
    return list(VALID_MODE_TRANSITIONS.get(mode, set()))


def requires_confirmation(to_mode: Mode) -> bool:
    """Check if transitioning to this mode requires explicit operator confirmation."""
    return to_mode in LIVE_MODES
