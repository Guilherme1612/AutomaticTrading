"""Mutation Engine schemas (Architecture.md §10, Agents.md §17)."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class MutationDimension(str, Enum):
    PERSONA_WEIGHT = "PERSONA_WEIGHT"
    PERSONA_PROMPT = "PERSONA_PROMPT"
    PERSONA_TEMPERATURE = "PERSONA_TEMPERATURE"
    CONVICTION_THRESHOLD = "CONVICTION_THRESHOLD"
    CRUCIBLE_SEVERITY = "CRUCIBLE_SEVERITY"
    SIZING_FRACTION = "SIZING_FRACTION"


class MutationStatus(str, Enum):
    PROPOSED = "PROPOSED"
    RUNNING_AB = "RUNNING_AB"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    OPERATOR_PROMOTED = "OPERATOR_PROMOTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class MutationCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    dimension: MutationDimension
    target: str  # persona name or config key
    baseline_value: str
    candidate_value: str
    fde_cluster_trigger: str = ""
    reversible: bool = True


class MutationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    candidate_id: str
    dimension: MutationDimension
    result: MutationStatus = MutationStatus.PROPOSED
    effect_size: float = 0.0
    p_value: float = 1.0
    sample_size: int = 0
    baseline_metric: float = 0.0
    candidate_metric: float = 0.0
