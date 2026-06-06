"""Core contract schemas — Holding state machine, Thesis."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Optional

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
    HoldingState.ABORTED_PRE_LLM, HoldingState.ABORTED_LLM,
    HoldingState.ABORTED_RISK,
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
    }),
    HoldingState.PHASE1_RESEARCH: frozenset({
        HoldingState.PHASE2_CRUCIBLE, HoldingState.ABORTED_LLM,
        HoldingState.PHASE1_TIMEOUT,
    }),
    HoldingState.PHASE1_TIMEOUT: frozenset({
        HoldingState.ABORTED_LLM,
    }),
    HoldingState.PHASE2_CRUCIBLE: frozenset({
        HoldingState.APPROVED_PENDING, HoldingState.ABORTED_LLM,
    }),
    HoldingState.APPROVED_PENDING: frozenset({
        HoldingState.ACTIVE, HoldingState.ABORTED_RISK,
        HoldingState.ABORTED_LLM,
    }),
    HoldingState.ACTIVE: frozenset({
        HoldingState.RESOLVED_UP, HoldingState.RESOLVED_FLAT,
        HoldingState.RESOLVED_DOWN, HoldingState.RESOLVED_MIXED,
        HoldingState.STOPPED_OUT, HoldingState.EXIT_THESIS_INVALIDATED,
        HoldingState.EXIT_OPPORTUNITY_COST, HoldingState.EXIT_TRAILING_STOP,
        HoldingState.EXIT_FAILED, HoldingState.HALTED, HoldingState.DELISTED,
        HoldingState.RESOLUTION_TIMEOUT, HoldingState.PANIC_EXIT,
        HoldingState.INTERRUPTED, HoldingState.THESIS_AGING_REVIEW,
    }),
    HoldingState.THESIS_AGING_REVIEW: frozenset({
        HoldingState.ACTIVE, HoldingState.EXIT_THESIS_INVALIDATED,
    }),
    HoldingState.HALTED: frozenset({
        HoldingState.ACTIVE, HoldingState.DELISTED, HoldingState.PANIC_EXIT,
    }),
    HoldingState.INTERRUPTED: frozenset({
        HoldingState.ACTIVE, HoldingState.PANIC_EXIT, HoldingState.DELISTED,
    }),
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Holding(BaseModel):
    """A holding through its lifecycle. State transitions via state_machine only.

    Architecture.md §8.3 — all fields must match spec exactly.
    """
    model_config = ConfigDict(frozen=False)  # state machine mutates state

    id: str
    ticker: str
    catalyst_id: str = ""

    state: HoldingState = HoldingState.CANDIDATE
    mode: Literal["SHADOW", "PAPER", "PAPER_VALIDATED",
                  "LIVE_EARLY", "LIVE_STANDARD", "LIVE_EXPANDED"] = "SHADOW"
    abort_reason: str | None = None

    # Entry
    signal_price: float = 0.0
    entry_date: datetime | date | None = None
    entry_price: float | None = None
    position_size_usd: float = 0.0
    position_size_shares: float = 0.0

    # Decision context (snapshot at entry)
    original_p_up: float = 0.0
    original_p_flat: float = 0.0
    original_p_down: float = 0.0
    original_ev_net: float = 0.0
    original_conviction: float = 0.0
    thesis_hash: str = ""
    thesis_version: int = 1
    thesis_embedding_id: str | None = None
    fundamental_weights_moat: float = 0.0
    fundamental_weights_growth: float = 0.0
    matured_sources_at_entry: int = 0
    crucible_severity_at_entry: float = 0.0

    # Risk
    stop_loss_price: float = 0.0
    catastrophe_net_price: float = 0.0
    trailing_stop_price: float | None = None
    trailing_stop_armed: bool = False
    thesis_review_due_date: date | None = None

    # Exit
    exit_date: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    realized_pnl_usd: float | None = None
    realized_pnl_pct: float | None = None

    # Audit linkage
    cycle_id_opened: str = ""
    cycle_id_closed: str | None = None

    # Extra fields for operational use (not in spec but needed by existing code)
    thesis: Thesis | None = None
    sector: str | None = None
    subsector: str | None = None
    catalyst_type: str | None = None
    last_reeval_at: date | None = None
    verdict: str | None = None
    conviction_score: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Backward-compatible aliases (DB columns use _usd suffix; kept for migration)
    entry_price_usd: float | None = None
    exit_price_usd: float | None = None
    stop_price_usd: float | None = None
    trailing_stop_price_usd: float | None = None

    @model_validator(mode="after")
    def _sync_price_aliases(self) -> "Holding":
        """Keep spec fields and _usd aliases in sync."""
        if self.entry_price is not None and self.entry_price_usd is None:
            object.__setattr__(self, "entry_price_usd", self.entry_price)
        elif self.entry_price_usd is not None and self.entry_price is None:
            object.__setattr__(self, "entry_price", self.entry_price_usd)
        if self.exit_price is not None and self.exit_price_usd is None:
            object.__setattr__(self, "exit_price_usd", self.exit_price)
        elif self.exit_price_usd is not None and self.exit_price is None:
            object.__setattr__(self, "exit_price", self.exit_price_usd)
        if self.stop_loss_price and not self.stop_price_usd:
            object.__setattr__(self, "stop_price_usd", self.stop_loss_price)
        elif self.stop_price_usd and not self.stop_loss_price:
            object.__setattr__(self, "stop_loss_price", self.stop_price_usd)
        if self.trailing_stop_price is not None and self.trailing_stop_price_usd is None:
            object.__setattr__(self, "trailing_stop_price_usd", self.trailing_stop_price)
        elif self.trailing_stop_price_usd is not None and self.trailing_stop_price is None:
            object.__setattr__(self, "trailing_stop_price", self.trailing_stop_price_usd)
        return self

    @model_validator(mode="after")
    def _check_probabilities(self) -> "Holding":
        """Validate original probabilities sum to 1.0 when set (Architecture.md §8.3)."""
        total = self.original_p_up + self.original_p_flat + self.original_p_down
        if total > 0 and abs(total - 1.0) > 1e-6:
            raise ValueError(f"original probabilities sum to {total}, expected 1.0")
        return self

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
