"""Paper trading ledger schemas."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class PaperLedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    ticker: str
    direction: str
    quantity: int
    price_usd: float = Field(gt=0)
    commission_usd: float = 0.0
    cash_after_usd: float
    total_value_usd: float
    cycle_id: str = ""
    timestamp: str = ""
