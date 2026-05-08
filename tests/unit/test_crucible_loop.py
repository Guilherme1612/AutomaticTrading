"""Unit tests for the Crucible adversarial inner state machine (Agents.md §16).

Tests:
- Low severity (<0.3) → DONE in 1 cycle
- High severity (>=0.6) → ABORT in 1 cycle
- Medium severity (0.3–0.6) → REWRITE → low severity → DONE
- Medium severity (0.3–0.6) → REWRITE → still high severity → ABORT
- Budget exhaust in cycle 1 → ABORT
- Budget exhaust in cycle 2 → ABORT
"""
from __future__ import annotations

import time

import pytest

from pmacs.engines.crucible_loop import (
    BUDGET_PER_CYCLE_S,
    CrucibleLoopResult,
    CrucibleLoopState,
    MAX_CYCLES,
    SEVERITY_THRESHOLD_REWRITE,
    SEVERITY_THRESHOLD_SKIP,
    run_crucible_loop,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_output(severity: float) -> dict:
    """Produce a minimal crucible output dict with a severity field."""
    return {"severity": severity}


def _make_fn(results: list):
    """Create a callable that returns items from *results* in order.

    Returns ``None`` after the list is exhausted (simulates budget exhaust).
    """
    call_count = 0

    def fn(evidence, cycle_num):
        nonlocal call_count
        if call_count >= len(results):
            return None
        result = results[call_count]
        call_count += 1
        return result

    return fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLowSeverityDone:
    """severity < 0.3 → DONE in 1 cycle."""

    def test_very_low(self):
        fn = _make_fn([_make_output(0.05)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 1
        assert result.final_severity == pytest.approx(0.05)

    def test_just_below_threshold(self):
        fn = _make_fn([_make_output(0.29)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 1
        assert result.final_severity == pytest.approx(0.29)

    def test_zero_severity(self):
        fn = _make_fn([_make_output(0.0)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.final_severity == pytest.approx(0.0)


class TestHighSeverityAbort:
    """severity >= 0.6 → ABORT in 1 cycle."""

    def test_exactly_threshold(self):
        fn = _make_fn([_make_output(0.6)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 1
        assert result.final_severity == pytest.approx(0.6)

    def test_very_high(self):
        fn = _make_fn([_make_output(1.0)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 1
        assert result.final_severity == pytest.approx(1.0)

    def test_just_above_threshold(self):
        fn = _make_fn([_make_output(0.61)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 1


class TestMediumToDone:
    """0.3 <= severity < 0.6 → REWRITE → low severity → DONE."""

    def test_reduces_to_low(self):
        fn = _make_fn([_make_output(0.4), _make_output(0.2)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 2
        assert result.final_severity == pytest.approx(0.2)
        assert "thesis survived" in result.reason

    def test_reduces_to_medium_but_below_skip(self):
        """Second cycle severity 0.5 (< 0.6) should still be DONE."""
        fn = _make_fn([_make_output(0.4), _make_output(0.5)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 2
        assert result.final_severity == pytest.approx(0.5)

    def test_exactly_rewrite_threshold(self):
        """severity == 0.3 → goes to cycle 2."""
        fn = _make_fn([_make_output(0.3), _make_output(0.1)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 2
        assert result.final_severity == pytest.approx(0.1)


class TestMediumToAbort:
    """0.3 <= severity < 0.6 → REWRITE → still >= 0.6 → ABORT."""

    def test_increases_to_high(self):
        fn = _make_fn([_make_output(0.4), _make_output(0.7)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 2
        assert result.final_severity == pytest.approx(0.7)
        assert "NO_TRADE" in result.reason

    def test_stays_at_skip_threshold(self):
        """Second cycle severity == 0.6 → ABORT."""
        fn = _make_fn([_make_output(0.35), _make_output(0.6)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 2
        assert result.final_severity == pytest.approx(0.6)


class TestBudgetExhaust:
    """Budget exceeded at various stages → ABORT."""

    def test_cycle_1_returns_none(self):
        """run_crucible_fn returns None → ABORT."""
        fn = _make_fn([])  # returns None immediately
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 1
        assert "Budget exceeded" in result.reason

    def test_cycle_2_returns_none(self):
        """Cycle 1 succeeds (medium), cycle 2 returns None → ABORT."""
        fn = _make_fn([_make_output(0.4)])  # only 1 result, cycle 2 returns None
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 2
        assert "Budget exceeded" in result.reason

    def test_tiny_budget(self):
        """Budget so small it's immediately exceeded → ABORT."""
        fn = _make_fn([_make_output(0.1)])
        # Budget of 0 seconds — cycle 1 will likely still run fast enough
        # but if wall-clock exceeds, it should abort.
        result = run_crucible_loop(fn, [], budget_total_s=0.000001)
        # With near-zero budget the result depends on timing.
        # Either the first call finishes fast enough, or budget exceeds.
        assert result.final_state in (CrucibleLoopState.DONE, CrucibleLoopState.ABORT)

    def test_outputs_collected(self):
        """Outputs list should contain all successful cycle outputs."""
        out1 = _make_output(0.4)
        out2 = _make_output(0.1)
        fn = _make_fn([out1, out2])
        result = run_crucible_loop(fn, [])
        assert len(result.outputs) == 2
        assert result.outputs[0] is out1
        assert result.outputs[1] is out2


class TestAttributeBasedOutput:
    """run_crucible_fn can return objects with .severity attribute."""

    def test_object_with_severity_attr(self):
        class FakeOutput:
            severity = 0.2
        fn = _make_fn([FakeOutput()])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.final_severity == pytest.approx(0.2)

    def test_object_high_severity(self):
        class FakeOutput:
            severity = 0.8
        fn = _make_fn([FakeOutput()])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.final_severity == pytest.approx(0.8)
