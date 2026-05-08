"""Unit tests for trailing stop engine."""
from __future__ import annotations

import pytest

from pmacs.engines.trailing_stop import (
    TrailingStopState,
    compute_profit_r,
    maybe_arm_trailing,
    maybe_ratchet_trailing,
)


class TestComputeProfitR:
    """Tests for compute_profit_r."""

    def test_basic_profit(self) -> None:
        """entry=100, current=115, stop=95 -> R=5, profit=15 -> profit_r=3.0."""
        result = compute_profit_r(entry_price=100.0, current_price=115.0, stop_loss_price=95.0)
        assert result == 3.0

    def test_no_profit_at_entry(self) -> None:
        """At entry price, profit_r = 0."""
        result = compute_profit_r(entry_price=100.0, current_price=100.0, stop_loss_price=95.0)
        assert result == 0.0

    def test_loss_below_entry(self) -> None:
        """Below entry but above stop: negative profit_r."""
        result = compute_profit_r(entry_price=100.0, current_price=97.0, stop_loss_price=95.0)
        assert result == -0.6

    def test_zero_risk_returns_zero(self) -> None:
        """If entry == stop (zero risk), return 0 to avoid division by zero."""
        result = compute_profit_r(entry_price=100.0, current_price=115.0, stop_loss_price=100.0)
        assert result == 0.0

    def test_at_stop_loss(self) -> None:
        """At stop loss: profit_r = -1.0 (full R loss)."""
        result = compute_profit_r(entry_price=100.0, current_price=95.0, stop_loss_price=95.0)
        assert result == -1.0


class TestMaybeArmTrailing:
    """Tests for maybe_arm_trailing."""

    def test_not_armed_low_profit_r(self) -> None:
        """profit_r=1.0 -> not armed (need > 1.5)."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=105.0,  # profit_r = 5/5 = 1.0
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert state.armed is False
        assert state.trailing_stop_price == 0.0

    def test_armed_at_1_5r_plus(self) -> None:
        """profit_r=2.0 -> armed with trailing = current - ATR."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=110.0,  # profit_r = 10/5 = 2.0
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert state.armed is True
        assert state.trailing_stop_price == pytest.approx(107.0)  # 110 - 3
        assert state.profit_r_at_arm == pytest.approx(2.0)

    def test_not_armed_exactly_1_5r(self) -> None:
        """profit_r exactly 1.5 -> not armed (need > 1.5, strictly)."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=107.5,  # profit_r = 7.5/5 = 1.5
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=False,
        )
        assert state.armed is False

    def test_already_armed_returns_armed_state(self) -> None:
        """If already armed, return armed=True state (caller handles ratchet)."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=110.0,
            stop_loss_price=95.0,
            atr_20=3.0,
            is_armed=True,
        )
        assert state.armed is True
        # trailing_stop_price is 0.0 because caller should use ratchet
        assert state.trailing_stop_price == 0.0

    def test_armed_with_different_atr(self) -> None:
        """Trailing distance scales with ATR."""
        state = maybe_arm_trailing(
            entry_price=100.0,
            current_price=115.0,  # profit_r = 15/5 = 3.0
            stop_loss_price=95.0,
            atr_20=5.0,
            is_armed=False,
        )
        assert state.armed is True
        assert state.trailing_stop_price == pytest.approx(110.0)  # 115 - 5


class TestMaybeRatchetTrailing:
    """Tests for maybe_ratchet_trailing."""

    def test_ratchet_up(self) -> None:
        """New trailing higher -> update."""
        result = maybe_ratchet_trailing(
            current_price=120.0,
            atr_20=3.0,
            current_trailing=110.0,
        )
        assert result == pytest.approx(117.0)  # 120 - 3 > 110

    def test_ratchet_down_prevented(self) -> None:
        """New trailing lower -> keep current."""
        result = maybe_ratchet_trailing(
            current_price=105.0,
            atr_20=3.0,
            current_trailing=110.0,
        )
        # 105 - 3 = 102 < 110, so keep 110
        assert result == pytest.approx(110.0)

    def test_ratchet_equal(self) -> None:
        """New trailing exactly equal -> keep current (no change)."""
        result = maybe_ratchet_trailing(
            current_price=113.0,
            atr_20=3.0,
            current_trailing=110.0,
        )
        # 113 - 3 = 110 == 110, so keep 110
        assert result == pytest.approx(110.0)

    def test_ratchet_large_price_move(self) -> None:
        """Large favorable move -> significant ratchet up."""
        result = maybe_ratchet_trailing(
            current_price=150.0,
            atr_20=4.0,
            current_trailing=110.0,
        )
        assert result == pytest.approx(146.0)  # 150 - 4
