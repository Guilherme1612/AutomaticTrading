"""Core contract schemas — Holding state machine, Thesis."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HoldingState(str, Enum):
    """All holding states. Direct mutation forbidden (Architecture.md §16.1)."""
    # Pre-decision pipeline
    CANDIDATE = "CANDIDATE"
    PHASE1_RESEARCH = "PHASE1_RESEARCH"
    PHASE2_CRUCIBLE = "PHASE2_CRUCIBLE"
    APPROVED_PENDING = "APPROVED_PENDING"
    # Active position
    ACTIVE = "ACTIVE"
    # Aborts
    ABORTED_PRE_LLM = "ABORTED_PRE_LLM"
    ABORTED_LLM = "ABORTED_LLM"
    ABORTED_RISK = "ABORTED_RISK"
    PHASE1_TIMEOUT = "PHASE1_TIMEOUT"
    # Resolutions (terminal)
    RESOLVED_UP = "RESOLVED_UP"
    RESOLVED_FLAT = "RESOLVED_FLAT"
    RESOLVED_DOWN = "RESOLVED_DOWN"
    RESOLVED_MIXED = "RESOLVED_MIXED"
    # Exits (terminal)
    STOPPED_OUT = "STOPPED_OUT"
    EXIT_THESIS_INVALIDATED = "EXIT_THESIS_INVALIDATED"
    EXIT_OPPORTUNITY_COST = "EXIT_OPPORTUNITY_COST"
    EXIT_TRAILING_STOP = "EXIT_TRAILING_STOP"
    EXIT_FAILED = "EXIT_FAILED"
    # Operational
    HALTED = "HALTED"
    DELISTED = "DELISTED"
    RESOLUTION_TIMEOUT = "RESOLUTION_TIMEOUT"
    PANIC_EXIT = "PANIC_EXIT"
    INTERRUPTED = "INTERRUPTED"
    THESIS_AGING_REVIEW = "THESIS_AGING_REVIEW"


TERMINAL_STATES = frozenset({
    HoldingState.RESOLVED_UP, HoldingState.RESOLVED_FLAT,
    HoldingState.RESOLVED_DOWN, HoldingState.RESOLVED_MIXED,
    HoldingState.STOPPED_OUT, HoldingState.EXIT_THESIS_INVALIDATED,
    HoldingState.EXIT_OPPORTUNITY_COST, HoldingState.EXIT_TRAILING_STOP,
    HoldingState.EXIT_FAILED, HoldingState.DELISTED,
    HoldingState.RESOLUTION_TIMEOUT, HoldingState.PANIC_EXIT,
    HoldingState.INTERRUPTED,
})

ABORT_STATES = frozenset({
    HoldingState.ABORTED_PRE_LLM, HoldingState.ABORTED_LLM,
    HoldingState.ABORTED_RISK,
})

# Valid transitions from each state (Architecture.md §8.2)
VALID_TRANSITIONS: dict[HoldingState, frozenset[HoldingState]] = {
    HoldingState.CANDIDATE: frozenset({
        HoldingState.PHASE1_RESEARCH, HoldingState.ABORTED_PRE_LLM,
        HoldingState.HALTED,
    }),
    HoldingState.PHASE1_RESEARCH: frozenset({
        HoldingState.PHASE2_CRUCIBLE, HoldingState.ABORTED_LLM,
        HoldingState.PHASE1_TIMEOUT,
    }),
    HoldingState.PHASE2_CRUCIBLE: frozenset({
        HoldingState.APPROVED_PENDING, HoldingState.ABORTED_LLM,
    }),
    HoldingState.APPROVED_PENDING: frozenset({
        HoldingState.ACTIVE, HoldingState.ABORTED_RISK,
    }),
    HoldingState.ACTIVE: frozenset({
        HoldingState.THESIS_AGING_REVIEW,
        HoldingState.STOPPED_OUT, HoldingState.EXIT_THESIS_INVALIDATED,
        HoldingState.EXIT_OPPORTUNITY_COST, HoldingState.EXIT_TRAILING_STOP,
        HoldingState.EXIT_FAILED, HoldingState.DELISTED,
        HoldingState.RESOLUTION_TIMEOUT, HoldingState.PANIC_EXIT,
        HoldingState.HALTED,
    }),
    HoldingState.THESIS_AGING_REVIEW: frozenset({
        HoldingState.ACTIVE, HoldingState.EXIT_THESIS_INVALIDATED,
    }),
    HoldingState.HALTED: frozenset({HoldingState.CANDIDATE}),
}

ABORT_REASON_STATES = frozenset({
    HoldingState.ABORTED_PRE_LLM, HoldingState.ABORTED_LLM,
    HoldingState.ABORTED_RISK,
})


class Thesis(BaseModel):
    """Investment thesis with hash for dedup."""
    model_config = ConfigDict(frozen=True)

    id: str
    ticker: str
    text: str
    hash: str
    version: int = 1
    catalyst_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Holding(BaseModel):
    """A holding through its lifecycle. State transitions via state_machine only."""
    model_config = ConfigDict(frozen=False)  # state machine mutates state

    id: str
    ticker: str
    state: HoldingState = HoldingState.CANDIDATE
    cycle_id_opened: str = ""
    cycle_id_closed: str | None = None
    thesis: Thesis | None = None
    entry_date: date | None = None
    exit_date: date | None = None
    entry_price_usd: float | None = None
    exit_price_usd: float | None = None
    position_size_usd: float | None = None
    abort_reason: str | None = None
    sector: str | None = None
    subsector: str | None = None
    catalyst_type: str | None = None
    last_reeval_at: date | None = None
    stop_price_usd: float | None = None
    trailing_stop_price_usd: float | None = None
    verdict: str | None = None
    conviction_score: float | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def _validate_terminal_has_exit(self) -> "Holding":
        if self.state in TERMINAL_STATES and self.exit_date is None:
            # Will be set by state_machine.transition(); allow creation
            pass
        return self


class InvalidStateTransition(Exception):
    """Raised when a holding state transition is invalid."""


# Re-export for convenience
__all__ = [
    "HoldingState", "TERMINAL_STATES", "ABORT_STATES",
    "VALID_TRANSITIONS", "ABORT_REASON_STATES",
    "Thesis", "Holding", "InvalidStateTransition",
]
