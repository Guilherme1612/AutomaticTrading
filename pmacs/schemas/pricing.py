"""Pricing schemas — EV computation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvInput(BaseModel):
    """Input to EV computation."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    p_up: float = Field(ge=0.0, le=1.0)
    p_down: float = Field(ge=0.0, le=1.0)
    target_price_usd: float = Field(gt=0)
    stop_price_usd: float = Field(gt=0)
    current_price_usd: float = Field(gt=0)
    conviction_score: float = Field(ge=0.0, le=1.0, default=0.5)


class EvResult(BaseModel):
    """Output of EV computation."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    ev_usd: float
    ev_pct: float
    reward_risk_ratio: float
    should_trade: bool
    reason: str = ""
