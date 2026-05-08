"""Unit tests for Phase 11 calibration engines, flywheel health, and episodic context."""
from __future__ import annotations

import pytest

from pmacs.engines.calibration import CalibrationResult, compute_brier, refit_persona_weights
from pmacs.engines.causal_attribution import AttributionResult, attribute_resolution
from pmacs.engines.lessons import Lesson, extract_lesson_from_resolution
from pmacs.engines.crucible_calibration import compute_severity_multiplier
from pmacs.engines.override_learning import OverrideCluster, cluster_overrides
from pmacs.engines.flywheel_health import FlywheelHealthSnapshot, snapshot_health
from pmacs.agents.episodic_context import build_context_brief


# ======================================================================
# compute_brier
# ======================================================================

class TestComputeBrier:
    def test_perfect_up_prediction(self):
        """Predict up with certainty, actual up -> Brier = 0."""
        score = compute_brier(1.0, 0.0, 0.0, "up")
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_worst_prediction(self):
        """Predict up with certainty, actual down -> Brier = 2.0."""
        score = compute_brier(1.0, 0.0, 0.0, "down")
        assert score == pytest.approx(2.0, abs=1e-9)

    def test_flat_actual_uniform_forecast(self):
        """Uniform forecast, flat actual -> Brier = (0.33^2 + 0.67^2 + 0.33^2)."""
        score = compute_brier(0.33, 0.34, 0.33, "flat")
        expected = (0.33 - 0) ** 2 + (0.34 - 1) ** 2 + (0.33 - 0) ** 2
        assert score == pytest.approx(expected, abs=0.01)

    def test_known_value(self):
        """p_up=0.7, p_flat=0.2, p_down=0.1, actual=up."""
        score = compute_brier(0.7, 0.2, 0.1, "up")
        expected = (0.7 - 1) ** 2 + (0.2 - 0) ** 2 + (0.1 - 0) ** 2
        assert score == pytest.approx(expected, abs=1e-9)

    def test_unknown_actual_uses_uniform(self):
        """Unknown actual string falls back to uniform outcome vector."""
        score = compute_brier(0.5, 0.3, 0.2, "UNKNOWN")
        assert isinstance(score, float)
        assert score > 0


# ======================================================================
# refit_persona_weights
# ======================================================================

class TestRefitPersonaWeights:
    def test_weights_sum_to_one(self):
        briers = {"macro": 0.3, "sector": 0.5, "technicals": 0.2}
        current = {"macro": 0.33, "sector": 0.33, "technicals": 0.34}
        weights = refit_persona_weights(briers, current)
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-9)

    def test_better_persona_gets_higher_weight(self):
        """Lower Brier -> higher weight."""
        briers = {"good_persona": 0.1, "bad_persona": 0.8}
        current = {"good_persona": 0.5, "bad_persona": 0.5}
        weights = refit_persona_weights(briers, current)
        assert weights["good_persona"] > weights["bad_persona"]

    def test_single_persona(self):
        briers = {"only": 0.4}
        current = {"only": 1.0}
        weights = refit_persona_weights(briers, current)
        assert weights["only"] == pytest.approx(1.0, abs=1e-9)

    def test_equal_briers_equal_weights(self):
        briers = {"a": 0.3, "b": 0.3}
        current = {"a": 0.5, "b": 0.5}
        weights = refit_persona_weights(briers, current)
        assert weights["a"] == pytest.approx(weights["b"], abs=1e-9)


# ======================================================================
# attribute_resolution
# ======================================================================

class TestAttributeResolution:
    def test_up_outcome_credit(self):
        outputs = {
            "bull": {"p_up": 0.8, "p_down": 0.1},
            "bear": {"p_up": 0.2, "p_down": 0.7},
        }
        results = attribute_resolution("BUY", "up", outputs)
        bull = next(r for r in results if r.persona == "bull")
        bear = next(r for r in results if r.persona == "bear")
        assert bull.credit > 0  # predicted up correctly
        assert bear.credit < 0  # predicted down, was wrong

    def test_down_outcome_credit(self):
        outputs = {"persona_a": {"p_up": 0.1, "p_down": 0.8}}
        results = attribute_resolution("SKIP", "down", outputs)
        assert results[0].credit > 0  # predicted down correctly

    def test_flat_outcome_credit(self):
        outputs = {"persona_a": {"p_up": 0.33, "p_down": 0.33}}
        results = attribute_resolution("HOLD", "flat", outputs)
        assert results[0].credit > 0  # near-uniform -> flat credit

    def test_returns_all_personas(self):
        outputs = {"a": {"p_up": 0.5, "p_down": 0.3}, "b": {"p_up": 0.4, "p_down": 0.4}}
        results = attribute_resolution("BUY", "up", outputs)
        assert len(results) == 2


# ======================================================================
# extract_lesson_from_resolution
# ======================================================================

class TestExtractLesson:
    def test_failure_taxonomy_produces_lesson(self):
        lesson = extract_lesson_from_resolution(
            ticker="AAPL",
            thesis="Earnings beat expected",
            verdict="BUY",
            actual_outcome="down",
            failure_taxonomy="CATALYST_MISMATCH",
            cycle_id="c001",
        )
        assert lesson is not None
        assert lesson.lesson_type == "failure_pattern"
        assert "AAPL" in lesson.text
        assert "CATALYST_MISMATCH" in lesson.text

    def test_success_pattern_produces_lesson(self):
        lesson = extract_lesson_from_resolution(
            ticker="MSFT",
            thesis="Cloud growth accelerating",
            verdict="STRONG_BUY",
            actual_outcome="up",
            cycle_id="c002",
        )
        assert lesson is not None
        assert lesson.lesson_type == "success_pattern"
        assert "MSFT" in lesson.text

    def test_no_lesson_for_mediocre_outcome(self):
        lesson = extract_lesson_from_resolution(
            ticker="TSLA",
            thesis="Might go up",
            verdict="HOLD",
            actual_outcome="flat",
            cycle_id="c003",
        )
        assert lesson is None

    def test_unclassified_taxonomy_no_lesson(self):
        lesson = extract_lesson_from_resolution(
            ticker="NVDA",
            thesis="AI hype",
            verdict="BUY",
            actual_outcome="down",
            failure_taxonomy="UNCLASSIFIED",
            cycle_id="c004",
        )
        assert lesson is None


# ======================================================================
# compute_severity_multiplier
# ======================================================================

class TestSeverityMultiplier:
    def test_high_false_rate_reduces_multiplier(self):
        result = compute_severity_multiplier(1.0, 0.8, learning_rate=0.1)
        assert result < 1.0

    def test_low_false_rate_increases_multiplier(self):
        result = compute_severity_multiplier(1.0, 0.1, learning_rate=0.1)
        assert result > 1.0

    def test_clamped_at_floor(self):
        result = compute_severity_multiplier(0.5, 1.0, learning_rate=1.0)
        assert result == pytest.approx(0.5)

    def test_clamped_at_ceiling(self):
        result = compute_severity_multiplier(2.0, 0.0, learning_rate=1.0)
        assert result == pytest.approx(2.0)

    def test_zero_false_rate(self):
        result = compute_severity_multiplier(1.0, 0.0, learning_rate=0.1)
        assert result == pytest.approx(1.05)


# ======================================================================
# cluster_overrides
# ======================================================================

class TestClusterOverrides:
    def test_single_override(self):
        overrides = [{"from_verdict": "SKIP", "to_verdict": "BUY", "ticker": "AAPL"}]
        clusters = cluster_overrides(overrides)
        assert len(clusters) == 1
        assert clusters[0].count == 1
        assert "AAPL" in clusters[0].tickers

    def test_multiple_same_direction(self):
        overrides = [
            {"from_verdict": "SKIP", "to_verdict": "BUY", "ticker": "AAPL"},
            {"from_verdict": "SKIP", "to_verdict": "BUY", "ticker": "MSFT"},
            {"from_verdict": "SKIP", "to_verdict": "BUY", "ticker": "GOOG"},
        ]
        clusters = cluster_overrides(overrides)
        assert len(clusters) == 1
        assert clusters[0].count == 3
        assert len(clusters[0].tickers) == 3

    def test_multiple_directions(self):
        overrides = [
            {"from_verdict": "SKIP", "to_verdict": "BUY", "ticker": "AAPL"},
            {"from_verdict": "BUY", "to_verdict": "SKIP", "ticker": "TSLA"},
        ]
        clusters = cluster_overrides(overrides)
        assert len(clusters) == 2

    def test_empty_overrides(self):
        clusters = cluster_overrides([])
        assert clusters == []


# ======================================================================
# FlywheelHealth snapshot
# ======================================================================

class TestFlywheelHealth:
    def test_snapshot_creation(self):
        snap = snapshot_health(
            rolling_brier_avg=0.25,
            rolling_sharpe=0.5,
            calibration_gap=0.05,
            active_mutations=2,
            pending_reviews=3,
            lessons_count=10,
        )
        assert isinstance(snap, FlywheelHealthSnapshot)
        assert snap.rolling_brier_avg == 0.25
        assert snap.rolling_sharpe == 0.5
        assert snap.active_mutations == 2
        assert snap.lessons_count == 10

    def test_default_values(self):
        snap = snapshot_health(
            rolling_brier_avg=0.3,
            rolling_sharpe=0.0,
            calibration_gap=0.1,
        )
        assert snap.active_mutations == 0
        assert snap.pending_reviews == 0
        assert snap.lessons_count == 0


# ======================================================================
# build_context_brief
# ======================================================================

class TestBuildContextBrief:
    def test_basic_brief(self):
        brief = build_context_brief(
            persona="macro",
            ticker="AAPL",
            regime="BULL",
            regime_confidence=0.75,
        )
        assert "BULL" in brief
        assert "75%" in brief

    def test_with_failures(self):
        failures = [
            {"taxonomy": "CATALYST_MISMATCH", "summary": "Earnings miss"},
            {"taxonomy": "REGIME_ERROR", "summary": "Wrong regime call"},
        ]
        brief = build_context_brief(
            persona="sector",
            ticker="MSFT",
            recent_failures=failures,
        )
        assert "CATALYST_MISMATCH" in brief
        assert "REGIME_ERROR" in brief

    def test_with_track_record(self):
        brief = build_context_brief(
            persona="technicals",
            ticker="GOOG",
            persona_brier=0.22,
            persona_cycle_count=10,
        )
        assert "0.220" in brief
        assert "10 cycles" in brief

    def test_track_record_skipped_below_threshold(self):
        """persona_cycle_count < 5 means no track record section."""
        brief = build_context_brief(
            persona="technicals",
            ticker="GOOG",
            persona_brier=0.22,
            persona_cycle_count=3,
        )
        assert "TRACK RECORD" not in brief

    def test_with_lessons(self):
        brief = build_context_brief(
            persona="macro",
            ticker="TSLA",
            recent_lessons=["Avoid earnings plays on TSLA", "Sector rotation risk"],
        )
        assert "Avoid earnings" in brief

    def test_truncation_at_200_words(self):
        long_lessons = ["word " * 100]
        long_failures = [{"taxonomy": f"TYPE_{i}", "summary": "x" * 200} for i in range(5)]
        brief = build_context_brief(
            persona="macro",
            ticker="AAPL",
            regime="BULL",
            regime_confidence=0.8,
            recent_failures=long_failures,
            persona_brier=0.3,
            persona_cycle_count=50,
            recent_lessons=long_lessons,
        )
        words = brief.replace("...", "").split()
        assert len(words) <= 202  # 200 + "..."
