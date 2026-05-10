"""Integration tests for Phase 11 calibration engine.

Validates compute_brier across all 3 outcomes, refit_persona_weights with
synthetic data, CalibrationResult structure, and cycle_id acceptance.
"""
from __future__ import annotations

import inspect

import pytest

from pmacs.engines.calibration import CalibrationResult, compute_brier, refit_persona_weights


# ======================================================================
# compute_brier — all 3 outcomes
# ======================================================================

class TestBrierAllOutcomes:
    def test_actual_up(self) -> None:
        """Outcome 'up': actual vector = [1, 0, 0]."""
        score = compute_brier(1.0, 0.0, 0.0, "up", cycle_id="c001")
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_actual_flat(self) -> None:
        """Outcome 'flat': actual vector = [0, 1, 0]."""
        score = compute_brier(0.0, 1.0, 0.0, "flat", cycle_id="c002")
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_actual_down(self) -> None:
        """Outcome 'down': actual vector = [0, 0, 1]."""
        score = compute_brier(0.0, 0.0, 1.0, "down", cycle_id="c003")
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_brier_range_all_outcomes(self) -> None:
        """Brier score in [0, 2] for all 3 outcomes with non-perfect forecasts."""
        for actual in ("up", "flat", "down"):
            score = compute_brier(0.5, 0.3, 0.2, actual, cycle_id="c004")
            assert 0.0 <= score <= 2.0, f"Brier out of range for actual={actual}: {score}"


# ======================================================================
# refit_persona_weights — 20 synthetic resolutions
# ======================================================================

class TestRefitWithSyntheticResolutions:
    def test_20_synthetic_resolutions_adjusts_weights(self) -> None:
        """After 20 synthetic resolutions, refit_persona_weights adjusts weights
        and lower-Brier persona gets higher weight (Brier improves)."""
        # Simulate 20 resolutions: macro is better (lower Brier), sector worse
        macro_briers = [0.15, 0.20, 0.18, 0.22, 0.17, 0.19, 0.16, 0.21, 0.18, 0.20,
                        0.15, 0.19, 0.17, 0.23, 0.16, 0.18, 0.20, 0.19, 0.17, 0.21]
        sector_briers = [0.45, 0.50, 0.48, 0.52, 0.47, 0.49, 0.46, 0.51, 0.48, 0.50,
                         0.44, 0.49, 0.47, 0.53, 0.46, 0.48, 0.50, 0.49, 0.47, 0.51]

        avg_macro = sum(macro_briers) / len(macro_briers)
        avg_sector = sum(sector_briers) / len(sector_briers)

        # Verify macro has lower Brier (better)
        assert avg_macro < avg_sector

        persona_briers = {"macro": avg_macro, "sector": avg_sector}
        current_weights = {"macro": 0.5, "sector": 0.5}

        new_weights = refit_persona_weights(
            persona_briers, current_weights, min_samples=20, cycle_id="c-synth-001",
        )

        # Weights adjusted from 0.5/0.5
        assert new_weights["macro"] != pytest.approx(0.5, abs=0.01)
        assert new_weights["sector"] != pytest.approx(0.5, abs=0.01)

        # Better persona (macro) gets higher weight
        assert new_weights["macro"] > new_weights["sector"]

        # Weights sum to 1
        assert sum(new_weights.values()) == pytest.approx(1.0, abs=1e-9)

    def test_brier_improves_after_refit(self) -> None:
        """The effective Brier should improve when using the new weights
        vs the old equal weights."""
        persona_briers = {"macro": 0.18, "sector": 0.50, "technicals": 0.35}
        current_weights = {"macro": 0.33, "sector": 0.33, "technicals": 0.34}

        new_weights = refit_persona_weights(
            persona_briers, current_weights, min_samples=20, cycle_id="c-synth-002",
        )

        # Effective Brier with old weights
        old_eff = sum(
            current_weights[p] * persona_briers[p] for p in persona_briers
        )
        # Effective Brier with new weights
        new_eff = sum(
            new_weights[p] * persona_briers[p] for p in persona_briers
        )

        # New weights should give lower (better) effective Brier
        assert new_eff < old_eff


# ======================================================================
# cycle_id parameter acceptance
# ======================================================================

class TestCycleIdAcceptance:
    def test_compute_brier_accepts_cycle_id(self) -> None:
        """compute_brier function signature includes cycle_id parameter."""
        sig = inspect.signature(compute_brier)
        assert "cycle_id" in sig.parameters
        # Can be called with cycle_id without error
        score = compute_brier(0.5, 0.3, 0.2, "up", cycle_id="test-cycle-123")
        assert isinstance(score, float)

    def test_refit_accepts_cycle_id(self) -> None:
        """refit_persona_weights function signature includes cycle_id parameter."""
        sig = inspect.signature(refit_persona_weights)
        assert "cycle_id" in sig.parameters
        weights = refit_persona_weights(
            {"a": 0.3}, {"a": 1.0}, cycle_id="test-cycle-456",
        )
        assert "a" in weights


# ======================================================================
# CalibrationResult structure
# ======================================================================

class TestCalibrationResultStructure:
    def test_calibration_result_fields(self) -> None:
        """CalibrationResult dataclass has expected fields."""
        result = CalibrationResult(
            persona="macro",
            old_weight=0.33,
            new_weight=0.45,
            brier_before=0.30,
            brier_after=0.25,
            samples_used=20,
        )
        assert result.persona == "macro"
        assert result.old_weight == pytest.approx(0.33)
        assert result.new_weight == pytest.approx(0.45)
        assert result.brier_before == pytest.approx(0.30)
        assert result.brier_after == pytest.approx(0.25)
        assert result.samples_used == 20

    def test_calibration_result_all_personas(self) -> None:
        """Can create CalibrationResult for multiple personas."""
        personas = ["macro", "sector", "technicals", "forensics", "sentiment", "insider", "valuation"]
        for p in personas:
            result = CalibrationResult(
                persona=p,
                old_weight=0.14,
                new_weight=0.14,
                brier_before=0.30,
                brier_after=0.28,
                samples_used=20,
            )
            assert result.persona == p
