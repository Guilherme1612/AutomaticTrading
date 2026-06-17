"""System schemas — Mode, KillSwitchState, system-level models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Mode(str, Enum):
    INSTALLING = "INSTALLING"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    PAPER_VALIDATED = "PAPER_VALIDATED"
    LIVE_EARLY = "LIVE_EARLY"
    LIVE_STANDARD = "LIVE_STANDARD"
    LIVE_EXPANDED = "LIVE_EXPANDED"


class KillSwitchState(str, Enum):
    DISENGAGED = "DISENGAGED"
    ENGAGED = "ENGAGED"


class KillSwitchTrigger(str, Enum):
    DAILY_LOSS = "DAILY_LOSS"
    ROLLING_5D_LOSS = "ROLLING_5D_LOSS"
    AUDIT_CHAIN_BREAK = "AUDIT_CHAIN_BREAK"
    CRASH_LOOP = "CRASH_LOOP"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    OPERATOR_MANUAL = "OPERATOR_MANUAL"
    CORTEX_SELF_CHECK_TIMEOUT = "CORTEX_SELF_CHECK_TIMEOUT"
    MODEL_HASH_MISMATCH = "MODEL_HASH_MISMATCH"
    AUDIT_REPLICATION_PERSISTENT = "AUDIT_REPLICATION_PERSISTENT"
    MODE_DEMOTION = "MODE_DEMOTION"


class ModeTransition(BaseModel):
    """Record of a mode change."""
    model_config = ConfigDict(frozen=True)

    from_mode: Mode
    to_mode: Mode
    changed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""
    operator_confirmed: bool = False
    triggered_by: str = "OPERATOR"  # OPERATOR / AUTO_DEMOTION


# Valid mode transitions
VALID_MODE_TRANSITIONS: dict[Mode, set[Mode]] = {
    Mode.INSTALLING: {Mode.SHADOW, Mode.PAPER},
    Mode.SHADOW: {Mode.PAPER},
    Mode.PAPER: {Mode.PAPER_VALIDATED, Mode.SHADOW},
    Mode.PAPER_VALIDATED: {Mode.LIVE_EARLY, Mode.PAPER},
    Mode.LIVE_EARLY: {Mode.LIVE_STANDARD, Mode.PAPER_VALIDATED},
    Mode.LIVE_STANDARD: {Mode.LIVE_EXPANDED, Mode.LIVE_EARLY},
    Mode.LIVE_EXPANDED: {Mode.LIVE_STANDARD},
}

# Combined SHADOW + PAPER is the standard mode after wizard
# SHADOW is always-on: it captures audit-only signals (math-gate metrics, verdicts)
# alongside whatever the active execution mode does. Architecture.md §16.9:
# "Mutation A/B running in PAPER — candidate arm runs SHADOW-only."
# Phases.md §3.7: "SHADOW and PAPER are concurrent from day 1."
SHADOW_PAPER_MODES: frozenset[Mode] = frozenset({Mode.SHADOW, Mode.PAPER})


def is_shadow_active(mode: str) -> bool:
    """Check if SHADOW audit capture is active for the given mode.

    SHADOW is always-on for PAPER and above. INSTALLING has no shadow.
    Pure SHADOW mode (rare, manual) is shadow-only.
    """
    return mode in (
        Mode.SHADOW.value,
        Mode.PAPER.value,
        Mode.PAPER_VALIDATED.value,
        Mode.LIVE_EARLY.value,
        Mode.LIVE_STANDARD.value,
        Mode.LIVE_EXPANDED.value,
    )
