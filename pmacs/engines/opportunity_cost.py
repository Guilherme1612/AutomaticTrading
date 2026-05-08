"""Opportunity cost engine — hold vs exit decision based on conviction and alternatives.

Evaluates whether a position should be held or exited by comparing:
1. Conviction trajectory (entry conviction vs current conviction).
2. Current P&L relative to available alternatives.
3. Whether capital is better deployed elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OpportunityCostResult:
    """Result of an opportunity cost evaluation."""
    action: str  # "HOLD" or "EXIT"
    reason: str
    opportunity_cost_pct: float


def decide_hold_or_exit(
    holding_pnl_pct: float,
    days_held: int,
    alternative_expected_return_pct: float,
    conviction_at_entry: float,
    current_conviction: float,
) -> OpportunityCostResult:
    """Decide whether to hold or exit based on opportunity cost.

    Exit triggers:
    1. Conviction dropped > 0.3 AND current conviction < 0.2 (thesis collapse).
    2. Holding underwater > 5% AND alternatives offer 10%+ better return.

    Args:
        holding_pnl_pct: Current P&L as percentage (negative = underwater).
        days_held: Number of days the position has been held.
        alternative_expected_return_pct: Expected return from best alternative.
        conviction_at_entry: Conviction score at time of entry.
        current_conviction: Current conviction score.

    Returns:
        OpportunityCostResult with action ("HOLD" or "EXIT") and reason.
    """
    conviction_drop = conviction_at_entry - current_conviction

    # Strong conviction drop: exit
    if conviction_drop > 0.3 and current_conviction < 0.2:
        return OpportunityCostResult(
            action="EXIT",
            reason=(
                f"Conviction dropped {conviction_drop:.2f} "
                f"(from {conviction_at_entry:.2f} to {current_conviction:.2f})"
            ),
            opportunity_cost_pct=alternative_expected_return_pct,
        )

    # Underwater with better alternatives
    if (
        holding_pnl_pct < -5.0
        and alternative_expected_return_pct > holding_pnl_pct + 10.0
    ):
        return OpportunityCostResult(
            action="EXIT",
            reason=(
                f"Underwater ({holding_pnl_pct:.1f}%) with better "
                f"alternatives ({alternative_expected_return_pct:.1f}%)"
            ),
            opportunity_cost_pct=alternative_expected_return_pct,
        )

    return OpportunityCostResult(
        action="HOLD",
        reason="No compelling reason to exit",
        opportunity_cost_pct=0.0,
    )
