"""Unit tests for budget_enforcer — three-tier cap checks."""

import pytest

from pmacs.billing.budget_enforcer import (
    check_daily_hard_cap,
    check_monthly_hard_cap,
    check_per_cycle_soft_cap,
    check_runaway,
    enforce_budgets,
)
from pmacs.storage.sqlite import init_db


@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    yield conn
    conn.close()


class TestPerCycleSoftCap:
    def test_under_cap_allowed(self, sqlite_conn):
        result = check_per_cycle_soft_cap(sqlite_conn, 0.50, cap=1.00)
        assert result.allowed is True

    def test_over_cap_blocked(self, sqlite_conn):
        result = check_per_cycle_soft_cap(sqlite_conn, 1.50, cap=1.00)
        assert result.allowed is False
        assert "cycle_soft" in result.cap_type

    def test_exact_cap_allowed(self, sqlite_conn):
        result = check_per_cycle_soft_cap(sqlite_conn, 1.00, cap=1.00)
        assert result.allowed is True


class TestDailyHardCap:
    def test_under_cap_allowed(self, sqlite_conn):
        result = check_daily_hard_cap(sqlite_conn, 0.01, cap=2.00)
        assert result.allowed is True

    def test_over_cap_blocked(self, sqlite_conn):
        # Seed a high total
        sqlite_conn.execute("UPDATE budget_state SET total_cost_usd = 1.99 WHERE period = 'today'")
        sqlite_conn.commit()
        result = check_daily_hard_cap(sqlite_conn, 0.05, cap=2.00)
        assert result.allowed is False
        assert "daily_hard" in result.cap_type


class TestMonthlyHardCap:
    def test_under_cap_allowed(self, sqlite_conn):
        result = check_monthly_hard_cap(sqlite_conn, 0.01, cap=30.00)
        assert result.allowed is True

    def test_over_cap_blocked(self, sqlite_conn):
        sqlite_conn.execute("UPDATE budget_state SET total_cost_usd = 29.99 WHERE period = 'this_month'")
        sqlite_conn.commit()
        result = check_monthly_hard_cap(sqlite_conn, 0.05, cap=30.00)
        assert result.allowed is False
        assert "monthly_hard" in result.cap_type


class TestRunawayDetection:
    def test_no_runaway(self):
        result = check_runaway(0.05, 0.05)
        assert result.allowed is True

    def test_runaway_detected(self):
        result = check_runaway(0.10, 0.05)
        assert result.allowed is False
        assert "runaway" in result.cap_type

    def test_just_under_threshold(self):
        # 1.49x should pass
        result = check_runaway(0.149, 0.10)
        assert result.allowed is True

    def test_just_over_threshold(self):
        # 1.51x should fail
        result = check_runaway(0.151, 0.10)
        assert result.allowed is False

    def test_zero_estimated(self):
        result = check_runaway(0.01, 0.0)
        assert result.allowed is True


class TestEnforceBudgets:
    def test_all_pass(self, sqlite_conn):
        result = enforce_budgets(sqlite_conn, 0.01)
        assert result.allowed is True

    def test_daily_blocks(self, sqlite_conn):
        sqlite_conn.execute("UPDATE budget_state SET total_cost_usd = 1.99 WHERE period = 'today'")
        sqlite_conn.commit()
        result = enforce_budgets(sqlite_conn, 0.05, daily_cap=2.00, cycle_soft_cap=100.00)
        assert result.allowed is False
        assert "daily_hard" in result.cap_type
