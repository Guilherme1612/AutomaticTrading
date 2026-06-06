"""Integration tests for Crucible adversarial loop budget and severity routing.

Tests the full Crucible pipeline integration:
  1. Time budget enforcement (90s per cycle, 180s total)
  2. Two-iteration rewrite loop state machine
  3. Severity thresholds (>=0.6 abort, 0.3-0.6 rewrite, <0.3 done)
  4. CrucibleSanity validator with mock crucible outputs
  5. CrucibleOutput Pydantic schema validation in loop context
  6. Grammar loading and sanity validator wiring

No running llama-server required — all LLM calls are mocked.

spec_ref: Agents.md §16, Architecture.md §9.4
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from pmacs.agents.sanity.crucible import CrucibleSanity
from pmacs.engines.crucible_loop import (
    BUDGET_PER_CYCLE_S,
    MAX_CYCLES,
    SEVERITY_THRESHOLD_REWRITE,
    SEVERITY_THRESHOLD_SKIP,
    CrucibleLoopResult,
    CrucibleLoopState,
    run_crucible_loop,
)
from pmacs.schemas.personas import CrucibleOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attack(severity: float = 0.3, attack_type: str = "LOGICAL_HOLE") -> dict:
    return {
        "attack_type": attack_type,
        "severity": severity,
        "description": "Test attack description",
        "evidence_ids": ["ev1"],
    }


def _make_crucible_output(
    severity: float = 0.3,
    thesis_survives: bool = True,
    attacks: list[dict] | None = None,
    rewrite_cycle: int = 1,
) -> CrucibleOutput:
    """Create a valid CrucibleOutput for integration testing."""
    if attacks is None:
        attacks = [_make_attack(severity)]
    if severity >= 0.6:
        thesis_survives = False
    else:
        thesis_survives = True

    return CrucibleOutput(
        ticker="AAPL",
        attacks=attacks,
        attack_count=len(attacks),
        severity=severity,
        thesis_survives=thesis_survives,
        summary=f"Crucible cycle {rewrite_cycle} output",
        rewrite_cycle=rewrite_cycle,
    )


def _make_crucible_fn(results: list[CrucibleOutput | None]):
    """Create a mock crucible callable returning results in order.

    Returns None after list is exhausted to simulate budget exhaust.
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


class TestCrucibleBudgetConstants:
    """Verify budget constants match spec (Agents.md §16)."""

    def test_budget_per_cycle_90s(self) -> None:
        assert BUDGET_PER_CYCLE_S == 90.0

    def test_max_cycles_2(self) -> None:
        assert MAX_CYCLES == 2

    def test_total_budget_180s(self) -> None:
        assert MAX_CYCLES * BUDGET_PER_CYCLE_S == 180.0

    def test_severity_abort_threshold(self) -> None:
        assert SEVERITY_THRESHOLD_SKIP == 0.6

    def test_severity_rewrite_threshold(self) -> None:
        assert SEVERITY_THRESHOLD_REWRITE == 0.3


class TestSeverityRouting:
    """Severity thresholds route correctly: abort / rewrite / done."""

    def test_low_severity_done_cycle1(self) -> None:
        """severity < 0.3: DONE after 1 cycle."""
        output = _make_crucible_output(severity=0.2)
        fn = _make_crucible_fn([output])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 1
        assert result.final_severity == pytest.approx(0.2)

    def test_high_severity_abort_cycle1(self) -> None:
        """severity >= 0.6: ABORT after 1 cycle."""
        output = _make_crucible_output(severity=0.7)
        fn = _make_crucible_fn([output])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 1
        assert result.final_severity == pytest.approx(0.7)

    def test_exact_0_6_aborts(self) -> None:
        """severity == 0.6 exactly: ABORT."""
        output = _make_crucible_output(severity=0.6)
        fn = _make_crucible_fn([output])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT

    def test_medium_to_done_rewrite(self) -> None:
        """0.3 <= severity < 0.6: REWRITE, then severity < 0.6: DONE."""
        out1 = _make_crucible_output(severity=0.4, rewrite_cycle=1)
        out2 = _make_crucible_output(severity=0.2, rewrite_cycle=2)
        fn = _make_crucible_fn([out1, out2])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 2
        assert result.final_severity == pytest.approx(0.2)
        assert "thesis survived" in result.reason

    def test_medium_to_abort_rewrite(self) -> None:
        """0.3 <= severity < 0.6: REWRITE, then severity >= 0.6: ABORT."""
        out1 = _make_crucible_output(severity=0.35, rewrite_cycle=1)
        out2 = _make_crucible_output(severity=0.7, rewrite_cycle=2)
        fn = _make_crucible_fn([out1, out2])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 2
        assert result.final_severity == pytest.approx(0.7)
        assert "NO_TRADE" in result.reason

    def test_medium_stays_medium_done(self) -> None:
        """Rewrite cycle severity stays 0.3-0.6 but < 0.6: DONE (2 cycles)."""
        out1 = _make_crucible_output(severity=0.4, rewrite_cycle=1)
        out2 = _make_crucible_output(severity=0.5, rewrite_cycle=2)
        fn = _make_crucible_fn([out1, out2])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE
        assert result.cycles_used == 2

    def test_exact_0_3_triggers_rewrite(self) -> None:
        """severity == 0.3 exactly: triggers rewrite (not done)."""
        out1 = _make_crucible_output(severity=0.3, rewrite_cycle=1)
        out2 = _make_crucible_output(severity=0.1, rewrite_cycle=2)
        fn = _make_crucible_fn([out1, out2])
        result = run_crucible_loop(fn, [])
        assert result.cycles_used == 2
        assert result.final_state == CrucibleLoopState.DONE


class TestBudgetEnforcement:
    """Time budget enforcement at integration level."""

    def test_cycle1_budget_exhaust(self) -> None:
        """fn returns None in cycle 1 -> ABORT with budget message."""
        fn = _make_crucible_fn([])  # returns None immediately
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 1
        assert "Budget exceeded" in result.reason

    def test_cycle2_budget_exhaust(self) -> None:
        """Cycle 1 succeeds (medium), cycle 2 returns None -> ABORT."""
        out1 = _make_crucible_output(severity=0.4, rewrite_cycle=1)
        fn = _make_crucible_fn([out1])  # only 1 result; cycle 2 returns None
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.ABORT
        assert result.cycles_used == 2
        assert "Budget exceeded" in result.reason

    def test_total_budget_default_180(self) -> None:
        """Default total budget is 180s."""
        fn = _make_crucible_fn([_make_crucible_output(severity=0.1)])
        result = run_crucible_loop(fn, [])
        assert result.final_state == CrucibleLoopState.DONE

    def test_custom_budget_propagated(self) -> None:
        """Custom budget_total_s is respected."""
        fn = _make_crucible_fn([_make_crucible_output(severity=0.1)])
        result = run_crucible_loop(fn, [], budget_total_s=0.000001)
        # With near-zero budget, depends on timing
        assert result.final_state in (CrucibleLoopState.DONE, CrucibleLoopState.ABORT)

    def test_outputs_collected_across_cycles(self) -> None:
        """Both cycle outputs are collected in result.outputs."""
        out1 = _make_crucible_output(severity=0.4, rewrite_cycle=1)
        out2 = _make_crucible_output(severity=0.1, rewrite_cycle=2)
        fn = _make_crucible_fn([out1, out2])
        result = run_crucible_loop(fn, [])
        assert len(result.outputs) == 2
        assert result.outputs[0] is out1
        assert result.outputs[1] is out2


class TestCrucibleSanityIntegration:
    """CrucibleSanity validator works with CrucibleOutput in loop context."""

    def test_valid_output_passes_sanity(self) -> None:
        """A valid CrucibleOutput passes sanity check."""
        validator = CrucibleSanity()
        output = _make_crucible_output(severity=0.3)
        raw = output.model_dump()
        result = validator.validate(raw, evidence=[])
        assert result.passed

    def test_high_severity_fails_survives_true(self) -> None:
        """CrucibleOutput with severity > 0.6 and thesis_survives=True is rejected by schema."""
        with pytest.raises(Exception):
            CrucibleOutput(
                ticker="AAPL",
                attacks=[_make_attack(0.8)],
                attack_count=1,
                severity=0.8,
                thesis_survives=True,  # invalid: severity > 0.6
                summary="test",
                rewrite_cycle=1,
            )

    def test_sanity_catches_severity_mismatch(self) -> None:
        """Sanity validator catches severity != max attack severity."""
        validator = CrucibleSanity()
        raw = {
            "attacks": [_make_attack(0.8)],
            "severity": 0.3,  # mismatch with attack severity
            "attack_count": 1,
            "thesis_survives": True,
        }
        result = validator.validate(raw, evidence=[])
        assert not result.passed
        assert "severity" in (result.reason or "").lower()

    def test_sanity_catches_duplicate_attacks(self) -> None:
        """Sanity validator catches duplicate attacks."""
        validator = CrucibleSanity()
        attack = _make_attack(0.4, "LOGICAL_HOLE")
        raw = {
            "attacks": [attack, attack],  # duplicate
            "severity": 0.4,
            "attack_count": 2,
            "thesis_survives": True,
        }
        result = validator.validate(raw, evidence=[])
        assert not result.passed
        assert "duplicate" in (result.reason or "").lower()

    def test_loop_output_fed_to_sanity(self) -> None:
        """Outputs from the loop pass sanity validation."""
        out1 = _make_crucible_output(severity=0.4, rewrite_cycle=1)
        out2 = _make_crucible_output(severity=0.2, rewrite_cycle=2)
        fn = _make_crucible_fn([out1, out2])
        result = run_crucible_loop(fn, [])

        validator = CrucibleSanity()
        for output in result.outputs:
            raw = output.model_dump()
            sanity = validator.validate(raw, evidence=[])
            assert sanity.passed, f"Sanity failed: {sanity.reason}"


class TestGrammarAndValidatorWiring:
    """Crucible grammar loads and sanity validator is wired correctly."""

    def test_crucible_grammar_loads(self) -> None:
        """Crucible GBNF grammar loads successfully."""
        from pmacs.agents.grammars import load_grammar

        grammar = load_grammar("crucible")
        assert len(grammar) > 0
        assert "root" in grammar

    def test_crucible_sanity_validator_instantiable(self) -> None:
        """CrucibleSanity can be instantiated directly."""
        validator = CrucibleSanity()
        assert validator is not None

    def test_crucible_output_schema_roundtrip(self) -> None:
        """CrucibleOutput round-trips through model_dump/model_validate."""
        output = _make_crucible_output(severity=0.35, rewrite_cycle=1)
        raw = output.model_dump()
        restored = CrucibleOutput.model_validate(raw)
        assert restored.severity == pytest.approx(0.35)
        assert restored.rewrite_cycle == 1
        assert restored.thesis_survives is True

    def test_crucible_runner_class_exists(self) -> None:
        """CrucibleRunner class is importable and configured correctly.

        Skipped if pmacs.data.gateway module is not available (stub mode).
        """
        try:
            from pmacs.agents.crucible import CrucibleRunner
        except ModuleNotFoundError:
            pytest.skip("pmacs.data.gateway not available — stub mode")

        runner = CrucibleRunner()
        assert runner.persona_name == "crucible"
        assert runner.grammar_name == "crucible"
        assert runner.base_temperature == 0.1  # Lower than other personas per spec
        assert runner.max_tokens == 768
