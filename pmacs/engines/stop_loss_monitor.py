"""Stop-loss monitor engine -- breach detection, order type selection.

Checks every active holding for stop-loss breaches during RTH.
Produces StopCheckResult for the orchestration layer to act on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StopBreachType = Literal["FIXED_STOP", "TRAILING_STOP"]


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
    stop_type: StopBreachType = "FIXED_STOP"


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
            stop_type="FIXED_STOP",
        )
    return None


def check_trailing_breach(
    holding,  # Holding-like with trailing_stop_price_usd, trailing_stop_armed, ticker, id
    current_price: float,
    market_state: str = "RTH",
) -> StopCheckResult | None:
    """Check if price breached trailing stop level. Returns None if not breached.

    Only triggers when the trailing stop is armed (holding.trailing_stop_armed
    or holding.trailing_stop_price_usd is set) and current_price has fallen
    to or below the trailing stop price.

    Produces EXIT_TRAILING_STOP state transition [S2].

    Args:
        holding: A Holding model with trailing_stop_price_usd and optionally
            a trailing_stop_armed flag. If trailing_stop_armed attribute is
            not present, the presence of a non-None trailing_stop_price_usd
            implies armed.
        current_price: Latest market price for the ticker.
        market_state: "RTH" for regular trading hours.

    Returns:
        StopCheckResult with stop_type="TRAILING_STOP" if breached, None otherwise.
    """
    # Check if trailing stop is armed
    armed = getattr(holding, "trailing_stop_armed", None)
    trailing_price = getattr(holding, "trailing_stop_price_usd", None)

    # If explicit armed flag is available, use it
    if armed is not None:
        if not armed:
            return None
    else:
        # No explicit armed flag -- presence of trailing_stop_price_usd implies armed
        if trailing_price is None:
            return None

    if trailing_price is None:
        return None

    if current_price <= trailing_price:
        is_gap = market_state != "RTH"
        return StopCheckResult(
            triggered=True,
            ticker=holding.ticker,
            holding_id=holding.id,
            stop_price=trailing_price,
            current_price=current_price,
            is_gap_down=is_gap,
            order_type="MARKET_ON_OPEN" if is_gap else "MARKET",
            stop_type="TRAILING_STOP",
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
