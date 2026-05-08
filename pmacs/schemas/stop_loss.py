"""Stop-loss schemas — stop triggers, trailing stops."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class StopType(str, Enum):
    CATASTROPHE_NET = "CATASTROPHE_NET"
    FIXED_STOP = "FIXED_STOP"
    TRAILING_STOP = "TRAILING_STOP"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"


class StopTrigger(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    holding_id: str
    ticker: str
    stop_type: StopType
    trigger_price_usd: float = Field(gt=0)
    stop_price_usd: float = Field(gt=0)
    current_price_usd: float = Field(gt=0)
    gap_down: bool = False
    cycle_id: str = ""
    detected_at: str = ""
