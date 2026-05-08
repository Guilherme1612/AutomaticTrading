"""Fundamental routing schemas."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class FundamentalData(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    market_cap_usd: float | None = None
    pe_ratio: float | None = None
    revenue_growth_pct: float | None = None
    sector: str | None = None
    subsector: str | None = None
