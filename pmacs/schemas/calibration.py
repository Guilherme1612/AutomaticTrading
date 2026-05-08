"""Calibration schemas — Brier-based probability refit."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class CalibrationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    persona: str
    brier_score: float = Field(ge=0.0, le=1.0)
    sample_count: int = Field(ge=0)
    calibrated_at: str = ""
