"""Opportunity cost engine -- hold vs exit decision based on conviction and alternatives.

Evaluates whether a position should be held or exited by comparing:
1. Conviction trajectory (entry conviction vs current conviction).
2. Current P&L relative to available alternatives.
3. Whether capital is better deployed elsewhere.

Architecture.md Section 12 step 18: iterates per active holding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pmacs.logsys import log_debug
from pmacs.schemas.contracts import Holding, HoldingState


@dataclass
class OpportunityCostResult:
    """Result of an opportunity cost evaluation."""
    action: str  # "HOLD" or "EXIT"
    reason: str
    opportunity_cost_pct: float
    holding_id: str = ""
    ticker: str = ""
    conviction_drop: float = 0.0
    exit_state: HoldingState | None = None


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
            conviction_drop=conviction_drop,
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
            conviction_drop=conviction_drop,
        )

    return OpportunityCostResult(
        action="HOLD",
        reason="No compelling reason to exit",
        opportunity_cost_pct=0.0,
        conviction_drop=conviction_drop,
    )


def evaluate_holding(
    holding: Holding,
    current_conviction: float,
    alternative_return_pct: float,
    cycle_id: str,
    days_held: int = 0,
) -> OpportunityCostResult:
    """Evaluate a single holding for opportunity cost.

    Computes PnL, calls decide_hold_or_exit, and when action is EXIT,
    sets exit_state=EXIT_OPPORTUNITY_COST.

    Architecture.md Section 12 step 18: per-holding evaluation.

    Args:
        holding: The Holding model to evaluate.
        current_conviction: Current conviction score for this thesis.
        alternative_return_pct: Expected return from best alternative.
        cycle_id: REQUIRED cycle ID (Architecture.md Section 16.5).
        days_held: Number of days held (computed from entry_date if 0).
    """
    if not cycle_id:
        raise ValueError(
            "cycle_id is REQUIRED on audit-emitting functions (Architecture.md §16.5)"
        )

    # Compute PnL
    pnl_pct = 0.0
    if holding.entry_price_usd and holding.entry_price_usd > 0:
        # PnL relative to entry -- if no exit_price, use entry as baseline
        # In production, current_price would be passed; for now, estimate
        pnl_pct = 0.0  # Will be overridden by caller with real data

    # Compute days held
    if days_held == 0 and holding.entry_date is not None:
        from datetime import date
        days_held = (date.today() - holding.entry_date).days

    # Get entry conviction
    conviction_at_entry = holding.conviction_score or 0.5

    result = decide_hold_or_exit(
        holding_pnl_pct=pnl_pct,
        days_held=days_held,
        alternative_expected_return_pct=alternative_return_pct,
        conviction_at_entry=conviction_at_entry,
        current_conviction=current_conviction,
    )

    # Fill holding metadata
    result.holding_id = holding.id
    result.ticker = holding.ticker

    # Set exit state for EXIT actions
    if result.action == "EXIT":
        result.exit_state = HoldingState.EXIT_OPPORTUNITY_COST

    # Audit
    log_debug(
        "OPPORTUNITY_COST_EVAL",
        payload={
            "holding_id": holding.id,
            "ticker": holding.ticker,
            "action": result.action,
            "conviction_drop": result.conviction_drop,
            "opportunity_cost_pct": result.opportunity_cost_pct,
            "reason": result.reason,
            "exit_state": result.exit_state.value if result.exit_state else None,
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Opportunity cost eval: {holding.ticker} -> {result.action}",
    )

    return result


def run_opportunity_cost_scan(
    active_holdings: list[Holding],
    conviction_scores: dict[str, float],
    alternative_return_pct: float,
    cycle_id: str,
    pnl_pcts: dict[str, float] | None = None,
) -> list[OpportunityCostResult]:
    """Iterate all active holdings and evaluate opportunity cost.

    This is what Nervous calls at step 18 of the cycle orchestration.

    Args:
        active_holdings: List of holdings in ACTIVE state.
        conviction_scores: Dict mapping holding_id -> current conviction.
        alternative_return_pct: Expected return from best alternative.
        cycle_id: REQUIRED cycle ID.
        pnl_pcts: Optional dict mapping holding_id -> current PnL pct.

    Returns:
        List of OpportunityCostResult, one per holding.
    """
    if not cycle_id:
        raise ValueError(
            "cycle_id is REQUIRED on audit-emitting functions (Architecture.md §16.5)"
        )

    results: list[OpportunityCostResult] = []

    for holding in active_holdings:
        current_conviction = conviction_scores.get(holding.id, holding.conviction_score or 0.5)

        # Build per-holding evaluation
        result = evaluate_holding(
            holding=holding,
            current_conviction=current_conviction,
            alternative_return_pct=alternative_return_pct,
            cycle_id=cycle_id,
        )

        # Override PnL if provided externally
        if pnl_pcts and holding.id in pnl_pcts:
            pnl = pnl_pcts[holding.id]
            conviction_at_entry = holding.conviction_score or 0.5
            conviction_drop = conviction_at_entry - current_conviction
            days_held = 0
            if holding.entry_date is not None:
                from datetime import date
                days_held = (date.today() - holding.entry_date).days

            override = decide_hold_or_exit(
                holding_pnl_pct=pnl,
                days_held=days_held,
                alternative_expected_return_pct=alternative_return_pct,
                conviction_at_entry=conviction_at_entry,
                current_conviction=current_conviction,
            )
            override.holding_id = holding.id
            override.ticker = holding.ticker
            if override.action == "EXIT":
                override.exit_state = HoldingState.EXIT_OPPORTUNITY_COST
            results.append(override)
        else:
            results.append(result)

    return results
