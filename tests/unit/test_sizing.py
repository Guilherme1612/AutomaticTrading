"""Tests for sizing engine."""

from __future__ import annotations

import pytest

from pmacs.engines.sizing import (
    SizingInputs,
    compute_kelly,
    size_position,
)


class TestComputeKelly:
    """Test Kelly fraction computation."""

    def test_basic_positive(self):
        """Known inputs -> known Kelly fraction."""
        # p_up=0.6, gain=0.10, p_down=0.2, loss=0.05
        # kelly = (0.6*0.10 - 0.2*0.05) / 0.05 = (0.06 - 0.01) / 0.05 = 1.0
        kelly = compute_kelly(0.6, 0.2, 0.10, 0.05)
        assert kelly == pytest.approx(1.0)

    def test_zero_loss(self):
        """Zero stop loss -> 0.0 Kelly."""
        assert compute_kelly(0.6, 0.2, 0.10, 0.0) == 0.0

    def test_negative_kelly(self):
        """No edge -> negative Kelly."""
        # p_up=0.2, gain=0.05, p_down=0.5, loss=0.10
        # kelly = (0.2*0.05 - 0.5*0.10) / 0.10 = (0.01 - 0.05) / 0.10 = -0.4
        kelly = compute_kelly(0.2, 0.5, 0.05, 0.10)
        assert kelly == pytest.approx(-0.4)


def _make_inputs(**overrides) -> SizingInputs:
    defaults = dict(
        p_up=0.6,
        p_down=0.2,
        target_gain_pct=0.10,
        stop_loss_pct=0.05,
        matured_sources_used=4,
        is_limited_history=False,
        portfolio_correlations=[],
        max_position_pct=0.20,
        portfolio_value_usd=5000.0,
        current_price=100.0,
    )
    defaults.update(overrides)
    return SizingInputs(**defaults)


class TestSizePosition:
    """Test position sizing with haircuts."""

    def test_basic_half_kelly(self):
        """Basic case: half-Kelly, no haircuts, capped at max_position_pct."""
        x = _make_inputs(p_up=0.6, p_down=0.2, target_gain_pct=0.10, stop_loss_pct=0.05)
        # kelly=1.0, half_kelly=0.5, all factors=1.0, target_pct=0.5, capped at 0.20
        result = size_position(x)
        assert result.target_usd == pytest.approx(1000.0)  # 0.20 * 5000
        assert result.target_shares == pytest.approx(10.0)  # 1000/100
        assert result.abort_reason is None
        assert result.applied_haircuts["bootstrap"] == 1.0
        assert result.applied_haircuts["limited_history"] == 1.0

    def test_bootstrap_haircut_zero_mature(self):
        """0 matured sources -> 0.50 bootstrap factor."""
        x = _make_inputs(matured_sources_used=0)
        result = size_position(x)
        assert result.applied_haircuts["bootstrap"] == pytest.approx(0.50)

    def test_bootstrap_haircut_one_mature(self):
        """1 matured source -> 0.65 factor."""
        x = _make_inputs(matured_sources_used=1)
        result = size_position(x)
        assert result.applied_haircuts["bootstrap"] == pytest.approx(0.65)

    def test_bootstrap_haircut_four_mature(self):
        """4+ matured sources -> 1.0 factor (no haircut)."""
        x = _make_inputs(matured_sources_used=4)
        result = size_position(x)
        assert result.applied_haircuts["bootstrap"] == pytest.approx(1.0)

    def test_bootstrap_haircut_five_mature(self):
        """5 matured sources -> still 1.0 (capped)."""
        x = _make_inputs(matured_sources_used=5)
        result = size_position(x)
        assert result.applied_haircuts["bootstrap"] == pytest.approx(1.0)

    def test_limited_history_stacking(self):
        """Limited history + 0 matured: bootstrap=0.50 * limited=0.50 = 0.25."""
        x = _make_inputs(matured_sources_used=0, is_limited_history=True)
        result = size_position(x)
        assert result.applied_haircuts["bootstrap"] == pytest.approx(0.50)
        assert result.applied_haircuts["limited_history"] == pytest.approx(0.50)
        # Combined: 0.50 * 0.50 = 0.25 of what would otherwise be

    def test_negative_kelly_abort(self):
        """Negative Kelly -> abort with reason."""
        x = _make_inputs(p_up=0.2, p_down=0.5, target_gain_pct=0.05, stop_loss_pct=0.10)
        result = size_position(x)
        assert result.abort_reason == "NEGATIVE_KELLY_NO_EDGE"
        assert result.target_usd == 0.0
        assert result.target_shares == 0.0

    def test_max_position_cap(self):
        """Position capped at max_position_pct (20%)."""
        # Kelly=1.0, half=0.5, all factors=1.0 -> 0.5, but capped at 0.20
        x = _make_inputs(p_up=0.6, p_down=0.2, target_gain_pct=0.10, stop_loss_pct=0.05)
        result = size_position(x)
        assert result.target_usd == pytest.approx(1000.0)  # 0.20 * 5000

    def test_no_cap_when_below_max(self):
        """No cap when target_pct < max_position_pct."""
        # Small Kelly -> target below max
        x = _make_inputs(
            p_up=0.4, p_down=0.3, target_gain_pct=0.05, stop_loss_pct=0.05,
            matured_sources_used=4, is_limited_history=False,
        )
        # kelly = (0.4*0.05 - 0.3*0.05) / 0.05 = (0.02-0.015)/0.05 = 0.1
        # half_kelly = 0.05, all factors=1.0 -> 0.05
        result = size_position(x)
        assert result.target_usd == pytest.approx(250.0)  # 0.05 * 5000
        assert result.abort_reason is None

    def test_correlation_factor(self):
        """High correlation -> reduced size."""
        x = _make_inputs(portfolio_correlations=[0.8])
        # correlation_factor = max(0.3, 1.0 - 0.8) = 0.3 (floor)
        result = size_position(x)
        assert result.applied_haircuts["correlation"] == pytest.approx(0.3)

    def test_correlation_factor_floor(self):
        """Correlation factor floors at 0.3."""
        x = _make_inputs(portfolio_correlations=[0.9])
        result = size_position(x)
        assert result.applied_haircuts["correlation"] == pytest.approx(0.3)

    def test_no_correlation(self):
        """No correlations -> factor=1.0."""
        x = _make_inputs(portfolio_correlations=[])
        result = size_position(x)
        assert result.applied_haircuts["correlation"] == pytest.approx(1.0)

    def test_zero_price(self):
        """Zero price -> 0 shares."""
        x = _make_inputs(current_price=0.0)
        result = size_position(x)
        assert result.target_shares == 0.0
