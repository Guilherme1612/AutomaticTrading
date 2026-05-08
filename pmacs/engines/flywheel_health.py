"""Flywheel health engine — snapshot of all calibration components."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FlywheelHealthSnapshot:
    rolling_brier_avg: float
    rolling_sharpe: float
    calibration_gap: float  # difference between predicted and actual
    active_mutations: int
    pending_reviews: int
    lessons_count: int


def snapshot_health(
    rolling_brier_avg: float,
    rolling_sharpe: float,
    calibration_gap: float,
    active_mutations: int = 0,
    pending_reviews: int = 0,
    lessons_count: int = 0,
) -> FlywheelHealthSnapshot:
    """Build a point-in-time flywheel health snapshot."""
    return FlywheelHealthSnapshot(
        rolling_brier_avg=rolling_brier_avg,
        rolling_sharpe=rolling_sharpe,
        calibration_gap=calibration_gap,
        active_mutations=active_mutations,
        pending_reviews=pending_reviews,
        lessons_count=lessons_count,
    )
