"""Fundamental routing engine — routes fundamental data to arbitration.

Spec ref: Architecture.md §9.4

Routes fundamental data from the data layer to the arbitration engine,
applying fundamental_weights_moat and fundamental_weights_growth factors.
Determines which fundamental signals contribute to the arbitrated output
and applies sector-specific weighting adjustments.
"""

from __future__ import annotations

from dataclasses import dataclass

from pmacs.schemas.fundamental import FundamentalData


# Default weights by sector (Architecture.md §9.4)
SECTOR_WEIGHTS: dict[str, dict[str, float]] = {
    "Technology": {"moat": 1.2, "growth": 1.3},
    "Healthcare": {"moat": 1.1, "growth": 1.1},
    "Financials": {"moat": 1.0, "growth": 0.8},
    "Consumer Staples": {"moat": 1.2, "growth": 0.7},
    "Consumer Discretionary": {"moat": 0.9, "growth": 1.2},
    "Industrials": {"moat": 1.0, "growth": 0.9},
    "Energy": {"moat": 0.8, "growth": 0.7},
    "Materials": {"moat": 0.7, "growth": 0.8},
    "Utilities": {"moat": 1.1, "growth": 0.6},
    "Real Estate": {"moat": 1.0, "growth": 0.6},
    "Communication Services": {"moat": 1.1, "growth": 1.2},
}

DEFAULT_WEIGHTS: dict[str, float] = {"moat": 1.0, "growth": 1.0}


@dataclass(frozen=True)
class RoutedFundamental:
    """Fundamental data routed with appropriate weights."""

    ticker: str
    sector: str | None
    moat_weight: float
    growth_weight: float
    has_fundamentals: bool
    market_cap_usd: float | None = None
    pe_ratio: float | None = None
    revenue_growth_pct: float | None = None


def route_fundamentals(data: FundamentalData) -> RoutedFundamental:
    """Route fundamental data with sector-appropriate weights.

    Applies fundamental_weights_moat and fundamental_weights_growth
    based on the company's sector classification. Missing fundamentals
    get default weights (1.0, 1.0).
    """
    sector = data.sector or "Unknown"
    weights = SECTOR_WEIGHTS.get(sector, DEFAULT_WEIGHTS)

    return RoutedFundamental(
        ticker=data.ticker,
        sector=sector,
        moat_weight=weights["moat"],
        growth_weight=weights["growth"],
        has_fundamentals=data.market_cap_usd is not None,
        market_cap_usd=data.market_cap_usd,
        pe_ratio=data.pe_ratio,
        revenue_growth_pct=data.revenue_growth_pct,
    )


def route_fundamental_batch(
    fundamentals: list[FundamentalData],
) -> list[RoutedFundamental]:
    """Route a batch of fundamental data for multiple tickers."""
    return [route_fundamentals(fd) for fd in fundamentals]
