"""Reconciliation schemas — paper-vs-broker, cross-DB."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: str
    target: str
    entity_type: str
    entity_id: str
    drift_type: str  # MISSING_IN_TARGET / FIELD_MISMATCH / ORPHAN
    details: str = ""
    resolved: bool = False
