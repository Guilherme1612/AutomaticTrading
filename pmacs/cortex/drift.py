"""Cross-cycle drift monitoring (Architecture.md §3 repo tree).

Stub module for monitoring parameter drift across cycles.
Will be wired in Phase 6 (Calibration + FDE).
"""
from __future__ import annotations

from dataclasses import dataclass

from pmacs.storage.consistency import ConsistencyResult


@dataclass(frozen=True)
class DriftResult:
    """Result of a drift check."""

    has_drift: bool
    dimension: str
    magnitude: float | None
    details: str


class DriftMonitor:
    """Cross-cycle drift monitor.

    Checks for parameter drift between cycles. Currently a stub
    that reports no drift. Will be fully implemented in Phase 6
    (Calibration + FDE) per spec/Phases.md §2.

    The monitor will eventually:
    - Track persona conviction score distributions over time
    - Detect shifts in catalyst frequency patterns
    - Monitor EV calculation parameter stability
    - Alert on calibration gap exceeding threshold
    """

    def check_drift(self, cycle_id: str) -> DriftResult:
        """Check for drift in the given cycle.

        Args:
            cycle_id: The cycle to check for drift.

        Returns:
            DriftResult indicating whether drift was detected.
        """
        return DriftResult(
            has_drift=False,
            dimension="none",
            magnitude=None,
            details="Drift monitoring not yet active (Phase 6 stub)",
        )

    def check_cross_db(self) -> list[ConsistencyResult]:
        """Check cross-database consistency as part of drift monitoring.

        Delegates to pmacs.storage.consistency in production.
        Currently returns empty list (stub).

        Returns:
            List of ConsistencyResult from cross-DB checks.
        """
        return []
