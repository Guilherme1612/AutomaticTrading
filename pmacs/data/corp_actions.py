"""Corporate actions — splits, dividends, mergers (Architecture.md §6)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CorpActionType(str, Enum):
    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    MERGER = "MERGER"
    SPINOFF = "SPINOFF"
    NAME_CHANGE = "NAME_CHANGE"


@dataclass(frozen=True)
class CorpAction:
    type: CorpActionType
    ticker: str
    ex_date: str  # ISO date
    ratio: float = 1.0  # For splits: new/old (e.g., 2.0 for 2:1 split)
    amount: float = 0.0  # For dividends: amount per share


def adjust_price_for_split(price: float, split_ratio: float) -> float:
    """Adjust historical price for a stock split.

    Args:
        price: Pre-split price.
        split_ratio: New shares per old share (2.0 for 2:1 split).

    Returns:
        Adjusted price.
    """
    if split_ratio <= 0:
        raise ValueError(f"Invalid split ratio: {split_ratio}")
    return price / split_ratio


def adjust_cost_basis_for_dividend(cost_basis: float, dividend_per_share: float) -> float:
    """Reduce cost basis by dividend amount.

    Args:
        cost_basis: Current cost basis per share.
        dividend_per_share: Dividend amount per share.

    Returns:
        Adjusted cost basis.
    """
    return max(0.0, cost_basis - dividend_per_share)
