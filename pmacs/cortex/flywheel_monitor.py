"""Flywheel health monitoring (Architecture.md §3 repo tree).

Stub module for monitoring flywheel component health.
Will be wired in Phase 6-7 (Calibration + Mutation).
"""
from __future__ import annotations

from pmacs.schemas.flywheel import FlywheelHealthSnapshot


class FlywheelMonitor:
    """Flywheel component health monitor.

    Checks the overall health of the flywheel (calibration loop,
    mutation engine, lesson ingestion). Currently a stub that
    reports nominal health. Will be fully implemented in Phase 6-7
    per spec/Phases.md §2.

    The monitor will eventually:
    - Track rolling Brier score degradation
    - Monitor Sharpe ratio trends
    - Alert on max drawdown thresholds
    - Track calibration recency (cycles since last calibration)
    - Monitor active mutation count and health
    """

    def get_health(self) -> FlywheelHealthSnapshot:
        """Get the current flywheel health snapshot.

        Returns:
            FlywheelHealthSnapshot with current health metrics.
            All values are nominal/defaults in this stub.
        """
        return FlywheelHealthSnapshot(
            rolling_brier=None,
            rolling_sharpe=None,
            max_drawdown_pct=None,
            calibration_gap=None,
            cycles_since_calibration=0,
            active_mutations=0,
        )

    def is_healthy(self) -> bool:
        """Quick health check.

        Returns:
            True if the flywheel is in a healthy state.
        """
        health = self.get_health()
        # Stub: always healthy. Full implementation will check thresholds
        # from config/risk.toml and config/mutation.toml.
        return True
