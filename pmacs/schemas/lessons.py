"""Lesson schemas — lesson extraction from resolutions."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class Lesson(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    kind: str
    text: str
    weight: float = Field(ge=0.0, le=1.0, default=0.5)
    resolution_id: str = ""
    cycle_id: str = ""
