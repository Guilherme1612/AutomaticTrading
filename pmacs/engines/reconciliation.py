"""Paper-ledger reconciliation engine (Architecture.md §9).

Compares PMACS internal paper ledger totals against broker-reported
positions.  Pure deterministic math — no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReconciliationResult:
    """Output of a single reconciliation pass."""

    matched: bool
    pmacs_position_value: float
    broker_position_value: float
    difference_usd: float
    difference_pct: float
    requires_action: bool


def reconcile_paper_ledger(
    ledger_total: float,
    broker_total: float,
    tolerance_usd: float = 100.0,
    tolerance_pct: float = 5.0,
) -> ReconciliationResult:
    """Reconcile PMACS paper ledger with broker positions.

    A match is declared when *both* the absolute and percentage differences
    are within their respective tolerances.
    """
    diff = abs(ledger_total - broker_total)
    diff_pct = (diff / ledger_total * 100) if ledger_total > 0 else 0.0
    matched = diff <= tolerance_usd and diff_pct <= tolerance_pct

    return ReconciliationResult(
        matched=matched,
        pmacs_position_value=ledger_total,
        broker_position_value=broker_total,
        difference_usd=round(diff, 2),
        difference_pct=round(diff_pct, 2),
        requires_action=not matched,
    )
