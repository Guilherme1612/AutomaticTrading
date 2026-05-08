"""Sizing schemas — position sizing with Kelly criterion."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SizingInput(BaseModel):
    """Input to position sizing."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    portfolio_value_usd: float = Field(gt=0)
    ev_pct: float
    conviction_score: float = Field(ge=0.0, le=1.0)
    current_price_usd: float = Field(gt=0)
    stop_price_usd: float | None = None
    history_count: int = Field(ge=0, default=0)
    max_position_pct: float = Field(ge=0.0, le=1.0, default=0.20)


class SizingResult(BaseModel):
    """Output of position sizing."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    position_size_usd: float = Field(ge=0)
    position_size_pct: float = Field(ge=0.0, le=1.0)
    share_count: int = Field(ge=0)
    kelly_fraction: float = Field(ge=0.0)
    bootstrap_haircut: float = Field(ge=0.0, le=1.0, default=0.0)
    limited_history_haircut: float = Field(ge=0.0, le=1.0, default=0.0)
    capped: bool = False
    reason: str = ""
