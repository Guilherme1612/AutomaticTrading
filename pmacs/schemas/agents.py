"""Agent output schemas — persona outputs, directional probabilities."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PersonaName(str, Enum):
    GATEKEEPER = "gatekeeper"
    MACRO_REGIME = "macro_regime"
    CATALYST_SUMMARIZER = "catalyst_summarizer"
    MOAT_ANALYST = "moat_analyst"
    GROWTH_HUNTER = "growth_hunter"
    INSIDER_ACTIVITY = "insider_activity"
    SHORT_INTEREST = "short_interest"
    FORENSICS = "forensics"
    # Wave-2 debate + audit personas (Agents.md §11b-§11d). Run after the 7
    # analysis personas, before Arbitration. Advocates emit DirectionalProbability;
    # the auditor emits flags (no probabilities).
    BULL_ADVOCATE = "bull_advocate"
    BEAR_ADVOCATE = "bear_advocate"
    CROSS_PERSONA_AUDITOR = "cross_persona_auditor"
    CRUCIBLE = "crucible"
    MEMO_WRITER = "memo_writer"
    # Post-arbitration forward-valuation persona (Agents.md §18, Architecture.md
    # §9.4b). Emits bull/base/bear ASSUMPTIONS consumed by the deterministic
    # ForwardValuationEngine. Does NOT enter Arbitration, does NOT amend conviction.
    VALUATION_AGENT = "valuation_agent"


class DirectionalProbability(BaseModel):
    """Persona output: directional probabilities for a ticker.

    Probabilities must sum to approximately 1.0 (±1e-6).
    """
    model_config = ConfigDict(frozen=True)

    persona: PersonaName
    ticker: str
    p_up: float = Field(ge=0.0, le=1.0)
    p_flat: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    reasoning: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    cycle_id: str = ""

    @model_validator(mode="after")
    def _probabilities_sum_to_one(self) -> "DirectionalProbability":
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Probabilities must sum to ~1.0, got {total:.6f} "
                f"(p_up={self.p_up}, p_flat={self.p_flat}, p_down={self.p_down})"
            )
        if self.p_up == 1.0 and self.p_flat == 0.0 and self.p_down == 0.0:
            raise ValueError("Degenerate distribution (all mass on p_up)")
        if self.p_down == 1.0 and self.p_flat == 0.0 and self.p_up == 0.0:
            raise ValueError("Degenerate distribution (all mass on p_down)")
        return self


class PersonaOutput(BaseModel):
    """Base class for all persona structured outputs."""
    model_config = ConfigDict(frozen=True)

    persona: PersonaName
    ticker: str
    cycle_id: str
    raw_output: str = ""
    grammar_version: str = ""
    model_hash: str = ""
    temperature: float = 0.2
    seed: int = 0
    retry_count: int = 0
