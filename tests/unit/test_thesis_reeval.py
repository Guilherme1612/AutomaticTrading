"""Tests for thesis re-evaluation engine — weekly cadence, aging, conviction exits."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pmacs.engines.thesis_reeval import (
    ReEvalResult,
    check_thesis_aging,
    check_weekly_reeval,
    evaluate_thesis,
)


class TestWeeklyReEval:
    """Tests for check_weekly_reeval with last_reeval_at persistence."""

    def test_reeval_not_due_after_6_days(self):
        entry = date(2024, 1, 1)
        current = entry + timedelta(days=6)
        assert check_weekly_reeval(entry, current, 0.5) is False

    def test_reeval_due_at_7_days_from_entry_when_last_reeval_at_is_none(self):
        entry = date(2024, 1, 1)
        current = entry + timedelta(days=7)
        assert check_weekly_reeval(entry, current, 0.5, last_reeval_at=None) is True

    def test_reeval_due_7_days_after_last_reeval_at(self):
        entry = date(2024, 1, 1)
        last_reeval = entry + timedelta(days=14)
        # 7 days after last_reeval_at
        current = last_reeval + timedelta(days=7)
        assert check_weekly_reeval(entry, current, 0.5, last_reeval_at=last_reeval) is True

    def test_reeval_not_due_6_days_after_last_reeval_at(self):
        entry = date(2024, 1, 1)
        last_reeval = entry + timedelta(days=14)
        # Only 6 days after last_reeval_at
        current = last_reeval + timedelta(days=6)
        assert check_weekly_reeval(entry, current, 0.5, last_reeval_at=last_reeval) is False

    def test_reeval_at_day_14_from_entry_no_prior_reeval(self):
        entry = date(2024, 1, 1)
        current = entry + timedelta(days=14)
        assert check_weekly_reeval(entry, current, 0.5) is True


class TestThesisAging:
    """Tests for check_thesis_aging (90-day calendar, no reset)."""

    def test_aging_not_triggered_at_89_days(self):
        entry = date(2024, 1, 1)
        current = entry + timedelta(days=89)
        assert check_thesis_aging(entry, current) is False

    def test_aging_triggered_at_90_days(self):
        entry = date(2024, 1, 1)
        current = entry + timedelta(days=90)
        assert check_thesis_aging(entry, current) is True

    def test_aging_triggered_at_120_days(self):
        entry = date(2024, 1, 1)
        current = entry + timedelta(days=120)
        assert check_thesis_aging(entry, current) is True


class TestEvaluateThesis:
    """Tests for evaluate_thesis — aging review verdicts."""

    def test_aging_review_returns_validated(self):
        result = evaluate_thesis(
            current_conviction=0.6,
            crucible_severity=0.2,
            holding_pnl_pct=5.0,
            is_aging_review=True,
        )
        assert result.action == "VALIDATED"

    def test_aging_review_returns_exit_when_underwater_10pct(self):
        result = evaluate_thesis(
            current_conviction=0.6,
            crucible_severity=0.2,
            holding_pnl_pct=-15.0,
            is_aging_review=True,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"
        assert "underwater" in result.reason.lower() or "-15.0%" in result.reason

    def test_conviction_collapsed_exit(self):
        result = evaluate_thesis(
            current_conviction=0.05,
            crucible_severity=0.0,
            holding_pnl_pct=0.0,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"

    def test_crucible_severity_with_low_conviction_exit(self):
        result = evaluate_thesis(
            current_conviction=0.2,
            crucible_severity=0.9,
            holding_pnl_pct=0.0,
        )
        assert result.action == "EXIT_THESIS_INVALIDATED"

    def test_non_aging_underwater_does_not_exit(self):
        """Without is_aging_review, being underwater does NOT trigger exit."""
        result = evaluate_thesis(
            current_conviction=0.6,
            crucible_severity=0.2,
            holding_pnl_pct=-15.0,
            is_aging_review=False,
        )
        assert result.action == "VALIDATED"
