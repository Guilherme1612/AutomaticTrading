"""Tests for conviction engine."""

from __future__ import annotations

import pytest

from pmacs.engines.conviction import compute_conviction, verdict_tier
from pmacs.schemas.arbitration import Arbitrated, ArbitrationDecision
from pmacs.schemas.conviction import VerdictTier


def _make_arb(p_up: float = 0.6, p_down: float = 0.1, matured: int = 4) -> Arbitrated:
    return Arbitrated(
        ticker="TEST",
        cycle_id="c001",
        p_up=p_up,
        p_flat=1.0 - p_up - p_down,
        p_down=p_down,
        matured_sources_used=matured,
        decision=ArbitrationDecision.PROCEED,
    )


class TestComputeConviction:
    """Test compute_conviction with known inputs."""

    def test_basic_positive(self):
        """direction=0.5, maturity=1.0, crucible=0.0, ev=1.5 -> 0.5."""
        arb = _make_arb(p_up=0.6, p_down=0.1, matured=4)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=1.5,
        )
        # direction=0.5, maturity=1.0, crucible=1.0, ev=1.0
        assert result == pytest.approx(0.5)

    def test_bootstrap_floor(self):
        """Bootstrap mode: maturity_factor >= 0.50 even with 0 matured sources."""
        arb = _make_arb(p_up=0.7, p_down=0.1, matured=0)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=1.5,
            is_bootstrap=True,
        )
        # direction=0.6, maturity=floor(0/4, 0.50)=0.50, crucible=1.0, ev=1.0
        assert result == pytest.approx(0.3)

    def test_bootstrap_no_floor_above_050(self):
        """Bootstrap with 2 matured sources: 2/4=0.50, exactly at floor."""
        arb = _make_arb(p_up=0.7, p_down=0.1, matured=2)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=1.5,
            is_bootstrap=True,
        )
        # direction=0.6, maturity=0.50, crucible=1.0, ev=1.0
        assert result == pytest.approx(0.3)

    def test_non_bootstrap_zero_maturity(self):
        """Non-bootstrap: 0 matured -> maturity_factor floored at 0.25."""
        arb = _make_arb(p_up=0.7, p_down=0.1, matured=0)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=1.5,
            is_bootstrap=False,
        )
        # direction=0.6, maturity=0.25 (floor), crucible=1.0, ev=1.0
        assert result == pytest.approx(0.6 * 0.25)

    def test_negative_direction(self):
        """Negative direction (p_down > p_up) -> negative conviction."""
        arb = _make_arb(p_up=0.1, p_down=0.6, matured=4)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=1.5,
        )
        # direction=-0.5, maturity=1.0, crucible=1.0, ev=1.0
        assert result == pytest.approx(-0.5)

    def test_crucible_severity_full(self):
        """Crucible severity 1.0 -> crucible_factor=0.0 -> conviction=0.0."""
        arb = _make_arb(p_up=0.7, p_down=0.1, matured=4)
        result = compute_conviction(
            arb,
            crucible_severity=1.0,
            ev_multiple=1.5,
        )
        assert result == pytest.approx(0.0)

    def test_ev_multiple_capped(self):
        """ev_multiple > 1.5 -> ev_factor capped at 1.0."""
        arb = _make_arb(p_up=0.6, p_down=0.1, matured=4)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=3.0,
        )
        # direction=0.5, maturity=1.0, crucible=1.0, ev=min(3.0/1.5, 1.0)=1.0
        assert result == pytest.approx(0.5)

    def test_ev_multiple_fractional(self):
        """ev_multiple=0.75 -> ev_factor=0.5."""
        arb = _make_arb(p_up=0.6, p_down=0.1, matured=4)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=0.75,
        )
        # direction=0.5, maturity=1.0, crucible=1.0, ev=0.75/1.5=0.5
        assert result == pytest.approx(0.25)


class TestVerdictTier:
    """Test verdict_tier mapping."""

    def test_strong_buy(self):
        assert verdict_tier(0.7) == VerdictTier.STRONG_BUY

    def test_strong_buy_boundary(self):
        assert verdict_tier(0.6) == VerdictTier.STRONG_BUY

    def test_buy(self):
        assert verdict_tier(0.4) == VerdictTier.BUY

    def test_buy_boundary(self):
        assert verdict_tier(0.3) == VerdictTier.BUY

    def test_skip_low(self):
        assert verdict_tier(0.2) == VerdictTier.SKIP

    def test_skip_negative(self):
        assert verdict_tier(-0.1) == VerdictTier.SKIP

    def test_zero(self):
        assert verdict_tier(0.0) == VerdictTier.SKIP

    def test_active_holding_with_valid_thesis(self):
        """Active holding with valid thesis -> HOLD."""
        assert verdict_tier(0.1, is_active_holding=True, thesis_valid=True) == VerdictTier.HOLD

    def test_active_holding_invalid_thesis(self):
        """Active holding with invalid thesis -> normal tier logic."""
        assert verdict_tier(0.1, is_active_holding=True, thesis_valid=False) == VerdictTier.SKIP
