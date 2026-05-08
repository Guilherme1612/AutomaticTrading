"""Flywheel health schemas — monitors all calibration components."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class FlywheelHealthSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    rolling_brier: float | None = None
    rolling_sharpe: float | None = None
    max_drawdown_pct: float | None = None
    calibration_gap: float | None = None
    cycles_since_calibration: int = 0
    active_mutations: int = 0
