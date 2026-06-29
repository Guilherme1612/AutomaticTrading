"""Tests for conviction engine."""

from __future__ import annotations

import pytest

from pmacs.engines.conviction import (
    compute_conviction,
    evaluate_pass_signal,
    verdict_tier,
)
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

    def test_bootstrap_full_maturity(self):
        """Bootstrap mode: maturity_factor=1.0 regardless of matured_sources_used."""
        arb = _make_arb(p_up=0.7, p_down=0.1, matured=0)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=1.5,
            is_bootstrap=True,
        )
        # direction=0.6, maturity=1.0 (bootstrap bypasses dampening), crucible=1.0, ev=1.0
        assert result == pytest.approx(0.6)

    def test_bootstrap_matured_sources_ignored(self):
        """Bootstrap with 2 matured sources: maturity_factor still 1.0 in bootstrap."""
        arb = _make_arb(p_up=0.7, p_down=0.1, matured=2)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=1.5,
            is_bootstrap=True,
        )
        # direction=0.6, maturity=1.0 (bootstrap ignores matured_sources), crucible=1.0, ev=1.0
        assert result == pytest.approx(0.6)

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

    def test_negative_ev_multiple_suppresses_to_zero(self):
        """Negative ev_multiple suppresses conviction to 0, does NOT invert it."""
        arb = _make_arb(p_up=0.7, p_down=0.1, matured=4)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=-1.0,
        )
        # ev_factor = max(0.0, -1.0/1.5) = 0.0 -> conviction = 0.0
        assert result == pytest.approx(0.0)

    def test_bearish_negative_ev_does_not_double_negate(self):
        """Bearish direction + negative EV must not produce a positive conviction."""
        arb = _make_arb(p_up=0.1, p_down=0.6, matured=4)
        result = compute_conviction(
            arb,
            crucible_severity=0.0,
            ev_multiple=-1.0,
        )
        # Old bug: negative * negative = positive (false BUY). Fixed: ev_factor=0 -> 0.
        assert result == pytest.approx(0.0)


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


class TestVerdictTierPASS:
    """PASS verdict = active no-bid (allocator-grade memo).

    PASS overrides the conviction floor. Two triggers:
      - R:R < 1.5
      - comps empty AND growth < 10%
    """

    def test_pass_when_rr_below_threshold(self):
        # 0.6 conviction would normally be STRONG_BUY; R:R=1.0 is the active no-bid
        assert verdict_tier(0.6, rr_ratio=1.0) == VerdictTier.PASS

    def test_pass_when_rr_at_threshold_boundary(self):
        # R:R exactly 1.5 → NOT pass (strict less-than)
        assert verdict_tier(0.6, rr_ratio=1.5) != VerdictTier.PASS

    def test_pass_when_comps_empty_growth_low(self):
        # No comps, growth 5% → PASS
        assert verdict_tier(0.6, comparable_transactions=[], growth_pct=0.05) == VerdictTier.PASS

    def test_no_pass_when_comps_present_even_low_growth(self):
        # Comps present → don't PASS on growth alone
        assert verdict_tier(0.6, comparable_transactions=[{"target": "X"}], growth_pct=0.05) != VerdictTier.PASS

    def test_no_pass_when_growth_high_even_no_comps(self):
        # Growth high → don't PASS on comps alone
        assert verdict_tier(0.6, comparable_transactions=[], growth_pct=0.25) != VerdictTier.PASS

    def test_pass_overrides_low_conviction(self):
        # 0.1 conviction would be SKIP; with R:R trigger, PASS wins
        assert verdict_tier(0.1, rr_ratio=0.8) == VerdictTier.PASS

    def test_no_pass_when_rr_none_and_growth_unknown(self):
        # No triggers → fall through to normal logic
        assert verdict_tier(0.1, rr_ratio=None, comparable_transactions=None, growth_pct=None) == VerdictTier.SKIP


class TestEvaluatePassSignal:
    """evaluate_pass_signal is the source of truth for PASS triggers."""

    def test_rr_below_threshold_returns_reason(self):
        s = evaluate_pass_signal(rr_ratio=0.5, comparable_transactions=None, growth_pct=None)
        assert s.triggered
        assert "0.50" in s.reason
        assert s.reason_code == "rr_below_threshold"

    def test_comps_empty_growth_low(self):
        s = evaluate_pass_signal(rr_ratio=None, comparable_transactions=[], growth_pct=0.05)
        assert s.triggered
        assert s.reason_code == "comps_empty_growth_below_threshold"

    def test_no_signal(self):
        s = evaluate_pass_signal(rr_ratio=3.0, comparable_transactions=[{"x": 1}], growth_pct=0.20)
        assert not s.triggered
        assert s.reason == ""

    def test_rr_takes_precedence_over_comps(self):
        # When both triggers fire, RR wins (operator-analyst judgment first)
        s = evaluate_pass_signal(rr_ratio=0.8, comparable_transactions=[], growth_pct=0.05)
        assert s.reason_code == "rr_below_threshold"
