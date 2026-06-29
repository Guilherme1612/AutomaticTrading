"""Budget enforcer — three-tier cap checks and runaway detection.

PRD §8: per-cycle soft cap, daily hard cap, monthly hard cap, runaway detection.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pmacs.logsys import log_debug
from pmacs.nervous.sse_publisher import publish_system_event
from pmacs.schemas.billing import BudgetCheckResult


# Default caps (configurable via Settings → risk.toml [billing])
DEFAULT_CYCLE_SOFT_CAP = 1.00
DEFAULT_DAILY_HARD_CAP = 2.00
DEFAULT_MONTHLY_HARD_CAP = 30.00
RUNAWAY_MULTIPLIER = 1.5


def _load_billing_caps_from_risk_toml() -> tuple[float, float, float]:
    """Read operator-configured caps from config/risk.toml [billing].

    Falls back to the module-level defaults if the file is missing or malformed.
    The cycle soft cap is a single-tier gate — Settings UI writes the daily +
    monthly cap, and we expose a sensible default for the per-cycle soft cap.
    """
    try:
        from pathlib import Path
        import tomllib

        candidates = [
            Path("config") / "risk.toml",
            Path(__file__).resolve().parent.parent.parent / "config" / "risk.toml",
        ]
        for path in candidates:
            if path.exists():
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                billing = data.get("billing", {}) or {}
                daily = float(billing.get("daily_cap_usd", DEFAULT_DAILY_HARD_CAP))
                monthly = float(billing.get("monthly_cap_usd", DEFAULT_MONTHLY_HARD_CAP))
                # Allow operator to override the per-cycle soft cap too
                cycle = float(billing.get("cycle_soft_cap_usd", DEFAULT_CYCLE_SOFT_CAP))
                return daily, monthly, cycle
    except Exception:
        pass
    return DEFAULT_DAILY_HARD_CAP, DEFAULT_MONTHLY_HARD_CAP, DEFAULT_CYCLE_SOFT_CAP


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
    daily_cap: float | None = None,
    monthly_cap: float | None = None,
    cycle_soft_cap: float | None = None,
) -> BudgetCheckResult:
    """Run all three budget checks in order (cheapest to most expensive).

    Returns the first failing check, or allowed=True if all pass.

    Caps default to the operator-configured values from risk.toml [billing].
    Pass an explicit value to override for a single call.
    """
    cfg_daily, cfg_monthly, cfg_cycle = _load_billing_caps_from_risk_toml()
    if daily_cap is None:
        daily_cap = cfg_daily
    if monthly_cap is None:
        monthly_cap = cfg_monthly
    if cycle_soft_cap is None:
        cycle_soft_cap = cfg_cycle

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
    """Get current total cost for a budget period.

    Lazily rolls over stale daily/monthly periods BEFORE reading, so the cap
    always applies to the CURRENT period's spend — not spend accumulated across
    days/months because the scheduled roller never ran. ``period_roller.check_and_roll``
    has no scheduler/caller in the codebase (its docstring claims "called by
    orchestrator at cycle end" but the wiring was never added), so without this
    lazy rollover a stale "today" bucket accumulates multi-day spend and trips the
    daily cap as a false breach (root cause of the 2026-06-24 CYCLE_BLOCKED_BUDGET_DAILY
    at $2.04 — ~8 days of ~$0.25/day real spend piled into one never-rolled bucket).

    Idempotent: ``check_and_roll`` is a no-op once period_start matches the
    current day/month, so calling it on every read is cheap. Rollover failure is
    swallowed so a cap check never crashes — worst case it reads a stale total
    (the pre-fix behavior) instead of blocking.
    """
    try:
        from pmacs.billing.period_roller import check_and_roll
        check_and_roll(sqlite_conn)
    except Exception:
        pass
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
