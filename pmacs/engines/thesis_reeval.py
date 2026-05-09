"""Thesis re-evaluation engine — weekly checks, aging reviews, conviction-based exits.

Periodically re-evaluates whether the investment thesis for a holding
remains valid. Three mechanisms:
1. Weekly re-evaluation: every 7 days since entry.
2. Thesis aging: mandatory review at 90+ days.
3. Conviction-based exit: if conviction collapses or crucible severity
   is high with low conviction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class ReEvalResult:
    """Result of a thesis re-evaluation."""
    action: str  # "VALIDATED", "EXIT_THESIS_INVALIDATED", "THESIS_AGING_REVIEW"
    reason: str
    new_conviction: float


def check_weekly_reeval(
    entry_date: date,
    current_date: date,
    current_conviction: float,
    last_reeval_at: date | None = None,
) -> bool:
    """Check if weekly re-evaluation is due (7+ days since last check).

    Uses ``last_reeval_at`` when available, falls back to ``entry_date``
    when None (first re-eval cycle).

    Args:
        entry_date: Date the position was entered.
        current_date: Today's date.
        current_conviction: Current conviction score (unused, kept for
            interface consistency).
        last_reeval_at: Date of the last re-evaluation. When None, uses
            entry_date as the reference point.

    Returns:
        True if 7 or more days have elapsed since the reference date.
    """
    reference_date = last_reeval_at if last_reeval_at is not None else entry_date
    return (current_date - reference_date).days >= 7


def check_thesis_aging(
    entry_date: date,
    current_date: date,
) -> bool:
    """Check if 90-day thesis aging review is triggered.

    This is measured in calendar days from entry_date. Unlike weekly
    re-evaluation, there is no reset — the 90-day clock starts at
    entry and never restarts.

    Args:
        entry_date: Date the position was entered.
        current_date: Today's date.

    Returns:
        True if the position has been held for 90 or more days.
    """
    return (current_date - entry_date).days >= 90


def evaluate_thesis(
    current_conviction: float,
    crucible_severity: float,
    holding_pnl_pct: float,
    is_aging_review: bool = False,
) -> ReEvalResult:
    """Evaluate whether thesis is still valid.

    Exit triggers (in order of priority):
    1. Conviction collapsed (< 0.1).
    2. Crucible severity > 0.8 with conviction < 0.3.
    3. Aging review + underwater > 10%.

    Args:
        current_conviction: Current conviction score for the thesis.
        crucible_severity: Severity score from the Crucible adversarial
            attack (0.0 to 1.0).
        holding_pnl_pct: Current P&L as percentage.
        is_aging_review: Whether this is a mandatory 90-day review.

    Returns:
        ReEvalResult with action and reason.
    """
    if current_conviction < 0.1:
        return ReEvalResult(
            action="EXIT_THESIS_INVALIDATED",
            reason=f"Conviction collapsed: {current_conviction:.2f}",
            new_conviction=current_conviction,
        )

    if crucible_severity > 0.8 and current_conviction < 0.3:
        return ReEvalResult(
            action="EXIT_THESIS_INVALIDATED",
            reason=(
                f"Crucible severity {crucible_severity:.2f} "
                f"with low conviction"
            ),
            new_conviction=current_conviction,
        )

    if is_aging_review and holding_pnl_pct < -10.0:
        return ReEvalResult(
            action="EXIT_THESIS_INVALIDATED",
            reason=f"Aging review: underwater {holding_pnl_pct:.1f}%",
            new_conviction=current_conviction,
        )

    return ReEvalResult(
        action="VALIDATED",
        reason="Thesis still valid",
        new_conviction=current_conviction,
    )
