"""Unit tests for budget_enforcer — three-tier cap checks."""

from datetime import datetime, timedelta, timezone

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


class TestLazyRollover:
    """Lazy period rollover at read time — fixes the false-breach bug where spend
    accumulated across days in a never-rolled 'today' bucket tripped the daily cap.

    Root cause (2026-06-24): period_roller.check_and_roll had zero callers, so the
    'today' period_start stayed stuck at a prior day and real spend piled up until
    a single small call pushed projected over the $2 DAILY cap — engaging the kill
    switch on what was really ~8 days of ~$0.25/day spend (well under the daily cap).
    """

    def _set_stale_today(self, conn, total, days_ago=1):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        conn.execute(
            "UPDATE budget_state SET period_start = ?, total_cost_usd = ? WHERE period = 'today'",
            [yesterday, total],
        )
        conn.commit()

    def test_stale_period_rolls_over_so_small_call_does_not_breach(self, sqlite_conn):
        """Yesterday's $1.95 already in a stale 'today' bucket + a new $0.10 call
        on a fresh day must NOT breach — the lazy rollover resets the bucket first."""
        self._set_stale_today(sqlite_conn, 1.95)
        result = enforce_budgets(sqlite_conn, 0.10, daily_cap=2.00, cycle_soft_cap=100.00)
        assert result.allowed is True, f"false breach after rollover: {result.reason}"

    def test_rollover_resets_period_start_to_today(self, sqlite_conn):
        self._set_stale_today(sqlite_conn, 1.95)
        enforce_budgets(sqlite_conn, 0.10, daily_cap=2.00, cycle_soft_cap=100.00)
        ps = sqlite_conn.execute(
            "SELECT period_start FROM budget_state WHERE period = 'today'"
        ).fetchone()[0]
        assert ps == datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def test_rollover_archives_stale_spend_to_history(self, sqlite_conn):
        """Rolling a stale period must archive the accumulated spend to
        budget_history (not silently wipe it)."""
        self._set_stale_today(sqlite_conn, 1.95)
        enforce_budgets(sqlite_conn, 0.10, daily_cap=2.00, cycle_soft_cap=100.00)
        archived = sqlite_conn.execute(
            "SELECT total_cost_usd, period_type FROM budget_history WHERE period_type = 'day'"
        ).fetchall()
        assert any(abs(row[0] - 1.95) < 0.001 for row in archived), \
            f"stale spend not archived: {archived}"

    def test_same_day_accumulation_still_breaches(self, sqlite_conn):
        """Same-day spend must still trip the daily cap (rollover is a no-op when
        period_start is already today). Guards against the fix over-correcting."""
        # period_start is already today (seeded by init_db) — accumulate $1.95 today.
        sqlite_conn.execute("UPDATE budget_state SET total_cost_usd = 1.95 WHERE period = 'today'")
        sqlite_conn.commit()
        result = enforce_budgets(sqlite_conn, 0.10, daily_cap=2.00, cycle_soft_cap=100.00)
        assert result.allowed is False
        assert "daily_hard" in result.cap_type

    def test_rollover_is_idempotent(self, sqlite_conn):
        """Two consecutive reads with a stale period roll exactly once (the second
        is a no-op) — check_and_roll guards on period_start != today."""
        self._set_stale_today(sqlite_conn, 1.95)
        enforce_budgets(sqlite_conn, 0.01, daily_cap=2.00, cycle_soft_cap=100.00)
        # Second call: period_start is now today → no roll, spend accumulates on fresh day.
        before = sqlite_conn.execute(
            "SELECT total_cost_usd FROM budget_state WHERE period = 'today'"
        ).fetchone()[0]
        enforce_budgets(sqlite_conn, 0.01, daily_cap=2.00, cycle_soft_cap=100.00)
        after = sqlite_conn.execute(
            "SELECT total_cost_usd FROM budget_state WHERE period = 'today'"
        ).fetchone()[0]
        # Only the two $0.01 calls' spend is present (rollover reset the $1.95).
        assert before < 0.05 and after < 0.05, f"stale spend not rolled: before={before} after={after}"
