"""Trade schemas — TradePlan (signed), execution results."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TradeDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    MARKET_ON_OPEN = "MARKET_ON_OPEN"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TradePlan(BaseModel):
    """Signed trade plan produced by the pipeline."""
    model_config = ConfigDict(frozen=True)

    id: str
    ticker: str
    direction: TradeDirection
    order_type: OrderType = OrderType.LIMIT
    quantity: int = Field(ge=1)
    price_usd: float = Field(gt=0)
    stop_price_usd: float | None = None
    cycle_id: str = ""
    holding_id: str = ""
    conviction_score: float = 0.0
    verdict: str = "SKIP"
    signature_b64: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TradeResult(BaseModel):
    """Result of a trade execution."""
    model_config = ConfigDict(frozen=True)

    id: str
    trade_plan_id: str
    ticker: str
    direction: TradeDirection
    filled_quantity: int = 0
    filled_price_usd: float = 0.0
    commission_usd: float = 0.0
    status: str = "PENDING"  # PENDING, FILLED, PARTIAL, REJECTED
    broker_order_id: str | None = None
    filled_at: datetime | None = None
