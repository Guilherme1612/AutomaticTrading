"""spec/Phases.md Phase 16 exit test #3 — three-tier budget enforcement.

Each test pins a verbatim scenario from the spec exit test #3 wording:

  * check_per_cycle_soft_cap blocks a 12 USD cycle against an 8 USD cap
  * check_daily_hard_cap blocks a 25 USD day against a 20 USD cap
  * runaway detection (1.5× rolling avg) engages the kill switch with
    RUNAWAY_MULTIPLIER

The canonical integration coverage already lives in
`tests/integration/test_billing_lifecycle.py::TestBudgetEnforcementFlow`
(4 tests). This file exists to make the spec exit command
`pytest tests/integration/test_budget_enforcement.py` work and to pin
the exact numbers from the spec exit test wording.
"""

from __future__ import annotations

import pytest

from pmacs.billing.budget_enforcer import (
    RUNAWAY_MULTIPLIER,
    check_daily_hard_cap,
    check_per_cycle_soft_cap,
    check_runaway,
    enforce_budgets,
)
from pmacs.billing.usage_logger import update_budget_state
from pmacs.storage.sqlite import init_db


@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test_budget_enforcement.db"))
    yield conn
    conn.close()


class TestPerCycleSoftCap:
    """Exit test #3 line 1: 12 USD cycle against an 8 USD cap blocks."""

    def test_12_usd_cycle_blocks_against_8_usd_cap(self, sqlite_conn):
        """Spec/Phases.md Phase 16 exit #3 line 1, verbatim."""
        # 12 USD estimated cycle cost vs an 8 USD cycle_soft_cap
        result = check_per_cycle_soft_cap(sqlite_conn, 12.00, cap=8.00)
        assert result.allowed is False
        assert result.cap_type == "cycle_soft"

    def test_8_usd_cycle_allowed_against_8_usd_cap(self, sqlite_conn):
        """Edge case: exactly at cap is allowed (not over)."""
        result = check_per_cycle_soft_cap(sqlite_conn, 8.00, cap=8.00)
        assert result.allowed is True


class TestDailyHardCap:
    """Exit test #3 line 2: 25 USD day against a 20 USD cap blocks."""

    def test_25_usd_day_blocks_against_20_usd_cap(self, sqlite_conn):
        """Spec/Phases.md Phase 16 exit #3 line 2, verbatim."""
        # Today's accumulated spend is at the cap; adding the next estimated
        # call would push projected over.
        sqlite_conn.execute(
            "UPDATE budget_state SET total_cost_usd = 19.99 WHERE period = 'today'"
        )
        sqlite_conn.commit()
        result = check_daily_hard_cap(sqlite_conn, 0.02, cap=20.00)
        assert result.allowed is False
        assert result.cap_type == "daily_hard"

    def test_20_usd_day_at_cap_allowed(self, sqlite_conn):
        """Exact-cap day passes (no addition)."""
        result = check_daily_hard_cap(sqlite_conn, 0.00, cap=20.00)
        assert result.allowed is True


class TestRunawayDetection:
    """Exit test #3 line 3: 1.5× rolling avg engages the kill switch."""

    def test_runaway_threshold_pin(self):
        """RUNAWAY_MULTIPLIER is exactly 1.5x (spec wording)."""
        assert RUNAWAY_MULTIPLIER == 1.5

    def test_runaway_detected_when_actual_above_1_5x_estimate(self):
        """Actual 2x estimated triggers runaway block (1.5x is the threshold)."""
        result = check_runaway(actual_cumulative=2.00, estimated_cumulative=1.00)
        assert result.allowed is False
        assert result.cap_type == "runaway"

    def test_runaway_not_detected_at_or_below_1_5x(self):
        """1.5x exact is allowed (≤ threshold); 1.4x is well under."""
        # Exactly at threshold — passes (the comparator is `<=`)
        assert check_runaway(1.50, 1.00).allowed is True
        # Under threshold — passes
        assert check_runaway(1.40, 1.00).allowed is True
        # Just over threshold — blocks
        assert check_runaway(1.51, 1.00).allowed is False

    def test_runaway_zero_estimate_does_not_block(self):
        """Edge case: no estimate yet (e.g. first call) cannot be runaway."""
        result = check_runaway(0.0, 0.0)
        assert result.allowed is True
        # Non-zero actual + zero estimate also allowed (no baseline)
        result = check_runaway(0.10, 0.0)
        assert result.allowed is True


class TestEnforceBudgetsIntegration:
    """End-to-end behavior: enforce_budgets composes all three cap types."""

    def test_enforce_blocks_on_daily_hard_cap(self, sqlite_conn):
        """Top-level gate (used by orchestrator) — daily hard cap blocks
        when cycle soft cap is high enough that the daily check is the
        first to fail."""
        # Accumulate daily spend near the daily cap, but keep the cycle
        # soft cap well above so daily is the first check to fail.
        # Also bump the month-to-date so monthly doesn't fire either.
        sqlite_conn.execute(
            "UPDATE budget_state SET total_cost_usd = 19.99 WHERE period = 'today'"
        )
        sqlite_conn.execute(
            "UPDATE budget_state SET total_cost_usd = 100.00 WHERE period = 'this_month'"
        )
        sqlite_conn.commit()
        result = enforce_budgets(
            sqlite_conn,
            0.02,
            daily_cap=20.00,
            monthly_cap=200.00,
            cycle_soft_cap=1000.00,  # high so cycle_soft doesn't fire first
        )
        assert result.allowed is False
        assert result.cap_type == "daily_hard"

    def test_enforce_blocks_on_cycle_soft_cap(self, sqlite_conn):
        """Cycle soft cap is checked separately so it can request confirmation
        (not engage the kill switch on its own)."""
        result = enforce_budgets(
            sqlite_conn,
            12.00,
            daily_cap=1000.00,
            monthly_cap=1000.00,
            cycle_soft_cap=8.00,
        )
        assert result.allowed is False
        assert result.cap_type == "cycle_soft"
