"""Failure diagnostic schemas — 18-type taxonomy (Agents.md §15)."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class FailureTaxonomy(str, Enum):
    THESIS_INVALIDATED_FUNDAMENTAL = "THESIS_INVALIDATED_FUNDAMENTAL"
    THESIS_INVALIDATED_COMPETITIVE = "THESIS_INVALIDATED_COMPETITIVE"
    THESIS_INVALIDATED_REGULATORY = "THESIS_INVALIDATED_REGULATORY"
    CATALYST_FALSE_POSITIVE = "CATALYST_FALSE_POSITIVE"
    CATALYST_TIMEOUT = "CATALYST_TIMEOUT"
    STOP_HUNTED = "STOP_HUNTED"
    STOP_LOSS_CORRECT = "STOP_LOSS_CORRECT"
    EXOGENOUS_MACRO_SHOCK = "EXOGENOUS_MACRO_SHOCK"
    CORRELATION_REGIME_SHIFT = "CORRELATION_REGIME_SHIFT"
    MOAT_DRIFT_OVERESTIMATE = "MOAT_DRIFT_OVERESTIMATE"
    GROWTH_STALL_MISSED = "GROWTH_STALL_MISSED"
    FORENSICS_FLAG_IGNORED = "FORENSICS_FLAG_IGNORED"
    INSIDER_SIGNAL_FALSE = "INSIDER_SIGNAL_FALSE"
    SHORT_INTEREST_CORRECT = "SHORT_INTEREST_CORRECT"
    SIZING_OVERLEVERAGED = "SIZING_OVERLEVERAGED"
    EXECUTION_SLIPPAGE = "EXECUTION_SLIPPAGE"
    OPPORTUNITY_COST_EXIT_CORRECT = "OPPORTUNITY_COST_EXIT_CORRECT"
    UNCLASSIFIED = "UNCLASSIFIED"


class FailedAssumption(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    taxonomy: FailureTaxonomy
    severity: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    holding_id: str = ""
    cycle_id: str = ""
    detected_at: str = ""


class FailureClassification(BaseModel):
    model_config = ConfigDict(frozen=True)
    holding_id: str
    primary_taxonomy: FailureTaxonomy
    secondary_taxonomy: FailureTaxonomy | None = None
    failed_assumptions: list[FailedAssumption] = Field(default_factory=list)
    recovery_hours: float | None = None
