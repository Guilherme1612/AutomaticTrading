"""Tests for OpportunityCostEngine per-holding iteration.

Task 2 [C2, M1]: evaluate_holding, run_opportunity_cost_scan.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from pmacs.engines.opportunity_cost import (
    OpportunityCostResult,
    decide_hold_or_exit,
    evaluate_holding,
    run_opportunity_cost_scan,
)
from pmacs.schemas.contracts import Holding, HoldingState


def _make_holding(
    holding_id: str = "h-001",
    ticker: str = "TEST",
    conviction: float = 0.6,
    entry_price: float = 100.0,
    position_size: float = 1000.0,
) -> Holding:
    """Create a test Holding."""
    return Holding(
        id=holding_id,
        ticker=ticker,
        state=HoldingState.ACTIVE,
        cycle_id_opened="cycle-001",
        entry_date=date(2026, 1, 15),
        entry_price_usd=entry_price,
        position_size_usd=position_size,
        conviction_score=conviction,
        created_at=datetime(2026, 1, 15, 12, 0, 0),
        updated_at=datetime(2026, 1, 15, 12, 0, 0),
    )


class TestDecideHoldOrExit:
    """Core decide_hold_or_exit logic tests."""

    def test_high_conviction_stays_hold(self):
        """Holding with high conviction stays HOLD."""
        result = decide_hold_or_exit(
            holding_pnl_pct=5.0,
            days_held=10,
            alternative_expected_return_pct=8.0,
            conviction_at_entry=0.7,
            current_conviction=0.65,
        )
        assert result.action == "HOLD"

    def test_conviction_drop_triggers_exit(self):
        """Conviction drop > 0.3 + below 0.2 triggers EXIT."""
        result = decide_hold_or_exit(
            holding_pnl_pct=-2.0,
            days_held=10,
            alternative_expected_return_pct=5.0,
            conviction_at_entry=0.6,
            current_conviction=0.15,  # drop of 0.45 > 0.3, below 0.2
        )
        assert result.action == "EXIT"
        assert "Conviction dropped" in result.reason

    def test_underwater_with_better_alternatives_triggers_exit(self):
        """Underwater > 5% with alternatives 10%+ better triggers EXIT."""
        result = decide_hold_or_exit(
            holding_pnl_pct=-8.0,  # underwater 8%
            days_held=15,
            alternative_expected_return_pct=5.0,  # 5 > -8 + 10 = 2
            conviction_at_entry=0.5,
            current_conviction=0.45,
        )
        assert result.action == "EXIT"
        assert "Underwater" in result.reason

    def test_slight_conviction_drop_stays_hold(self):
        """Slight conviction drop that doesn't meet thresholds stays HOLD."""
        result = decide_hold_or_exit(
            holding_pnl_pct=2.0,
            days_held=5,
            alternative_expected_return_pct=3.0,
            conviction_at_entry=0.5,
            current_conviction=0.35,  # drop of 0.15, not > 0.3
        )
        assert result.action == "HOLD"


class TestEvaluateHolding:
    """evaluate_holding per-holding tests."""

    def test_evaluate_requires_cycle_id(self):
        """evaluate_holding raises ValueError without cycle_id (§16.5)."""
        holding = _make_holding()
        with pytest.raises(ValueError, match="cycle_id is REQUIRED"):
            evaluate_holding(holding, current_conviction=0.6, alternative_return_pct=5.0, cycle_id="")

    def test_exit_result_includes_correct_exit_state(self):
        """EXIT result has exit_state=EXIT_OPPORTUNITY_COST."""
        holding = _make_holding(conviction=0.6)
        result = evaluate_holding(
            holding,
            current_conviction=0.15,  # big drop from 0.6
            alternative_return_pct=5.0,
            cycle_id="cycle-001",
        )
        assert result.action == "EXIT"
        assert result.exit_state == HoldingState.EXIT_OPPORTUNITY_COST

    def test_hold_result_has_no_exit_state(self):
        """HOLD result has exit_state=None."""
        holding = _make_holding(conviction=0.6)
        result = evaluate_holding(
            holding,
            current_conviction=0.55,
            alternative_return_pct=3.0,
            cycle_id="cycle-001",
        )
        assert result.action == "HOLD"
        assert result.exit_state is None

    def test_evaluate_fills_holding_metadata(self):
        """Result includes holding_id and ticker."""
        holding = _make_holding(holding_id="h-xyz", ticker="AAPL", conviction=0.5)
        result = evaluate_holding(
            holding,
            current_conviction=0.45,
            alternative_return_pct=3.0,
            cycle_id="cycle-001",
        )
        assert result.holding_id == "h-xyz"
        assert result.ticker == "AAPL"


class TestRunOpportunityCostScan:
    """run_opportunity_cost_scan iteration tests."""

    def test_per_holding_iteration_one_result_each(self):
        """Scan produces one result per holding."""
        holdings = [
            _make_holding(holding_id="h-1", ticker="AAA", conviction=0.6),
            _make_holding(holding_id="h-2", ticker="BBB", conviction=0.5),
            _make_holding(holding_id="h-3", ticker="CCC", conviction=0.7),
        ]
        convictions = {"h-1": 0.55, "h-2": 0.45, "h-3": 0.65}

        results = run_opportunity_cost_scan(
            active_holdings=holdings,
            conviction_scores=convictions,
            alternative_return_pct=3.0,
            cycle_id="cycle-001",
        )

        assert len(results) == 3
        ids = {r.holding_id for r in results}
        assert ids == {"h-1", "h-2", "h-3"}

    def test_scan_with_pnl_overrides(self):
        """Scan with external pnl_pcts uses real PnL data."""
        holdings = [
            _make_holding(holding_id="h-1", ticker="AAA", conviction=0.6),
            _make_holding(holding_id="h-2", ticker="BBB", conviction=0.5),
        ]
        convictions = {"h-1": 0.55, "h-2": 0.45}
        pnl = {"h-1": -8.0, "h-2": 2.0}  # h-1 underwater

        results = run_opportunity_cost_scan(
            active_holdings=holdings,
            conviction_scores=convictions,
            alternative_return_pct=5.0,
            cycle_id="cycle-001",
            pnl_pcts=pnl,
        )

        assert len(results) == 2
        # h-1 is underwater 8% with alternative 5% (5 > -8+10=2) -> EXIT
        h1_result = next(r for r in results if r.holding_id == "h-1")
        assert h1_result.action == "EXIT"
        assert h1_result.exit_state == HoldingState.EXIT_OPPORTUNITY_COST

    def test_scan_requires_cycle_id(self):
        """Scan raises ValueError without cycle_id."""
        with pytest.raises(ValueError, match="cycle_id is REQUIRED"):
            run_opportunity_cost_scan(
                active_holdings=[],
                conviction_scores={},
                alternative_return_pct=5.0,
                cycle_id="",
            )

    def test_empty_holdings_returns_empty(self):
        """Empty holdings list returns empty results."""
        results = run_opportunity_cost_scan(
            active_holdings=[],
            conviction_scores={},
            alternative_return_pct=5.0,
            cycle_id="cycle-001",
        )
        assert results == []
