"""Unit tests for period_roller — daily and monthly budget rollover."""

import pytest

from pmacs.billing.period_roller import check_and_roll, roll_daily, roll_monthly
from pmacs.storage.sqlite import init_db


@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    yield conn
    conn.close()


class TestDailyRollover:
    def test_rollover_archives_and_resets(self, sqlite_conn):
        # Seed some cost
        sqlite_conn.execute("UPDATE budget_state SET total_cost_usd = 1.50, period_start = '2026-05-23' WHERE period = 'today'")
        sqlite_conn.commit()

        roll_daily(sqlite_conn)

        # Check today was reset
        row = sqlite_conn.execute(
            "SELECT total_cost_usd, period_start FROM budget_state WHERE period = 'today'"
        ).fetchone()
        assert row[0] == 0.0  # Reset to 0

        # Check history was archived
        hist = sqlite_conn.execute(
            "SELECT total_cost_usd, period_type FROM budget_history WHERE period_type = 'day'"
        ).fetchone()
        assert hist is not None
        assert hist[0] == 1.50

    def test_rollover_marks_breach(self, sqlite_conn):
        sqlite_conn.execute("UPDATE budget_state SET total_cost_usd = 3.00, cap_usd = 2.00, period_start = '2026-05-23' WHERE period = 'today'")
        sqlite_conn.commit()

        roll_daily(sqlite_conn)

        hist = sqlite_conn.execute(
            "SELECT breached FROM budget_history WHERE period_type = 'day'"
        ).fetchone()
        assert hist[0] == 1  # Breached


class TestMonthlyRollover:
    def test_rollover_archives_and_resets(self, sqlite_conn):
        sqlite_conn.execute("UPDATE budget_state SET total_cost_usd = 15.00, period_start = '2026-04-01' WHERE period = 'this_month'")
        sqlite_conn.commit()

        roll_monthly(sqlite_conn)

        row = sqlite_conn.execute(
            "SELECT total_cost_usd FROM budget_state WHERE period = 'this_month'"
        ).fetchone()
        assert row[0] == 0.0

        hist = sqlite_conn.execute(
            "SELECT total_cost_usd FROM budget_history WHERE period_type = 'month'"
        ).fetchone()
        assert hist[0] == 15.00


class TestCheckAndRoll:
    def test_no_roll_when_current(self, sqlite_conn):
        """No rollover when period_start matches current date."""
        # Budget state is seeded with today's date by init_db
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        sqlite_conn.execute("UPDATE budget_state SET period_start = ?, total_cost_usd = 5.00 WHERE period = 'today'", [today])
        sqlite_conn.commit()

        check_and_roll(sqlite_conn)

        row = sqlite_conn.execute("SELECT total_cost_usd FROM budget_state WHERE period = 'today'").fetchone()
        assert row[0] == 5.00  # Not rolled
