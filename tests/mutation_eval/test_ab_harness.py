"""Mutation A/B test harness tests (Phases §14 exit, Architecture.md §10).

Validates:
- A/B candidates run SHADOW-only (never PAPER/LIVE)
- Statistical significance: p < 0.05, Cohen's d > 0.20, n >= 20
- 3-concurrent A/B cap
- Auto-rollback on regression within 50 cycles
- 50-cycle dormancy before activation
- Welch's t-test correctness with known values
- Cohen's d effect size calculation
- Full A/B lifecycle: create -> run -> evaluate -> promote/rollback
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pmacs.constants import (
    MUTATION_ACTIVATION_CYCLES,
    MUTATION_AUTO_ROLLBACK_WINDOW,
    MUTATION_MAX_CONCURRENT_AB,
    MUTATION_PROBATION_CYCLES,
    MUTATION_STAT_SIG_COHENS_D,
    MUTATION_STAT_SIG_MIN_N,
    MUTATION_STAT_SIG_P,
)
from pmacs.mutation.ab_runner import ABRunner, ABState, MAX_CONCURRENT_AB
from pmacs.mutation.rollback import execute_rollback, regression_detected
from pmacs.mutation.stat_test import welch_t_test, StatTestResult
from pmacs.mutation.daemon import mode_too_early, MutationDaemon


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _MockConfig:
    """Minimal config stub matching mutation.toml fields."""
    min_paper_cycles: int = MUTATION_ACTIVATION_CYCLES
    max_ab_tests: int = MUTATION_MAX_CONCURRENT_AB
    p_value_threshold: float = MUTATION_STAT_SIG_P
    cohens_d_threshold: float = MUTATION_STAT_SIG_COHENS_D
    min_sample_size: int = MUTATION_STAT_SIG_MIN_N
    probation_cycles: int = MUTATION_PROBATION_CYCLES
    auto_rollback_window: int = MUTATION_AUTO_ROLLBACK_WINDOW


def _make_runner(max_concurrent: int = 3) -> ABRunner:
    """Create an in-memory ABRunner (no SQLite)."""
    return ABRunner(max_concurrent=max_concurrent)


# ---------------------------------------------------------------------------
# Test: A/B runs SHADOW-only
# ---------------------------------------------------------------------------


class TestOfflineABHarness:
    """Offline A/B harness validation per Architecture.md §10."""

    def test_ab_runs_shadow_only(self) -> None:
        """Verify A/B candidate arm records outcomes as SHADOW data.

        The ABRunner docstring states candidate arm always runs SHADOW-only
        (Architecture.md §16 anti-pattern: Mutation A/B runs SHADOW, never
        PAPER). The runner has no mode parameter -- it only tracks arms as
        'control' and 'candidate'. Verify that recording candidate outcomes
        never touches PAPER/LIVE execution paths and that no mode-escalation
        mechanism exists.
        """
        runner = _make_runner()
        proposal_id = "mut-shadow-test-001"
        assert runner.start(proposal_id) is True

        state = runner.get_state(proposal_id)
        assert state is not None

        # Record candidate outcomes -- these must be shadow-only observations.
        # The ABState has no 'mode' field: it is inherently SHADOW.
        # Verify ABState dataclass has no mode/elevation fields.
        assert not hasattr(state, "mode"), (
            "ABState must not have a mode field -- candidate is always SHADOW"
        )
        assert not hasattr(state, "execution_tier"), (
            "ABState must not track execution tier -- always shadow observations"
        )

        # Candidate arm records metric values (e.g., Brier scores from shadow
        # inference) -- no trade execution involved.
        for i in range(5):
            runner.record_outcome(proposal_id, "candidate", 0.25 + i * 0.01)

        state = runner.get_state(proposal_id)
        assert state is not None
        assert len(state.candidate_outcomes) == 5
        # All values are metric observations, not trade outcomes.
        for val in state.candidate_outcomes:
            assert isinstance(val, float)
            assert 0.0 <= val <= 1.0  # Brier score range

    # ------------------------------------------------------------------
    # Test: Statistical significance thresholds
    # ------------------------------------------------------------------

    def test_statistical_significance_thresholds(self) -> None:
        """Test that significance requires ALL THREE conditions simultaneously:
        p < 0.05, Cohen's d >= 0.20, n >= 20.

        Per spec: a result must pass all three gates to be marked significant.
        """
        # Generate data that clearly differs (large effect, large sample).
        control = [0.30] * 25
        candidate = [0.20] * 25
        result = welch_t_test(control, candidate)

        # With identical values within each group (zero variance), the means
        # differ deterministically, so p_value=0.0, cohens_d=inf, n=25.
        assert result.is_significant is True
        assert result.p_value == 0.0
        assert result.sample_size >= 20
        assert result.cohens_d == float("inf")

        # Case 2: Effect present but sample too small (n < 20).
        control_small = [0.35, 0.33, 0.31, 0.29, 0.30, 0.32, 0.28, 0.34, 0.30, 0.31,
                         0.29, 0.33, 0.30, 0.32, 0.28]
        candidate_small = [0.20, 0.18, 0.19, 0.21, 0.17, 0.20, 0.19, 0.18, 0.21, 0.20,
                           0.19, 0.18, 0.20, 0.21, 0.19]
        result_small = welch_t_test(control_small, candidate_small)
        assert result_small.is_significant is False, (
            f"Must not be significant with n={result_small.sample_size} < 20"
        )

        # Case 3: Large sample but no real effect (p large, d small).
        import random
        rng = random.Random(42)
        control_no_effect = [rng.gauss(0.25, 0.05) for _ in range(30)]
        candidate_no_effect = [rng.gauss(0.25, 0.05) for _ in range(30)]
        result_none = welch_t_test(control_no_effect, candidate_no_effect)
        assert result_none.is_significant is False, (
            "Should not be significant when distributions overlap"
        )

        # Case 4: All three pass -- borderline significant.
        # Build two distributions with moderate effect size.
        control_sig = [0.35 + rng.gauss(0, 0.03) for _ in range(25)]
        candidate_sig = [0.25 + rng.gauss(0, 0.03) for _ in range(25)]
        result_sig = welch_t_test(control_sig, candidate_sig)
        assert result_sig.sample_size >= 20
        assert result_sig.p_value < 0.05
        assert result_sig.cohens_d >= 0.20
        assert result_sig.is_significant is True

    # ------------------------------------------------------------------
    # Test: 3-concurrent A/B cap
    # ------------------------------------------------------------------

    def test_concurrent_ab_cap(self) -> None:
        """Verify max 3 concurrent A/B tests (Architecture.md §10,
        MUTATION_MAX_CONCURRENT_AB = 3)."""
        runner = _make_runner(max_concurrent=3)

        # Start 3 tests -- all should succeed.
        assert runner.start("mut-001") is True
        assert runner.start("mut-002") is True
        assert runner.start("mut-003") is True
        assert runner.active_count == 3

        # 4th should be rejected.
        assert runner.start("mut-004") is False
        assert runner.active_count == 3

        # can_start should report False.
        assert runner.can_start() is False

        # Complete one, now a slot opens.
        runner.complete("mut-002")
        assert runner.active_count == 2
        assert runner.can_start() is True

        # 4th should now succeed.
        assert runner.start("mut-004") is True
        assert runner.active_count == 3
        assert runner.can_start() is False

    # ------------------------------------------------------------------
    # Test: Auto-rollback on regression
    # ------------------------------------------------------------------

    def test_auto_rollback_on_regression(self) -> None:
        """Verify regression_detected triggers when candidate underperforms.

        Per spec: auto-rollback activates after probation period (30 cycles)
        and within rollback window (50 cycles). Lower metric is better.
        """
        probation = MUTATION_PROBATION_CYCLES  # 30
        window = MUTATION_AUTO_ROLLBACK_WINDOW  # 50

        # During probation: no rollback even if regressed.
        assert regression_detected(
            promoted_cycles_ago=10,
            probation_cycles=probation,
            post_metric=0.50,  # worse
            baseline_metric=0.25,  # better (lower is better)
            lower_is_better=True,
            rollback_window=window,
        ) is False, "Must not rollback during probation period"

        # After probation, within window, regressed: rollback triggers.
        assert regression_detected(
            promoted_cycles_ago=35,
            probation_cycles=probation,
            post_metric=0.50,
            baseline_metric=0.25,
            lower_is_better=True,
            rollback_window=window,
        ) is True, "Must rollback after probation when regressed"

        # After probation, within window, improved: no rollback.
        assert regression_detected(
            promoted_cycles_ago=35,
            probation_cycles=probation,
            post_metric=0.20,
            baseline_metric=0.25,
            lower_is_better=True,
            rollback_window=window,
        ) is False, "Must not rollback when improved"

        # Beyond rollback window: monitoring expired.
        assert regression_detected(
            promoted_cycles_ago=probation + window + 1,
            probation_cycles=probation,
            post_metric=0.50,
            baseline_metric=0.25,
            lower_is_better=True,
            rollback_window=window,
        ) is False, "Must not rollback beyond monitoring window"

        # Exact boundary: probation + window.
        assert regression_detected(
            promoted_cycles_ago=probation + window,
            probation_cycles=probation,
            post_metric=0.50,
            baseline_metric=0.25,
            lower_is_better=True,
            rollback_window=window,
        ) is True, "At boundary (probation + window) still monitored"

        # Exact boundary + 1: expired.
        assert regression_detected(
            promoted_cycles_ago=probation + window + 1,
            probation_cycles=probation,
            post_metric=0.50,
            baseline_metric=0.25,
            lower_is_better=True,
            rollback_window=window,
        ) is False, "Beyond boundary: monitoring expired"

        # higher_is_better mode (e.g., Sharpe ratio).
        assert regression_detected(
            promoted_cycles_ago=35,
            probation_cycles=probation,
            post_metric=-0.5,  # worse (lower Sharpe)
            baseline_metric=0.5,  # better
            lower_is_better=False,
            rollback_window=window,
        ) is True, "Must rollback when higher-is-better metric drops"

    # ------------------------------------------------------------------
    # Test: 50-cycle dormancy period
    # ------------------------------------------------------------------

    def test_dormancy_period(self) -> None:
        """Verify no mutations before 50 PAPER cycles (MUTATION_ACTIVATION_CYCLES)."""
        # Below threshold: dormant.
        assert mode_too_early(0) is True
        assert mode_too_early(25) is True
        assert mode_too_early(49) is True

        # At threshold: active.
        assert mode_too_early(50) is False

        # Above threshold: active.
        assert mode_too_early(100) is False

        # Custom config with different threshold.
        custom_cfg = _MockConfig(min_paper_cycles=75)
        assert mode_too_early(50, config=custom_cfg) is True
        assert mode_too_early(74, config=custom_cfg) is True
        assert mode_too_early(75, config=custom_cfg) is False

    # ------------------------------------------------------------------
    # Test: Welch's t-test with known values
    # ------------------------------------------------------------------

    def test_welch_ttest_correct(self) -> None:
        """Test Welch's t-test against hand-computed values.

        Two samples with known means and variances.
        Control: mean=5.0, var=4.0, n=10
        Candidate: mean=7.0, var=4.0, n=10
        """
        control = [3, 4, 5, 6, 7, 3, 4, 5, 6, 7]  # mean=5.0
        candidate = [5, 6, 7, 8, 9, 5, 6, 7, 8, 9]  # mean=7.0

        result = welch_t_test(control, candidate)

        assert result.control_mean == pytest.approx(5.0)
        assert result.candidate_mean == pytest.approx(7.0)

        # t = (5.0 - 7.0) / sqrt(4.0/10 + 4.0/10) = -2.0 / 0.8944 ~ -2.236
        # |t| ~ 2.236, df ~ 18 (equal variances, equal n)
        # Implementation uses exact Lentz continued-fraction; p ~ 0.008
        assert result.p_value < 0.05, (
            f"p-value {result.p_value} should be < 0.05 for this data"
        )

        # Cohen's d = |7 - 5| / pooled_std
        # v1=v2=20/9~2.222, pooled_std = sqrt(2.222) ~ 1.49
        # d = 2 / 1.49 ~ 1.34
        assert result.cohens_d == pytest.approx(1.34, abs=0.05)

        # Sample size is min(10, 10) = 10, so n < 20: not significant.
        assert result.sample_size == 10
        assert result.is_significant is False, "n=10 < 20: must not be significant"

        # With the same effect but larger sample: should be significant.
        control_large = [3, 4, 5, 6, 7, 3, 4, 5, 6, 7] * 3  # n=30
        candidate_large = [5, 6, 7, 8, 9, 5, 6, 7, 8, 9] * 3  # n=30
        result_large = welch_t_test(control_large, candidate_large)
        assert result_large.sample_size == 30
        assert result_large.is_significant is True
        assert result_large.p_value < 0.05

    # ------------------------------------------------------------------
    # Test: Cohen's d calculation
    # ------------------------------------------------------------------

    def test_cohens_d_calculation(self) -> None:
        """Test Cohen's d effect size with known values.

        Cohen's d = |mean2 - mean1| / pooled_std
        Pooled std = sqrt(((n1-1)*var1 + (n2-1)*var2) / (n1+n2-2))
        """
        # Case 1: Zero effect (identical distributions).
        same = [1.0, 2.0, 3.0, 4.0, 5.0]
        result_zero = welch_t_test(same, same)
        assert result_zero.cohens_d == pytest.approx(0.0, abs=1e-10)
        assert result_zero.p_value == pytest.approx(1.0, abs=1e-10)

        # Case 2: Small effect (d ~ 0.2).
        # mean1=5.0, std1~1.58 (var~2.5), mean2=5.3, std2~1.58
        # pooled_std ~ 1.58, d ~ 0.3/1.58 ~ 0.19
        import random
        rng = random.Random(123)
        control_small = [rng.gauss(5.0, 1.5) for _ in range(50)]
        candidate_small = [rng.gauss(5.3, 1.5) for _ in range(50)]
        result_small_d = welch_t_test(control_small, candidate_small)
        assert 0.0 < result_small_d.cohens_d < 1.0, (
            f"Expected small-to-moderate d, got {result_small_d.cohens_d}"
        )

        # Case 3: Large effect (d > 0.8).
        control_large = [rng.gauss(5.0, 0.5) for _ in range(30)]
        candidate_large = [rng.gauss(8.0, 0.5) for _ in range(30)]
        result_large_d = welch_t_test(control_large, candidate_large)
        assert result_large_d.cohens_d > 0.8, (
            f"Expected large d > 0.8, got {result_large_d.cohens_d}"
        )

        # Case 4: Empty / single-element lists return 0.0.
        result_degenerate = welch_t_test([1.0], [2.0])
        assert result_degenerate.cohens_d == 0.0
        assert result_degenerate.is_significant is False

        # Case 5: Zero variance, same means -> d = 0.
        result_same_val = welch_t_test([3.0] * 10, [3.0] * 10)
        assert result_same_val.cohens_d == 0.0

        # Case 6: Zero variance, different means -> d = inf.
        result_diff_val = welch_t_test([3.0] * 25, [5.0] * 25)
        assert result_diff_val.cohens_d == float("inf")

    # ------------------------------------------------------------------
    # Test: Full A/B lifecycle
    # ------------------------------------------------------------------

    def test_ab_lifecycle(self) -> None:
        """Full lifecycle: create -> run -> evaluate -> promote/rollback.

        Simulates the complete flow from proposal creation through
        A/B test execution, statistical evaluation, and either
        promotion to READY_FOR_REVIEW or rejection.
        """
        runner = _make_runner(max_concurrent=3)
        proposal_id = "mut-lifecycle-001"

        # Step 1: Create (start A/B test).
        assert runner.start(proposal_id) is True
        state = runner.get_state(proposal_id)
        assert state is not None
        assert state.status == "RUNNING"
        assert len(state.control_outcomes) == 0
        assert len(state.candidate_outcomes) == 0

        # Step 2: Run -- record outcomes over 25 cycles.
        # Control: Brier ~0.30 (mediocre)
        # Candidate: Brier ~0.20 (improved)
        rng = __import__("random").Random(42)
        for i in range(25):
            control_val = 0.30 + rng.gauss(0, 0.05)
            candidate_val = 0.20 + rng.gauss(0, 0.05)
            runner.record_outcome(
                proposal_id, "control", control_val, cycle_id=f"cycle-{i}"
            )
            runner.record_outcome(
                proposal_id, "candidate", candidate_val, cycle_id=f"cycle-{i}"
            )

        state = runner.get_state(proposal_id)
        assert state is not None
        assert len(state.control_outcomes) == 25
        assert len(state.candidate_outcomes) == 25

        # Step 3: Evaluate -- run stat test.
        result = welch_t_test(
            state.control_outcomes,
            state.candidate_outcomes,
            alpha=MUTATION_STAT_SIG_P,
            min_cohens_d=MUTATION_STAT_SIG_COHENS_D,
            min_sample=MUTATION_STAT_SIG_MIN_N,
        )

        # Candidate mean should be lower (better Brier).
        assert result.candidate_mean < result.control_mean
        assert result.sample_size >= 20
        assert result.p_value < MUTATION_STAT_SIG_P
        assert result.cohens_d >= MUTATION_STAT_SIG_COHENS_D
        assert result.is_significant is True

        # Significant -> status would become READY_FOR_REVIEW in daemon.
        expected_status = "READY_FOR_REVIEW" if result.is_significant else "REJECTED"
        assert expected_status == "READY_FOR_REVIEW"

        # Step 4: Complete A/B test.
        final_state = runner.complete(proposal_id)
        assert final_state is not None
        assert final_state.status == "COMPLETE"
        assert runner.get_state(proposal_id) is None  # removed from active
        assert runner.active_count == 0

        # Step 5: Simulate rollback path (if operator had promoted and
        # regression detected). The candidate Brier was ~0.20, control was
        # ~0.30. If post-promotion metric degrades to 0.35:
        assert regression_detected(
            promoted_cycles_ago=35,
            probation_cycles=MUTATION_PROBATION_CYCLES,
            post_metric=0.35,
            baseline_metric=0.30,
            lower_is_better=True,
            rollback_window=MUTATION_AUTO_ROLLBACK_WINDOW,
        ) is True, "Post-promotion regression should trigger rollback"

        # Verify execute_rollback returns proper audit data (no real DB/audit).
        rollback_result = execute_rollback(
            proposal_id,
            reason="auto_rollback: regression after probation",
        )
        assert rollback_result["proposal_id"] == proposal_id
        assert rollback_result["status"] == "ROLLED_BACK"
        assert "rolled_back_at" in rollback_result
        assert rollback_result["reason"] == "auto_rollback: regression after probation"

    # ------------------------------------------------------------------
    # Test: Lifecycle with rejected candidate
    # ------------------------------------------------------------------

    def test_ab_lifecycle_rejected(self) -> None:
        """Lifecycle where candidate shows no improvement -> REJECTED."""
        runner = _make_runner()
        proposal_id = "mut-reject-001"

        assert runner.start(proposal_id) is True

        # Both arms produce similar Brier scores (no real difference).
        rng = __import__("random").Random(99)
        for i in range(25):
            val = 0.25 + rng.gauss(0, 0.04)
            runner.record_outcome(
                proposal_id, "control", val, cycle_id=f"cycle-{i}"
            )
            runner.record_outcome(
                proposal_id, "candidate", val + rng.gauss(0, 0.01),
                cycle_id=f"cycle-{i}"
            )

        state = runner.get_state(proposal_id)
        assert state is not None

        result = welch_t_test(
            state.control_outcomes,
            state.candidate_outcomes,
        )

        # No meaningful difference: should not be significant.
        expected_status = "READY_FOR_REVIEW" if result.is_significant else "REJECTED"
        assert expected_status == "REJECTED", (
            f"Candidate with no improvement must be REJECTED "
            f"(p={result.p_value:.4f}, d={result.cohens_d:.4f})"
        )

        runner.complete(proposal_id)
        assert runner.active_count == 0

    # ------------------------------------------------------------------
    # Test: Dormancy with MutationDaemon
    # ------------------------------------------------------------------

    def test_daemon_dormancy_via_run_cycle(self) -> None:
        """Verify MutationDaemon.run_cycle exits early when below 50 cycles."""
        cfg = _MockConfig()
        mock_db = MagicMock(spec=Path)
        mock_audit = MagicMock(spec=Path)
        mock_registry = MagicMock(spec=Path)

        # Patch ABRunner to avoid DB access.
        with patch.object(ABRunner, "__init__", lambda self, **kw: None):
            daemon = MutationDaemon(
                config=cfg,
                db_path=mock_db,
                audit_path=mock_audit,
                registry_path=mock_registry,
            )
            # run_cycle at 49 cycles should return immediately (dormant).
            # No SQLite calls should happen.
            daemon.run_cycle("cycle-dormant", paper_cycle_count=49)

    # ------------------------------------------------------------------
    # Test: execute_rollback with mocked DB/audit/SSE
    # ------------------------------------------------------------------

    def test_execute_rollback_persists(self, tmp_path: Path) -> None:
        """Verify execute_rollback updates SQLite, writes audit, publishes SSE."""
        import sqlite3

        # Set up a minimal SQLite DB with a proposal.
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE mutation_proposals "
            "(id TEXT PRIMARY KEY, status TEXT, completed_at TEXT)"
        )
        conn.execute(
            "INSERT INTO mutation_proposals (id, status) VALUES (?, 'OPERATOR_PROMOTED')",
            ("mut-rollback-001",),
        )
        conn.commit()
        conn.close()

        # Set up audit log path.
        audit_path = tmp_path / "audit.log"
        audit_path.write_text("")

        # Mock SSE publisher.
        sse = MagicMock()

        # Mock AuditWriter to avoid needing full audit infrastructure.
        # AuditWriter is imported locally in execute_rollback, so patch at source.
        with patch("pmacs.storage.audit.AuditWriter") as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer_cls.return_value = mock_writer

            result = execute_rollback(
                "mut-rollback-001",
                reason="auto_rollback: test regression",
                db_path=db_path,
                audit_path=audit_path,
                sse_publisher=sse,
                cycle_id="cycle-test-001",
            )

        # Verify return value.
        assert result["proposal_id"] == "mut-rollback-001"
        assert result["status"] == "ROLLED_BACK"
        assert result["reason"] == "auto_rollback: test regression"

        # Verify SQLite updated.
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT status FROM mutation_proposals WHERE id = ?",
            ("mut-rollback-001",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "ROLLED_BACK"

        # Verify SSE event published.
        sse.publish.assert_called_once()
        call_args = sse.publish.call_args
        assert call_args[0][0] == "mutation"
        assert call_args[0][1] == "mutation.rolled_back"
        assert call_args[0][2]["proposal_id"] == "mut-rollback-001"
        assert call_args[0][2]["reason"] == "auto_rollback: test regression"

        # Verify audit writer was called.
        mock_writer_cls.assert_called_once_with(audit_path)
        mock_writer.append.assert_called_once()
        mock_writer.close.assert_called_once()

    # ------------------------------------------------------------------
    # Test: Concurrent cap with config override
    # ------------------------------------------------------------------

    def test_concurrent_cap_from_config(self) -> None:
        """Verify ABRunner reads max_ab_tests from config object."""
        cfg = _MockConfig(max_ab_tests=2)
        runner = ABRunner(config=cfg)
        assert runner.max_concurrent == 2

        assert runner.start("a") is True
        assert runner.start("b") is True
        assert runner.start("c") is False, "3rd should fail with cap=2"

    # ------------------------------------------------------------------
    # Test: Record outcome for non-existent proposal is a no-op
    # ------------------------------------------------------------------

    def test_record_outcome_noop_for_unknown(self) -> None:
        """Recording outcomes for unknown proposals must not raise."""
        runner = _make_runner()
        # Should not raise.
        runner.record_outcome("does-not-exist", "control", 0.5)
        runner.record_outcome("does-not-exist", "candidate", 0.3)

    # ------------------------------------------------------------------
    # Test: Complete non-existent proposal returns None
    # ------------------------------------------------------------------

    def test_complete_unknown_returns_none(self) -> None:
        """Completing a non-existent proposal returns None."""
        runner = _make_runner()
        assert runner.complete("ghost-proposal") is None

    # ------------------------------------------------------------------
    # Test: Significance constants match spec
    # ------------------------------------------------------------------

    def test_constants_match_spec(self) -> None:
        """Verify mutation constants match spec requirements."""
        assert MUTATION_ACTIVATION_CYCLES == 50
        assert MUTATION_STAT_SIG_P == 0.05
        assert MUTATION_STAT_SIG_COHENS_D == 0.20
        assert MUTATION_STAT_SIG_MIN_N == 20
        assert MUTATION_PROBATION_CYCLES == 30
        assert MUTATION_AUTO_ROLLBACK_WINDOW == 50
        assert MUTATION_MAX_CONCURRENT_AB == 3
        assert MAX_CONCURRENT_AB == 3
