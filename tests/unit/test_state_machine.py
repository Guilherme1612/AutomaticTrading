"""State machine tests — Phase 1 exit test #3."""

import pytest

from pmacs.schemas.contracts import (
    ABORT_REASON_STATES,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    Holding,
    HoldingState,
    InvalidStateTransition,
)
from pmacs.engines.state_machine import (
    transition,
    is_valid_transition,
    get_valid_transitions,
)


def _make_holding(state: HoldingState = HoldingState.CANDIDATE) -> Holding:
    return Holding(id="h1", ticker="AAPL", state=state)


class TestValidTransitions:
    """Every valid transition must succeed."""

    def test_candidate_to_phase1(self):
        h = _make_holding(HoldingState.CANDIDATE)
        result = transition(h, HoldingState.PHASE1_RESEARCH, "test", "c1", 1)
        assert result.state == HoldingState.PHASE1_RESEARCH

    def test_phase1_to_phase2(self):
        h = _make_holding(HoldingState.PHASE1_RESEARCH)
        result = transition(h, HoldingState.PHASE2_CRUCIBLE, "test", "c1", 2)
        assert result.state == HoldingState.PHASE2_CRUCIBLE

    def test_phase2_to_approved(self):
        h = _make_holding(HoldingState.PHASE2_CRUCIBLE)
        result = transition(h, HoldingState.APPROVED_PENDING, "test", "c1", 3)
        assert result.state == HoldingState.APPROVED_PENDING

    def test_approved_to_active(self):
        h = _make_holding(HoldingState.APPROVED_PENDING)
        result = transition(h, HoldingState.ACTIVE, "test", "c1", 4)
        assert result.state == HoldingState.ACTIVE

    def test_active_to_stopped_out(self):
        h = _make_holding(HoldingState.ACTIVE)
        result = transition(h, HoldingState.STOPPED_OUT, "stop triggered", "c1", 5)
        assert result.state == HoldingState.STOPPED_OUT
        assert result.exit_date is not None
        assert result.cycle_id_closed == "c1"

    def test_active_to_exit_thesis(self):
        h = _make_holding(HoldingState.ACTIVE)
        result = transition(h, HoldingState.EXIT_THESIS_INVALIDATED, "thesis broke", "c1", 5)
        assert result.state == HoldingState.EXIT_THESIS_INVALIDATED
        assert result.exit_date is not None

    def test_candidate_to_aborted_pre(self):
        h = _make_holding(HoldingState.CANDIDATE)
        result = transition(h, HoldingState.ABORTED_PRE_LLM, "halted", "c1", 1)
        assert result.state == HoldingState.ABORTED_PRE_LLM
        assert result.abort_reason == "halted"


class TestInvalidTransitions:
    """Every invalid transition must raise."""

    def test_candidate_to_active_direct(self):
        h = _make_holding(HoldingState.CANDIDATE)
        with pytest.raises(InvalidStateTransition):
            transition(h, HoldingState.ACTIVE, "skip", "c1", 1)

    def test_active_to_candidate(self):
        h = _make_holding(HoldingState.ACTIVE)
        with pytest.raises(InvalidStateTransition):
            transition(h, HoldingState.CANDIDATE, "undo", "c1", 1)

    def test_stopped_out_to_active(self):
        """Terminal states are immutable."""
        h = _make_holding(HoldingState.STOPPED_OUT)
        with pytest.raises(InvalidStateTransition, match="terminal"):
            transition(h, HoldingState.ACTIVE, "revive", "c1", 1)

    def test_resolved_up_to_any(self):
        h = _make_holding(HoldingState.RESOLVED_UP)
        with pytest.raises(InvalidStateTransition):
            transition(h, HoldingState.CANDIDATE, "redo", "c1", 1)


class TestHelpers:
    def test_is_valid_transition(self):
        assert is_valid_transition(HoldingState.CANDIDATE, HoldingState.PHASE1_RESEARCH)
        assert not is_valid_transition(HoldingState.CANDIDATE, HoldingState.ACTIVE)
        assert not is_valid_transition(HoldingState.STOPPED_OUT, HoldingState.ACTIVE)

    def test_get_valid_transitions(self):
        transitions = get_valid_transitions(HoldingState.CANDIDATE)
        assert HoldingState.PHASE1_RESEARCH in transitions
        assert HoldingState.ACTIVE not in transitions
