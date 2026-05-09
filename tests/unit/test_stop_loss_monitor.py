"""Tests for stop_loss_monitor -- fixed and trailing breach detection.

Task 1 [S2]: check_trailing_breach, check_stop_breach, priority logic.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pmacs.engines.stop_loss_monitor import (
    StopCheckResult,
    check_stop_breach,
    check_trailing_breach,
)


@dataclass
class MockHolding:
    """Minimal holding-like object for stop loss tests."""
    id: str = "h-001"
    ticker: str = "TEST"
    stop_price_usd: float | None = 85.0
    trailing_stop_price_usd: float | None = None
    trailing_stop_armed: bool | None = None


class TestCheckTrailingBreach:
    """check_trailing_breach tests."""

    def test_trailing_breach_returns_trailing_stop_type(self):
        """When trailing armed and price breaches, returns TRAILING_STOP."""
        h = MockHolding(
            trailing_stop_price_usd=95.0,
            trailing_stop_armed=True,
        )
        result = check_trailing_breach(h, current_price=94.0)
        assert result is not None
        assert result.stop_type == "TRAILING_STOP"
        assert result.triggered is True
        assert result.stop_price == 95.0
        assert result.current_price == 94.0

    def test_trailing_not_breached_returns_none(self):
        """When price above trailing stop, returns None."""
        h = MockHolding(
            trailing_stop_price_usd=90.0,
            trailing_stop_armed=True,
        )
        result = check_trailing_breach(h, current_price=95.0)
        assert result is None

    def test_trailing_not_armed_returns_none(self):
        """When trailing stop not armed, returns None regardless of price."""
        h = MockHolding(
            trailing_stop_price_usd=95.0,
            trailing_stop_armed=False,
        )
        result = check_trailing_breach(h, current_price=90.0)
        assert result is None

    def test_trailing_no_price_returns_none(self):
        """When trailing_stop_price_usd is None, returns None."""
        h = MockHolding(
            trailing_stop_price_usd=None,
            trailing_stop_armed=True,
        )
        result = check_trailing_breach(h, current_price=90.0)
        assert result is None

    def test_trailing_implicit_arm(self):
        """When no explicit armed flag, trailing price presence implies armed."""
        h = MockHolding(
            trailing_stop_price_usd=90.0,
            trailing_stop_armed=None,  # No explicit flag
        )
        result = check_trailing_breach(h, current_price=89.0)
        assert result is not None
        assert result.stop_type == "TRAILING_STOP"

    def test_trailing_at_exact_price_triggers(self):
        """Price exactly at trailing stop triggers breach (<=)."""
        h = MockHolding(
            trailing_stop_price_usd=90.0,
            trailing_stop_armed=True,
        )
        result = check_trailing_breach(h, current_price=90.0)
        assert result is not None
        assert result.stop_type == "TRAILING_STOP"


class TestCheckStopBreach:
    """check_stop_breach tests (existing + stop_type field)."""

    def test_fixed_stop_breach_returns_fixed_stop_type(self):
        """Fixed stop breach returns FIXED_STOP type."""
        h = MockHolding(stop_price_usd=85.0)
        result = check_stop_breach(h, current_price=84.0)
        assert result is not None
        assert result.stop_type == "FIXED_STOP"
        assert result.triggered is True
        assert result.ticker == "TEST"

    def test_fixed_stop_no_breach(self):
        """Price above stop returns None."""
        h = MockHolding(stop_price_usd=85.0)
        result = check_stop_breach(h, current_price=86.0)
        assert result is None

    def test_fixed_stop_no_price(self):
        """Holding with no stop_price_usd returns None."""
        h = MockHolding(stop_price_usd=None)
        result = check_stop_breach(h, current_price=80.0)
        assert result is None


class TestBreachPriority:
    """When both fixed and trailing breach, trailing takes priority when armed."""

    def test_both_breach_trailing_takes_priority(self):
        """When both fixed and trailing breached, trailing is returned first."""
        h = MockHolding(
            stop_price_usd=85.0,
            trailing_stop_price_usd=95.0,
            trailing_stop_armed=True,
        )
        from pmacs.stop_loss_daemon import check_holding
        result, category = check_holding(h, current_price=84.0)
        assert result is not None
        assert result.stop_type == "TRAILING_STOP"
        assert category == "TRAILING"

    def test_fixed_only_when_trailing_not_armed(self):
        """When trailing not armed, only fixed stop is checked."""
        h = MockHolding(
            stop_price_usd=85.0,
            trailing_stop_price_usd=95.0,
            trailing_stop_armed=False,
        )
        from pmacs.stop_loss_daemon import check_holding
        result, category = check_holding(h, current_price=84.0)
        assert result is not None
        assert result.stop_type == "FIXED_STOP"
        assert category == "FIXED"
