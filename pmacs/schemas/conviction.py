"""Conviction schemas — conviction scoring (Source.md §7.2)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class VerdictTier(str, Enum):
    """The 5 verdict tiers emitted by the conviction engine.

    - STRONG_BUY / BUY: actionable longs (conviction >= 0.6 / 0.3 standard)
    - HOLD: position-management state for an *existing* active holding whose
      thesis is still valid (decision belongs to the holding, not the conviction)
    - SKIP: passive abstention — conviction too low to justify capital at risk
    - PASS: active no-bid — analyst-persona judgment says the setup is real
      but the edge doesn't justify entry (R:R < 1.5, comps empty + growth < 10%,
      etc.). PASS is *not* "we couldn't decide"; it's a deliberate no-action
      that requires a structured `pass_reason`. See ``engines.conviction`` for
      the trigger logic and ``schemas.personas.MemoWriterOutput.pass_reason``
      for the schema-side reason field.
    """
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SKIP = "SKIP"
    PASS = "PASS"


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
    def score_to_verdict(score: float, is_bootstrap: bool = False) -> VerdictTier:
        if is_bootstrap:
            if score >= 0.40:
                return VerdictTier.STRONG_BUY
            if score >= 0.15:
                return VerdictTier.BUY
            if score >= 0.05:
                return VerdictTier.HOLD
            return VerdictTier.SKIP
        if score >= 0.6:
            return VerdictTier.STRONG_BUY
        if score >= 0.3:
            return VerdictTier.BUY
        return VerdictTier.SKIP  # HOLD is only via verdict_tier() for active holdings
