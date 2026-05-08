"""Catastrophe-net stop placement — broker-side safety net.

Spec ref: Architecture.md §9.3, §16.7
PMACS manages tight stops internally. The broker receives only a catastrophe-net
stop at 15% below entry. This prevents catastrophic losses if PMACS stops responding.

Anti-pattern (Architecture.md §16.7): NEVER place tight stops on the broker side.
"""
from __future__ import annotations

from pmacs.constants import CATASTROPHE_NET_PCT


def compute_catastrophe_stop(entry_price: float) -> float:
    """Compute the broker-side catastrophe-net stop price.

    The stop is placed at CATASTROPHE_NET_PCT (15%) below entry price.
    Rounded to 2 decimal places for broker compatibility.
    """
    if entry_price <= 0:
        raise ValueError(f"Entry price must be positive, got {entry_price}")
    return round(entry_price * (1 - CATASTROPHE_NET_PCT), 2)


def place_catastrophe_net_stop(
    ticker: str,
    entry_price: float,
    shares: float,
) -> dict:
    """Create a catastrophe-net stop order for broker submission.

    Returns an order dict. Actual submission to broker happens in
    pmacs.execution.service. This function does NOT submit the order.

    Args:
        ticker: Stock ticker symbol.
        entry_price: Entry price of the position.
        shares: Number of shares.

    Returns:
        Order dict with all fields needed for broker submission.
    """
    stop_price = compute_catastrophe_stop(entry_price)
    return {
        "ticker": ticker,
        "side": "SELL",
        "type": "STOP_MARKET",
        "stop_price": stop_price,
        "qty": shares,
        "time_in_force": "GTC",
        "reason": "catastrophe_net",
    }


def validate_stop_order(order: dict) -> bool:
    """Validate a catastrophe-net stop order has all required fields."""
    required_fields = {"ticker", "side", "type", "stop_price", "qty", "time_in_force", "reason"}
    if not required_fields.issubset(order.keys()):
        return False
    if order["side"] != "SELL":
        return False
    if order["type"] != "STOP_MARKET":
        return False
    if order["reason"] != "catastrophe_net":
        return False
    if order["stop_price"] <= 0 or order["qty"] <= 0:
        return False
    return True
