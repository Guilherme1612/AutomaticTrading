"""Trailing stop engine — arm at 1.5R, ratchet up only.

The trailing stop is a profit-protection mechanism:
1. ARM: Activate when profit reaches 1.5R (1.5x the initial risk).
2. RATCHET: Move trailing price up as price rises, never down.
3. TRIGGER: Exit when price falls back to trailing stop level.

Trailing distance = 1.0 * ATR_20 (20-day average true range).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrailingStopState:
    """Current state of a trailing stop for one holding."""
    armed: bool = False
    trailing_stop_price: float = 0.0
    profit_r_at_arm: float = 0.0


def compute_profit_r(
    entry_price: float,
    current_price: float,
    stop_loss_price: float,
) -> float:
    """Compute profit in R-multiples. R = entry - stop.

    Args:
        entry_price: Position entry price.
        current_price: Current market price.
        stop_loss_price: Initial stop-loss price.

    Returns:
        Profit as a multiple of initial risk (R). Zero if R is zero.
    """
    if entry_price == stop_loss_price:
        return 0.0
    return (current_price - entry_price) / (entry_price - stop_loss_price)


def maybe_arm_trailing(
    entry_price: float,
    current_price: float,
    stop_loss_price: float,
    atr_20: float,
    is_armed: bool,
) -> TrailingStopState:
    """Arm trailing stop at profit_r > 1.5. Trailing = current - 1.0 * ATR_20.

    Args:
        entry_price: Position entry price.
        current_price: Current market price.
        stop_loss_price: Initial stop-loss price.
        atr_20: 20-day Average True Range.
        is_armed: Whether the trailing stop is already armed.

    Returns:
        Updated TrailingStopState. If already armed, returns state with
        armed=True but preserves existing trailing price (caller should use
        maybe_ratchet_trailing to update the trailing price).
    """
    if is_armed:
        return TrailingStopState(armed=True, trailing_stop_price=0.0)

    profit_r = compute_profit_r(entry_price, current_price, stop_loss_price)
    if profit_r > 1.5:
        trailing_price = current_price - 1.0 * atr_20
        return TrailingStopState(
            armed=True,
            trailing_stop_price=trailing_price,
            profit_r_at_arm=profit_r,
        )
    return TrailingStopState(armed=False)


def maybe_ratchet_trailing(
    current_price: float,
    atr_20: float,
    current_trailing: float,
) -> float:
    """Ratchet trailing stop up only. Returns new trailing price.

    The trailing stop only moves in the favorable direction. If the
    computed new trailing would be lower than the current one, the
    current one is kept.

    Args:
        current_price: Current market price.
        atr_20: 20-day Average True Range.
        current_trailing: Current trailing stop price.

    Returns:
        The higher of: current_trailing or (current_price - ATR_20).
    """
    new_trailing = current_price - 1.0 * atr_20
    if new_trailing > current_trailing:
        return new_trailing
    return current_trailing
