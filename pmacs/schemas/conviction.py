"""Conviction schemas — conviction scoring (Source.md §7.2)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class VerdictTier(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    SKIP = "SKIP"


class ConvictionInput(BaseModel):
    """Input to conviction computation."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    p_up: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    ev_pct: float
    arbitrated_confidence: float = Field(ge=0.0, le=1.0)
    crucible_severity: float = Field(ge=0.0, le=1.0, default=0.0)
    matured_sources_used: int = 0


class ConvictionResult(BaseModel):
    """Output of conviction scoring."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    score: float = Field(ge=0.0, le=1.0)
    verdict: VerdictTier
    components: dict[str, float] = Field(default_factory=dict)
    reason: str = ""

    @staticmethod
    def score_to_verdict(score: float) -> VerdictTier:
        if score >= 0.6:
            return VerdictTier.STRONG_BUY
        if score >= 0.3:
            return VerdictTier.BUY
        return VerdictTier.SKIP
