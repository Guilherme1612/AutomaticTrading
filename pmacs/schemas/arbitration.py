"""Arbitration schemas — combined directional probabilities.

Spec ref: Architecture.md §9.1
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pmacs.schemas.agents import DirectionalProbability, PersonaName


class ArbitrationDecision(str, Enum):
    """Possible outcomes of the arbitration engine."""
    PROCEED = "PROCEED"
    PROCEED_BOOTSTRAP_LOW_CONFIDENCE = "PROCEED_BOOTSTRAP_LOW_CONFIDENCE"
    ABORT_DISAGREEMENT = "ABORT_DISAGREEMENT"
    ABORT_NO_MATURE_SOURCES = "ABORT_NO_MATURE_SOURCES"


class PersonaWeight(BaseModel):
    """Weight assigned to a persona in arbitration."""
    model_config = ConfigDict(frozen=True)

    persona: PersonaName
    weight: float = Field(ge=0.0, le=1.0)
    brier_score: float | None = None
    calibration_count: int = 0


class Arbitrated(BaseModel):
    """Combined directional probabilities after arbitration."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    cycle_id: str
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    persona_outputs: list[DirectionalProbability] = Field(default_factory=list)
    persona_weights: list[PersonaWeight] = Field(default_factory=list)
    agreement_score: float = Field(ge=0.0, le=1.0, default=1.0)
    matured_sources_used: int = 0
    decision: ArbitrationDecision = ArbitrationDecision.PROCEED
    abort_reason: str | None = None

    @model_validator(mode="after")
    def _check_sum(self) -> "Arbitrated":
        if self.decision in (
            ArbitrationDecision.PROCEED,
            ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE,
        ):
            total = self.p_up + self.p_flat + self.p_down
            if abs(total - 1.0) > 0.01:
                raise ValueError(
                    f"probabilities sum to {total:.6f}, expected ~1.0 "
                    f"for decision={self.decision}"
                )
            # Normalize to exactly 1.0 if within tolerance
            if abs(total - 1.0) > 1e-9 and total > 0:
                scale = 1.0 / total
                object.__setattr__(self, "p_up", round(self.p_up * scale, 6))
                object.__setattr__(self, "p_flat", round(self.p_flat * scale, 6))
                object.__setattr__(self, "p_down", round(self.p_down * scale, 6))
        return self
