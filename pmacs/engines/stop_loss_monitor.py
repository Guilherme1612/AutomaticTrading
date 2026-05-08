"""Stop-loss monitor engine — breach detection, order type selection.

Checks every active holding for stop-loss breaches during RTH.
Produces StopCheckResult for the orchestration layer to act on.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StopCheckResult:
    """Result of a stop-loss breach check."""
    triggered: bool
    ticker: str
    holding_id: str
    stop_price: float
    current_price: float
    is_gap_down: bool
    order_type: str  # "MARKET" or "MARKET_ON_OPEN"


def check_stop_breach(
    holding,  # Holding-like object with stop_price_usd, ticker, id
    current_price: float,
    market_state: str = "RTH",
) -> StopCheckResult | None:
    """Check if price breached stop-loss level. Returns None if not breached.

    Args:
        holding: A Holding model with stop_price_usd field.
        current_price: Latest market price for the ticker.
        market_state: "RTH" for regular trading hours, anything else is
            treated as non-RTH (gap-down logic applies).

    Returns:
        StopCheckResult if breached, None otherwise.
    """
    stop_price = holding.stop_price_usd
    if stop_price is None:
        return None

    if current_price <= stop_price:
        is_gap = market_state != "RTH"
        return StopCheckResult(
            triggered=True,
            ticker=holding.ticker,
            holding_id=holding.id,
            stop_price=stop_price,
            current_price=current_price,
            is_gap_down=is_gap,
            order_type="MARKET_ON_OPEN" if is_gap else "MARKET",
        )
    return None


def determine_order_type(market_state: str) -> str:
    """Determine order type based on market state.

    Args:
        market_state: "RTH" for regular hours, anything else for non-RTH.

    Returns:
        "MARKET" during RTH, "MARKET_ON_OPEN" otherwise.
    """
    if market_state == "RTH":
        return "MARKET"
    return "MARKET_ON_OPEN"
