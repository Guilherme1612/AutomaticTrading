"""Portfolio schemas — portfolio state, position tracking."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    """Current position in portfolio."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    holding_id: str
    entry_price_usd: float = Field(gt=0)
    current_price_usd: float = Field(gt=0)
    position_size_usd: float = Field(gt=0)
    share_count: int = Field(ge=1)
    sector: str | None = None
    unrealized_pnl_usd: float = 0.0
    unrealized_pnl_pct: float = 0.0


class PortfolioState(BaseModel):
    """Current portfolio state."""
    model_config = ConfigDict(frozen=True)

    cash_usd: float = Field(ge=0)
    positions: list[Position] = Field(default_factory=list)
    total_value_usd: float = Field(ge=0)
    unrealized_pnl_usd: float = 0.0

    @property
    def position_count(self) -> int:
        return len(self.positions)

    @property
    def sector_exposure(self) -> dict[str, float]:
        result: dict[str, float] = {}
        total = self.total_value_usd or 1.0
        for pos in self.positions:
            sector = pos.sector or "UNKNOWN"
            result[sector] = result.get(sector, 0.0) + pos.position_size_usd / total
        return result
