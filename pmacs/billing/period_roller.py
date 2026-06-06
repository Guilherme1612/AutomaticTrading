"""Period roller — UTC midnight and month-boundary budget rollover.

PRD §8.5: Archive today's row into budget_history, reset totals.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pmacs.logsys import log_debug


def roll_daily(sqlite_conn) -> None:
    """Archive today's budget state into history and reset for new day."""
    row = sqlite_conn.execute(
        "SELECT period_start, total_cost_usd, cap_usd FROM budget_state WHERE period = 'today'"
    ).fetchone()
    if row is None:
        return

    period_start, total_cost, cap = row
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Archive
    breached = 1 if total_cost > cap else 0
    sqlite_conn.execute(
        "INSERT OR REPLACE INTO budget_history (period_start, period_end, period_type, total_cost_usd, cap_usd, breached) "
        "VALUES (?, ?, 'day', ?, ?, ?)",
        [period_start, today, total_cost, cap, breached],
    )

    # Reset
    sqlite_conn.execute(
        "UPDATE budget_state SET total_cost_usd = 0, period_start = ?, updated_at = ? WHERE period = 'today'",
        [today, now.isoformat()],
    )
    sqlite_conn.commit()

    log_debug(
        "BUDGET_ROLLOVER",
        payload={"period_type": "day", "archived_cost": total_cost, "breached": breached},
        level="INFO",
        msg=f"Daily budget rollover: ${total_cost:.4f} (breached={breached})",
    )


def roll_monthly(sqlite_conn) -> None:
    """Archive this month's budget state into history and reset for new month."""
    row = sqlite_conn.execute(
        "SELECT period_start, total_cost_usd, cap_usd FROM budget_state WHERE period = 'this_month'"
    ).fetchone()
    if row is None:
        return

    period_start, total_cost, cap = row
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1).strftime("%Y-%m-%d")

    # Archive
    breached = 1 if total_cost > cap else 0
    sqlite_conn.execute(
        "INSERT OR REPLACE INTO budget_history (period_start, period_end, period_type, total_cost_usd, cap_usd, breached) "
        "VALUES (?, ?, 'month', ?, ?, ?)",
        [period_start, now.strftime("%Y-%m-%d"), total_cost, cap, breached],
    )

    # Reset
    sqlite_conn.execute(
        "UPDATE budget_state SET total_cost_usd = 0, period_start = ?, updated_at = ? WHERE period = 'this_month'",
        [month_start, now.isoformat()],
    )
    sqlite_conn.commit()

    log_debug(
        "BUDGET_ROLLOVER",
        payload={"period_type": "month", "archived_cost": total_cost, "breached": breached},
        level="INFO",
        msg=f"Monthly budget rollover: ${total_cost:.4f} (breached={breached})",
    )


def check_and_roll(sqlite_conn) -> None:
    """Check if UTC period boundaries have been crossed and roll if needed.

    Called by orchestrator at cycle end.
    """
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    month_start = now.replace(day=1).strftime("%Y-%m-%d")

    # Check daily rollover
    row = sqlite_conn.execute(
        "SELECT period_start FROM budget_state WHERE period = 'today'"
    ).fetchone()
    if row and row[0] != today:
        roll_daily(sqlite_conn)

    # Check monthly rollover
    row = sqlite_conn.execute(
        "SELECT period_start FROM budget_state WHERE period = 'this_month'"
    ).fetchone()
    if row and row[0] != month_start:
        roll_monthly(sqlite_conn)
