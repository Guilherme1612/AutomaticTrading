"""Queue schemas — cycle queue management."""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


class PriorityBand(IntEnum):
    P1_HIGHEST = 1
    P2_HIGH = 2
    P3_NORMAL = 3
    P4_LOW = 4


class QueueItem(BaseModel):
    """A ticker in the cycle queue."""
    model_config = ConfigDict(frozen=True)

    cycle_id: str
    ticker: str
    priority_band: PriorityBand = PriorityBand.P3_NORMAL
    pinned: bool = False
    operator_initiated: bool = False
    enqueued_at: str = ""  # ISO timestamp
    started_at: str | None = None
    completed_at: str | None = None
