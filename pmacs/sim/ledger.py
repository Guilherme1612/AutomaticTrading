"""Paper trading ledger — in-memory position tracking for PAPER mode.

Spec ref: Architecture.md §9.3, Source.md §1
Capital: $5,000 initial, max 5 concurrent positions, 20% max single position.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from pmacs.constants import CATASTROPHE_NET_PCT, MAX_CONCURRENT_POSITIONS, MAX_SINGLE_POSITION_PCT, PAPER_CAPITAL_USD


@dataclass
class Position:
    """A single paper position in the ledger."""

    ticker: str
    shares: float
    entry_price: float
    entry_date: datetime
    current_price: float
    stop_price: float | None = None
    sector: str | None = None

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def cost_basis(self) -> float:
        return self.shares * self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.cost_basis <= 0:
            return 0.0
        return self.unrealized_pnl / self.cost_basis


@dataclass
class PaperLedger:
    """In-memory paper trading ledger.

    Tracks cash, positions, and portfolio-level metrics.
    Designed for PAPER mode — no real broker interaction.
    """

    cash: float = PAPER_CAPITAL_USD
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def total_value(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def position_count(self) -> int:
        return len(self.positions)

    @property
    def positions_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    def open_position(
        self,
        ticker: str,
        shares: float,
        price: float,
        sector: str | None = None,
        stop_price: float | None = None,
    ) -> None:
        """Open a new position. Raises ValueError on constraint violations."""
        if shares <= 0:
            raise ValueError(f"Shares must be positive, got {shares}")
        if price <= 0:
            raise ValueError(f"Price must be positive, got {price}")

        cost = shares * price

        # Check cash sufficiency
        if cost > self.cash:
            raise ValueError(
                f"Insufficient cash: {self.cash:.2f} < {cost:.2f}"
            )

        # Check max single position size (20% of initial capital)
        max_position = PAPER_CAPITAL_USD * MAX_SINGLE_POSITION_PCT
        if cost > max_position:
            raise ValueError(
                f"Position exceeds max single position size: "
                f"{cost:.2f} > {max_position:.2f} ({MAX_SINGLE_POSITION_PCT:.0%} of capital)"
            )

        # Check max concurrent positions
        if ticker not in self.positions and self.position_count >= MAX_CONCURRENT_POSITIONS:
            raise ValueError(
                f"Max concurrent positions reached: {self.position_count} >= {MAX_CONCURRENT_POSITIONS}"
            )

        # Check duplicate ticker
        if ticker in self.positions:
            raise ValueError(f"Position already exists for {ticker}")

        # Set default catastrophe-net stop if not provided
        if stop_price is None:
            stop_price = round(price * (1 - CATASTROPHE_NET_PCT), 2)

        self.cash -= cost
        self.positions[ticker] = Position(
            ticker=ticker,
            shares=shares,
            entry_price=price,
            entry_date=datetime.now(timezone.utc),
            current_price=price,
            stop_price=stop_price,
            sector=sector,
        )

    def close_position(self, ticker: str, price: float) -> float:
        """Close a position. Returns realized PnL."""
        if price <= 0:
            raise ValueError(f"Close price must be positive, got {price}")
        if ticker not in self.positions:
            raise ValueError(f"No position: {ticker}")

        pos = self.positions.pop(ticker)
        proceeds = pos.shares * price
        self.cash += proceeds
        return proceeds - pos.cost_basis

    def update_price(self, ticker: str, price: float) -> None:
        """Update the current price of a position."""
        if ticker not in self.positions:
            raise ValueError(f"No position: {ticker}")
        self.positions[ticker].current_price = price

    def update_prices(self, prices: dict[str, float]) -> None:
        """Bulk update current prices for multiple positions."""
        for ticker, price in prices.items():
            if ticker in self.positions:
                self.positions[ticker].current_price = price

    def snapshot(self) -> dict:
        """Return a summary snapshot of the ledger state."""
        return {
            "cash": self.cash,
            "positions_value": self.positions_value,
            "total_value": self.total_value,
            "position_count": self.position_count,
            "unrealized_pnl": sum(p.unrealized_pnl for p in self.positions.values()),
        }
