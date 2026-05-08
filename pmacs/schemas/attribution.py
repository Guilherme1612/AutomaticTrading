"""Causal attribution schemas — credit/blame apportionment."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class AttributionEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    persona: str
    ticker: str
    contribution: float  # -1 to 1 (negative = blame)
    resolution_id: str = ""
