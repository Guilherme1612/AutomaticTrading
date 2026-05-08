"""Override schemas — operator override learning."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class OperatorOverride(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    cycle_id: str
    ticker: str
    original_verdict: str
    override_verdict: str
    reason: str = ""
    cluster_id: str | None = None
