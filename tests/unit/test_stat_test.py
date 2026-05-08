"""Unit tests for pmacs/mutation/stat_test.py — Welch's t-test."""
from __future__ import annotations

import math
import pytest

from pmacs.mutation.stat_test import welch_t_test, StatTestResult


class TestWelchTTestIdenticalDistributions:
    """Identical distributions should yield high p-value, near-zero Cohen's d."""

    def test_identical_data_high_p(self) -> None:
        control = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
        candidate = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
        result = welch_t_test(control, candidate)
        assert result.p_value > 0.5
        assert result.control_mean == pytest.approx(3.0)
        assert result.candidate_mean == pytest.approx(3.0)

    def test_identical_data_cohens_d_near_zero(self) -> None:
        control = [10.0] * 30
        candidate = [10.0] * 30
        result = welch_t_test(control, candidate)
        assert result.cohens_d == pytest.approx(0.0, abs=1e-9)

    def test_identical_data_not_significant(self) -> None:
        control = [1.0] * 30
        candidate = [1.0] * 30
        result = welch_t_test(control, candidate)
        assert result.is_significant is False


class TestWelchTTestDifferentDistributions:
    """Clearly different distributions should yield low p-value, large Cohen's d."""

    def test_different_means_low_p(self) -> None:
        control = [1.0] * 30
        candidate = [100.0] * 30
        result = welch_t_test(control, candidate)
        assert result.p_value < 0.05
        assert result.cohens_d > 0.20

    def test_different_means_significant(self) -> None:
        control = [1.0] * 30
        candidate = [100.0] * 30
        result = welch_t_test(control, candidate)
        assert result.is_significant is True
        assert result.sample_size >= 20

    def test_moderate_difference(self) -> None:
        import random
        random.seed(42)
        control = [random.gauss(0, 1) for _ in range(30)]
        candidate = [random.gauss(2, 1) for _ in range(30)]
        result = welch_t_test(control, candidate)
        assert result.cohens_d > 1.0  # large effect
        assert result.p_value < 0.05


class TestWelchTTestInsufficientSamples:
    """Too few samples should not be significant even with large differences."""

    def test_too_few_samples(self) -> None:
        control = [1.0, 2.0]
        candidate = [100.0, 200.0]
        result = welch_t_test(control, candidate)
        assert result.is_significant is False
        assert result.sample_size < 20

    def test_single_element_each(self) -> None:
        result = welch_t_test([1.0], [100.0])
        assert result.is_significant is False
        assert result.p_value == 1.0
        assert result.cohens_d == 0.0

    def test_empty_lists(self) -> None:
        result = welch_t_test([], [])
        assert result.is_significant is False
        assert result.p_value == 1.0

    def test_19_samples_not_enough(self) -> None:
        control = [1.0] * 19
        candidate = [100.0] * 19
        result = welch_t_test(control, candidate)
        assert result.is_significant is False  # n < 20


class TestWelchTTestSignificanceRequiresAllThree:
    """Significance requires p < 0.05 AND d >= 0.20 AND n >= 20."""

    def test_p_and_d_but_not_n(self) -> None:
        control = [1.0] * 10
        candidate = [100.0] * 10
        result = welch_t_test(control, candidate)
        assert result.p_value < 0.05
        assert result.cohens_d > 0.20
        assert result.sample_size < 20
        assert result.is_significant is False

    def test_p_and_n_but_not_d(self) -> None:
        # Very similar distributions — p might be high, but let's force low d
        import random
        random.seed(99)
        control = [random.gauss(0, 1) for _ in range(30)]
        candidate = [random.gauss(0.01, 1) for _ in range(30)]
        result = welch_t_test(control, candidate)
        # d should be tiny, so not significant regardless of p
        assert result.is_significant is False


class TestCohensDCalculation:
    """Cohen's d = |m2 - m1| / pooled_std."""

    def test_cohens_d_known_values(self) -> None:
        # control mean=0, candidate mean=2, both std=1, n=30
        # pooled_std = 1.0, d = 2.0
        import random
        random.seed(123)
        control = [random.gauss(0, 1) for _ in range(30)]
        candidate = [random.gauss(2, 1) for _ in range(30)]
        result = welch_t_test(control, candidate)
        assert result.cohens_d == pytest.approx(2.0, abs=0.3)

    def test_zero_variance(self) -> None:
        # All same value — zero variance, se=0
        control = [5.0] * 30
        candidate = [5.0] * 30
        result = welch_t_test(control, candidate)
        assert result.cohens_d == 0.0
        assert result.p_value == pytest.approx(1.0, abs=0.01)

    def test_means_in_result(self) -> None:
        result = welch_t_test([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
        assert result.control_mean == pytest.approx(2.0)
        assert result.candidate_mean == pytest.approx(5.0)
