"""Failure diagnostic schemas — 18-type taxonomy (Agents.md §15)."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class FailureTaxonomy(str, Enum):
    MOAT_DRIFT_OVERESTIMATE = "MOAT_DRIFT_OVERESTIMATE"
    CATALYST_TIMING_MISREAD = "CATALYST_TIMING_MISREAD"
    REGIME_SHIFT_MISSED = "REGIME_SHIFT_MISSED"
    SECTOR_CORRELATION_MISJUDGED = "SECTOR_CORRELATION_MISJUDGED"
    INSIDER_SIGNAL_NOISE = "INSIDER_SIGNAL_NOISE"
    SHORT_THESIS_CROWDED = "SHORT_THESIS_CROWDED"
    FORENSIC_RED_FLAG_FALSE_POSITIVE = "FORENSIC_RED_FLAG_FALSE_POSITIVE"
    STOP_HUNTED = "STOP_HUNTED"
    STOP_LOSS_CORRECT = "STOP_LOSS_CORRECT"
    THESIS_INVALIDATED_PREMATURE = "THESIS_INVALIDATED_PREMATURE"
    THESIS_INVALIDATED_CORRECT = "THESIS_INVALIDATED_CORRECT"
    OPPORTUNITY_COST_EXCEEDED = "OPPORTUNITY_COST_EXCEEDED"
    ENTRY_TIMING_POOR = "ENTRY_TIMING_POOR"
    EXIT_TIMING_POOR = "EXIT_TIMING_POOR"
    SIZING_OVERCONFIDENT = "SIZING_OVERCONFIDENT"
    SIZING_UNDERCONFIDENT = "SIZING_UNDERCONFIDENT"
    CORRELATION_BREAKDOWN = "CORRELATION_BREAKDOWN"
    CATALYST_FAILED_TO_MATERIALIZE = "CATALYST_FAILED_TO_MATERIALIZE"


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
