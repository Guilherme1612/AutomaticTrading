"""Failure diagnostic schemas — 18 outcome + 5 reasoning-flaw taxonomy types (Agents.md §15).

The first 18 are outcome types emitted by ``classify()`` on terminal-state holdings
(Architecture.md §9.5). The last 5 are auditor-only reasoning-flaw types
(Agents.md §15.4) emitted by the CrossPersonaAuditor (§11d) at cycle time; ``classify()``
never produces them.
"""
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
    # Auditor-only reasoning-flaw types (Agents.md §15.4). Emitted by
    # CrossPersonaAuditor at cycle time, NEVER by classify(). The set below is
    # the "auditor-allowed set" referenced by AuditorFlag.taxonomy_mapping.
    CITATION_GAP = "CITATION_GAP"
    CONCLUSION_UNSUPPORTED = "CONCLUSION_UNSUPPORTED"
    CONFLICTING_CONCLUSIONS = "CONFLICTING_CONCLUSIONS"
    NUMBER_MISUSE = "NUMBER_MISUSE"
    HALLUCINATED_EVIDENCE = "HALLUCINATED_EVIDENCE"


# The subset of FailureTaxonomy that the CrossPersonaAuditor may emit (Agents.md §15.4).
# Used by the auditor sanity validator to reject an out-of-set taxonomy_mapping.
AUDITOR_ALLOWED_TAXONOMY: frozenset[FailureTaxonomy] = frozenset({
    FailureTaxonomy.CITATION_GAP,
    FailureTaxonomy.CONCLUSION_UNSUPPORTED,
    FailureTaxonomy.CONFLICTING_CONCLUSIONS,
    FailureTaxonomy.NUMBER_MISUSE,
    FailureTaxonomy.HALLUCINATED_EVIDENCE,
})


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
