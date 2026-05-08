"""Tests for portfolio risk gate."""

from __future__ import annotations

from pmacs.engines.portfolio_risk_gate import (
    RiskGateInputs,
    evaluate_risk_gate,
)


def _make_inputs(**overrides) -> RiskGateInputs:
    defaults = dict(
        current_position_count=2,
        max_concurrent_positions=5,
        target_usd=500.0,
        portfolio_value_usd=5000.0,
        max_position_pct=0.20,
        sector="Tech",
        current_sector_exposure={"Tech": 0.10},
        max_sector_pct=0.40,
    )
    defaults.update(overrides)
    return RiskGateInputs(**defaults)


class TestRiskGate:
    """Test risk gate evaluation."""

    def test_passes_with_room(self):
        """All checks pass when within limits."""
        x = _make_inputs()  # 2/5 positions, 10% position, sector=10%+10%=20%
        result = evaluate_risk_gate(x)
        assert result.passed is True
        assert result.reasons == []

    def test_fails_on_position_count(self):
        """Fails when position count at max."""
        x = _make_inputs(current_position_count=5, max_concurrent_positions=5)
        result = evaluate_risk_gate(x)
        assert result.passed is False
        assert any("Position limit" in r for r in result.reasons)

    def test_fails_on_concentration(self):
        """Fails when single position exceeds max_position_pct."""
        x = _make_inputs(target_usd=1500.0, portfolio_value_usd=5000.0)  # 30% > 20%
        result = evaluate_risk_gate(x)
        assert result.passed is False
        assert any("Position concentration" in r for r in result.reasons)

    def test_fails_on_sector_exposure(self):
        """Fails when sector exposure exceeds max_sector_pct."""
        x = _make_inputs(
            target_usd=500.0,
            portfolio_value_usd=5000.0,  # 10% new position
            current_sector_exposure={"Tech": 0.35},  # +10% = 45% > 40%
            max_sector_pct=0.40,
        )
        result = evaluate_risk_gate(x)
        assert result.passed is False
        assert any("Sector exposure" in r for r in result.reasons)

    def test_no_sector_check_without_sector(self):
        """No sector check when sector is None."""
        x = _make_inputs(sector=None)
        result = evaluate_risk_gate(x)
        # Should pass — only position count and concentration, both within limits
        assert result.passed is True

    def test_no_sector_check_without_exposure_dict(self):
        """No sector check when current_sector_exposure is None."""
        x = _make_inputs(current_sector_exposure=None)
        result = evaluate_risk_gate(x)
        assert result.passed is True

    def test_zero_portfolio_value(self):
        """Zero portfolio value -> position_pct=0, passes concentration."""
        x = _make_inputs(portfolio_value_usd=0.0, target_usd=0.0)
        result = evaluate_risk_gate(x)
        # position_pct = 0/0 -> 0.0, passes concentration
        assert result.passed is True

    def test_multiple_failures(self):
        """Multiple failures all reported."""
        x = _make_inputs(
            current_position_count=5,
            max_concurrent_positions=5,
            target_usd=1500.0,
            portfolio_value_usd=5000.0,
            current_sector_exposure={"Tech": 0.35},
            max_sector_pct=0.40,
        )
        result = evaluate_risk_gate(x)
        assert result.passed is False
        assert len(result.reasons) == 3  # position limit + concentration + sector

    def test_new_sector_passes(self):
        """Position in a new sector (not in current_sector_exposure) passes."""
        x = _make_inputs(
            sector="Healthcare",
            current_sector_exposure={"Tech": 0.35},
        )
        result = evaluate_risk_gate(x)
        assert result.passed is True
