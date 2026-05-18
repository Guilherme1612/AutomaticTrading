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


class GateStatus(BaseModel):
    """Status of a single promotion/demotion gate check (Phases.md §3.2)."""
    model_config = ConfigDict(frozen=True)

    gate_name: str
    passed: bool
    current_value: float | int
    threshold: float | int
    comparison: str  # ">=", "<=", ">", "<"


class PromotionGateResult(BaseModel):
    """Result of checking all promotion gates for a mode transition (Phases.md §3.2)."""
    model_config = ConfigDict(frozen=True)

    current_mode: str
    target_mode: str
    gates: list[GateStatus]
    all_pass: bool
    current_values: dict[str, float | int]
    thresholds: dict[str, float | int]


class DemotionGateResult(BaseModel):
    """Result of checking demotion gates for current mode (Phases.md §3.5)."""
    model_config = ConfigDict(frozen=True)

    current_mode: str
    demoted_mode: str | None = None  # None if no demotion triggered
    triggered: bool = False
    trigger_reason: str = ""
    trigger_metric: str = ""  # "sharpe", "drawdown", "brier"
    current_value: float = 0.0
    threshold: float = 0.0
