"""Unit tests for the EV pricing engine (pmacs/engines/pricing.py).

Architecture.md §9.4
"""

from __future__ import annotations

import pytest

from pmacs.engines.pricing import (
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TARGET_GAIN_PCT,
    MAX_STOP_LOSS_PCT,
    MIN_EV_THRESHOLD,
    EvInputs,
    compute_ev,
    compute_target_and_stop,
)


class TestComputeTargetAndStop:
    def test_defaults_when_atr_missing(self):
        target, stop = compute_target_and_stop(atr_pct=None)
        assert target == DEFAULT_TARGET_GAIN_PCT
        assert stop == DEFAULT_STOP_LOSS_PCT

    def test_defaults_when_atr_non_positive(self):
        target, stop = compute_target_and_stop(atr_pct=0.0)
        assert target == DEFAULT_TARGET_GAIN_PCT
        assert stop == DEFAULT_STOP_LOSS_PCT

    def test_target_and_stop_from_atr(self):
        # target = max(0.05, 1.5 * 0.08) = 0.12
        # stop   = min(0.15, max(0.10, 2.0 * 0.08)) = 0.15 capped -> wait 2*0.08=0.16 capped at 0.15
        target, stop = compute_target_and_stop(atr_pct=0.08)
        assert target == pytest.approx(0.12)
        assert stop == pytest.approx(0.15)

    def test_stop_capped_at_max(self):
        target, stop = compute_target_and_stop(atr_pct=0.20)
        assert target == pytest.approx(0.30)
        assert stop == pytest.approx(MAX_STOP_LOSS_PCT)
        assert stop <= MAX_STOP_LOSS_PCT

    def test_target_floor_at_five_percent(self):
        # Low ATR: 1.5 * 0.02 = 0.03, floored to 0.05
        target, stop = compute_target_and_stop(atr_pct=0.02)
        assert target == pytest.approx(0.05)
        assert stop == pytest.approx(0.10)


class TestComputeEv:
    def test_positive_ev(self):
        result = compute_ev(
            EvInputs(
                p_up=0.6,
                p_down=0.2,
                target_gain_pct=0.10,
                stop_loss_pct=0.15,
                current_price=100.0,
                cycle_id="c001",
            )
        )
        # EV = 0.6*0.10 - 0.2*0.15 = 0.06 - 0.03 = 0.03
        assert result.expected_value_pct == pytest.approx(0.03)
        assert result.ev_multiple == pytest.approx(0.03 / MIN_EV_THRESHOLD)
        assert result.is_positive is True

    def test_negative_ev(self):
        result = compute_ev(
            EvInputs(
                p_up=0.2,
                p_down=0.6,
                target_gain_pct=0.10,
                stop_loss_pct=0.15,
                current_price=100.0,
                cycle_id="c002",
            )
        )
        # EV = 0.2*0.10 - 0.6*0.15 = 0.02 - 0.09 = -0.07
        assert result.expected_value_pct == pytest.approx(-0.07)
        assert result.is_positive is False

    def test_zero_ev(self):
        result = compute_ev(
            EvInputs(
                p_up=0.5,
                p_down=0.5,
                target_gain_pct=0.10,
                stop_loss_pct=0.10,
                current_price=100.0,
                cycle_id="c003",
            )
        )
        assert result.expected_value_pct == pytest.approx(0.0)
        assert result.is_positive is False

    def test_explicit_target_stop_override(self):
        result = compute_ev(
            EvInputs(
                p_up=0.7,
                p_down=0.1,
                target_gain_pct=0.20,
                stop_loss_pct=0.12,
                atr_pct=0.05,  # would normally produce different values
                current_price=100.0,
                cycle_id="c004",
            )
        )
        # Explicit values differ from ATR-derived defaults, so they win
        assert result.target_gain_pct == pytest.approx(0.20)
        assert result.stop_loss_pct == pytest.approx(0.12)
        assert result.expected_value_pct == pytest.approx(0.7 * 0.20 - 0.1 * 0.12)

    def test_atr_resolves_target_and_stop_when_not_explicit(self):
        result = compute_ev(
            EvInputs(
                p_up=0.6,
                p_down=0.2,
                atr_pct=0.06,
                current_price=100.0,
                cycle_id="c005",
            )
        )
        # 1.5*0.06 = 0.09 target; 2.0*0.06 = 0.12 stop
        assert result.target_gain_pct == pytest.approx(0.09)
        assert result.stop_loss_pct == pytest.approx(0.12)

    def test_ev_multiple_when_above_threshold(self):
        # MIN_EV_THRESHOLD default is 0.01
        result = compute_ev(
            EvInputs(
                p_up=0.7,
                p_down=0.1,
                target_gain_pct=0.20,
                stop_loss_pct=0.10,
                current_price=100.0,
                cycle_id="c006",
            )
        )
        ev = 0.7 * 0.20 - 0.1 * 0.10
        assert result.ev_multiple == pytest.approx(ev / MIN_EV_THRESHOLD)
        assert result.ev_multiple > 1.0

    def test_ev_multiple_when_below_threshold(self):
        result = compute_ev(
            EvInputs(
                p_up=0.5,
                p_down=0.3,
                target_gain_pct=0.05,
                stop_loss_pct=0.05,
                current_price=100.0,
                cycle_id="c007",
            )
        )
        # EV = 0.5*0.05 - 0.3*0.05 = 0.01
        assert result.expected_value_pct == pytest.approx(0.01)
        assert result.ev_multiple == pytest.approx(1.0)

    def test_stop_loss_never_exceeds_catastrophe_net(self):
        result = compute_ev(
            EvInputs(
                p_up=0.6,
                p_down=0.2,
                atr_pct=0.50,
                current_price=100.0,
                cycle_id="c008",
            )
        )
        assert result.stop_loss_pct <= MAX_STOP_LOSS_PCT
