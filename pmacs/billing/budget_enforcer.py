"""Budget enforcer — three-tier cap checks and runaway detection.

PRD §8: per-cycle soft cap, daily hard cap, monthly hard cap, runaway detection.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pmacs.logsys import log_debug
from pmacs.nervous.sse_publisher import publish_system_event
from pmacs.schemas.billing import BudgetCheckResult


# Default caps (configurable via Settings)
DEFAULT_CYCLE_SOFT_CAP = 1.00
DEFAULT_DAILY_HARD_CAP = 2.00
DEFAULT_MONTHLY_HARD_CAP = 30.00
RUNAWAY_MULTIPLIER = 1.5


def check_per_cycle_soft_cap(
    sqlite_conn,
    estimated_total: float,
    cap: float = DEFAULT_CYCLE_SOFT_CAP,
) -> BudgetCheckResult:
    """Check if estimated cycle cost exceeds the per-cycle soft cap.

    Includes cumulative spend already in today's budget.
    On breach: cycle does NOT start automatically. Operator confirmation required.
    """
    current_today = _get_period_total(sqlite_conn, "today")
    projected = current_today + estimated_total
    if projected <= cap:
        return BudgetCheckResult(
            allowed=True, cap_type="cycle_soft",
            current_total=current_today, estimated_new_total=projected, cap_usd=cap,
        )

    return BudgetCheckResult(
        allowed=False,
        reason=f"Cycle estimate ${estimated_total:.4f} (projected ${projected:.4f}) exceeds soft cap ${cap:.2f}",
        cap_type="cycle_soft",
        current_total=current_today,
        estimated_new_total=projected,
        cap_usd=cap,
    )


def check_daily_hard_cap(
    sqlite_conn,
    estimated_call_cost: float,
    cap: float = DEFAULT_DAILY_HARD_CAP,
) -> BudgetCheckResult:
    """Check if adding this call's cost would exceed the daily hard cap.

    On breach: call rejected, kill switch engaged.
    """
    current = _get_period_total(sqlite_conn, "today")
    projected = current + estimated_call_cost

    if projected <= cap:
        return BudgetCheckResult(
            allowed=True, cap_type="daily_hard",
            current_total=current, estimated_new_total=projected, cap_usd=cap,
        )

    log_debug(
        "COST_CAP_BREACHED",
        payload={"period": "today", "current": current, "projected": projected, "cap": cap},
        level="WARN",
        error_code="COST_CAP_BREACHED",
        msg=f"Daily cap breach: ${projected:.4f} would exceed ${cap:.2f}",
    )

    # Engage kill switch (Phase 16 — PRD §8.2)
    _engage_budget_kill_switch("CYCLE_BLOCKED_BUDGET_DAILY", projected, cap)

    publish_system_event("cost.cap_breached", {
        "period": "daily",
        "current": round(current, 6),
        "projected": round(projected, 6),
        "cap": cap,
        "cap_type": "daily_hard",
    })

    return BudgetCheckResult(
        allowed=False,
        reason=f"Daily spend ${projected:.4f} would exceed cap ${cap:.2f}",
        cap_type="daily_hard",
        current_total=current,
        estimated_new_total=projected,
        cap_usd=cap,
    )


def check_monthly_hard_cap(
    sqlite_conn,
    estimated_call_cost: float,
    cap: float = DEFAULT_MONTHLY_HARD_CAP,
) -> BudgetCheckResult:
    """Check if adding this call's cost would exceed the monthly hard cap.

    On breach: call rejected, kill switch engaged.
    """
    current = _get_period_total(sqlite_conn, "this_month")
    projected = current + estimated_call_cost

    if projected <= cap:
        return BudgetCheckResult(
            allowed=True, cap_type="monthly_hard",
            current_total=current, estimated_new_total=projected, cap_usd=cap,
        )

    log_debug(
        "COST_CAP_BREACHED",
        payload={"period": "this_month", "current": current, "projected": projected, "cap": cap},
        level="WARN",
        error_code="COST_CAP_BREACHED",
        msg=f"Monthly cap breach: ${projected:.4f} would exceed ${cap:.2f}",
    )

    # Engage kill switch (Phase 16 — PRD §8.3)
    _engage_budget_kill_switch("CYCLE_BLOCKED_BUDGET_MONTHLY", projected, cap)

    publish_system_event("cost.cap_breached", {
        "period": "monthly",
        "current": round(current, 6),
        "projected": round(projected, 6),
        "cap": cap,
        "cap_type": "monthly_hard",
    })

    return BudgetCheckResult(
        allowed=False,
        reason=f"Monthly spend ${projected:.4f} would exceed cap ${cap:.2f}",
        cap_type="monthly_hard",
        current_total=current,
        estimated_new_total=projected,
        cap_usd=cap,
    )


def check_runaway(
    actual_cumulative: float,
    estimated_cumulative: float,
) -> BudgetCheckResult:
    """Detect mid-cycle cost runaway (actual > 1.5x estimated).

    On detection: pause cycle, surface alert for operator review.
    """
    if estimated_cumulative <= 0:
        return BudgetCheckResult(allowed=True, cap_type="runaway")

    ratio = actual_cumulative / estimated_cumulative
    if ratio <= RUNAWAY_MULTIPLIER:
        return BudgetCheckResult(allowed=True, cap_type="runaway")

    delta_pct = (ratio - 1.0) * 100
    log_debug(
        "COST_RUNAWAY_DETECTED",
        payload={
            "actual": actual_cumulative,
            "estimated": estimated_cumulative,
            "delta_pct": round(delta_pct, 1),
        },
        level="WARN",
        error_code="COST_RUNAWAY_DETECTED",
        msg=f"Runaway cost detected: ${actual_cumulative:.4f} is {delta_pct:.0f}% above estimate ${estimated_cumulative:.4f}",
    )

    publish_system_event("cost.runaway_detected", {
        "actual": round(actual_cumulative, 6),
        "estimated": round(estimated_cumulative, 6),
        "delta_pct": round(delta_pct, 1),
    })

    return BudgetCheckResult(
        allowed=False,
        reason=f"Runaway: actual ${actual_cumulative:.4f} is {delta_pct:.0f}% above estimate ${estimated_cumulative:.4f}",
        cap_type="runaway",
        current_total=actual_cumulative,
        estimated_new_total=estimated_cumulative,
    )


def enforce_budgets(
    sqlite_conn,
    estimated_call_cost: float,
    daily_cap: float = DEFAULT_DAILY_HARD_CAP,
    monthly_cap: float = DEFAULT_MONTHLY_HARD_CAP,
    cycle_soft_cap: float = DEFAULT_CYCLE_SOFT_CAP,
) -> BudgetCheckResult:
    """Run all three budget checks in order (cheapest to most expensive).

    Returns the first failing check, or allowed=True if all pass.
    """
    # Per-cycle soft cap (operator confirmation gate)
    result = check_per_cycle_soft_cap(sqlite_conn, estimated_call_cost, cycle_soft_cap)
    if not result.allowed:
        return result

    # Daily hard cap
    result = check_daily_hard_cap(sqlite_conn, estimated_call_cost, daily_cap)
    if not result.allowed:
        return result

    # Monthly hard cap
    result = check_monthly_hard_cap(sqlite_conn, estimated_call_cost, monthly_cap)
    if not result.allowed:
        return result

    return BudgetCheckResult(allowed=True)


def _get_period_total(sqlite_conn, period: str) -> float:
    """Get current total cost for a budget period."""
    row = sqlite_conn.execute(
        "SELECT total_cost_usd FROM budget_state WHERE period = ?",
        [period],
    ).fetchone()
    return row[0] if row else 0.0


def _engage_budget_kill_switch(trigger: str, total: float, cap: float) -> None:
    """Engage kill switch for budget breach. Best-effort — won't crash if KS unavailable."""
    try:
        from pmacs.cortex.kill_switch import engage
        engage(trigger=trigger, reason=f"Budget breach: ${total:.4f} exceeds cap ${cap:.2f}")
    except Exception as exc:
        log_debug(
            "KILL_SWITCH_ENGAGE_FAILED",
            payload={"trigger": trigger, "error": str(exc)},
            level="WARN",
            error_code="KILL_SWITCH_ENGAGED",  # reuse — this is the closest valid code for KS failure
            msg=f"Failed to engage kill switch for {trigger}: {exc}",
        )
