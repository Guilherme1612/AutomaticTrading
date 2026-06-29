"""Integration tests for Failure Diagnostic Engine (Agents.md §15).

Tests the deterministic 18-type classifier end-to-end:
  1. All 18 taxonomy types can be produced by the classifier
  2. Edge cases: missing data, ambiguous inputs, abort states
  3. ClassifyResult fields are well-formed (severity range, summary non-empty)
  4. HoldingContext dataclass wiring

No running services required — pure deterministic Python.

spec_ref: Agents.md §15, Architecture.md §9
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pmacs.engines.failure_diagnostic import ClassifyResult, HoldingContext, classify
from pmacs.schemas.contracts import HoldingState
from pmacs.schemas.failure import FailureTaxonomy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TICKER = "SMKT"
_HID = "hold-001"
_CID = "cycle-001"


def _ctx(**overrides) -> HoldingContext:
    """Build a HoldingContext with sensible defaults; override as needed."""
    defaults = dict(
        state=HoldingState.RESOLVED_DOWN,
        ticker=_TICKER,
        entry_price=100.0,
        exit_price=90.0,
        stop_loss_price=95.0,
        exit_reason=None,
        exit_date=None,
        actual_outcome="down",
        price_48h_after_exit=None,
        price_30d_after_exit=None,
        sector_drop_5d_pct=None,
        moat_strength=None,
        revenue_acceleration=None,
        forensics_flags=[],
        insider_signal=None,
        short_anomaly=None,
        realized_pnl_pct=None,
        expected_max_loss_pct=None,
        fill_slippage_pct=None,
        correlation_with_sector=None,
    )
    defaults.update(overrides)
    return HoldingContext(**defaults)


# ---------------------------------------------------------------------------
# Tests — all 18 taxonomy types
# ---------------------------------------------------------------------------


class TestAll18TaxonomyTypes:
    """Verify every FailureTaxonomy member is reachable via classify()."""

    def test_thesis_invalidated_fundamental(self) -> None:
        result = classify(
            _ctx(state=HoldingState.EXIT_THESIS_INVALIDATED,
                 exit_reason="fundamental data contradicted thesis"),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_FUNDAMENTAL
        assert "fundamental" in result.summary.lower()

    def test_thesis_invalidated_competitive(self) -> None:
        result = classify(
            _ctx(state=HoldingState.EXIT_THESIS_INVALIDATED,
                 exit_reason="competitive moat eroded"),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_COMPETITIVE
        assert "competitive" in result.summary.lower()

    def test_thesis_invalidated_regulatory(self) -> None:
        result = classify(
            _ctx(state=HoldingState.EXIT_THESIS_INVALIDATED,
                 exit_reason="regulatory action blocked pipeline"),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_REGULATORY
        assert "regulatory" in result.summary.lower()

    def test_catalyst_false_positive(self) -> None:
        result = classify(
            _ctx(state=HoldingState.RESOLVED_DOWN, actual_outcome="down"),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.CATALYST_FALSE_POSITIVE

    def test_catalyst_timeout(self) -> None:
        result = classify(
            _ctx(state=HoldingState.RESOLUTION_TIMEOUT, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.CATALYST_TIMEOUT
        assert "timed out" in result.summary.lower()

    def test_stop_hunted(self) -> None:
        """Price recovered above entry*1.02 within 48h -> STOP_HUNTED."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                exit_price=95.0,
                entry_price=100.0,
                price_48h_after_exit=103.0,  # > 100*1.02 = 102
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.STOP_HUNTED
        assert "48h" in result.summary

    def test_stop_loss_correct_with_30d(self) -> None:
        """Price stayed below stop for 30d -> STOP_LOSS_CORRECT."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                exit_price=95.0,
                stop_loss_price=95.0,
                price_30d_after_exit=90.0,  # < stop_loss_price
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT
        assert "30d" in result.summary

    def test_stop_loss_correct_default(self) -> None:
        """Stop without 48h recovery or 30d data -> STOP_LOSS_CORRECT default."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                exit_price=95.0,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT

    def test_exogenous_macro_shock(self) -> None:
        """Sector-wide drop >10% in 5d -> EXOGENOUS_MACRO_SHOCK."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                sector_drop_5d_pct=-12.5,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.EXOGENOUS_MACRO_SHOCK
        assert "-12.5" in result.summary

    def test_correlation_regime_shift(self) -> None:
        """High sector correlation but treated as idiosyncratic."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                correlation_with_sector=0.92,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.CORRELATION_REGIME_SHIFT
        assert "0.92" in result.summary

    def test_moat_drift_overestimate(self) -> None:
        """High moat score but thesis failed."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                moat_strength=0.85,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE
        assert "0.85" in result.summary

    def test_growth_stall_missed(self) -> None:
        """Revenue was ACCELERATING but thesis failed."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                revenue_acceleration="ACCELERATING",
                moat_strength=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.GROWTH_STALL_MISSED

    def test_forensics_flag_ignored(self) -> None:
        """Forensics raised flags that were underweighted."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                forensics_flags=["REVENUE_DIVERGENCE", "INVENTORY_BUILD"],
                moat_strength=None,
                revenue_acceleration=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.FORENSICS_FLAG_IGNORED
        assert "2" in result.summary  # 2 flags

    def test_insider_signal_false(self) -> None:
        """Insider buying was misleading."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                insider_signal="CLUSTER_BUY",
                forensics_flags=[],
                moat_strength=None,
                revenue_acceleration=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.INSIDER_SIGNAL_FALSE
        assert "CLUSTER_BUY" in result.summary

    def test_insider_signal_false_ceo(self) -> None:
        """CEO_BUY variant."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                insider_signal="CEO_BUY",
                forensics_flags=[],
                moat_strength=None,
                revenue_acceleration=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.INSIDER_SIGNAL_FALSE

    def test_short_interest_correct(self) -> None:
        """Short interest spike was correct."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                short_anomaly="SPIKE_UP",
                forensics_flags=[],
                moat_strength=None,
                revenue_acceleration=None,
                insider_signal=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.SHORT_INTEREST_CORRECT
        assert "short" in result.summary.lower()

    def test_sizing_overleveraged(self) -> None:
        """Realized loss > 2x expected max loss."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                realized_pnl_pct=-0.15,
                expected_max_loss_pct=0.05,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.SIZING_OVERLEVERAGED
        assert "2x" in result.summary

    def test_execution_slippage(self) -> None:
        """Fill slippage > 1%."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                fill_slippage_pct=1.5,
                realized_pnl_pct=-0.03,
                expected_max_loss_pct=0.10,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.EXECUTION_SLIPPAGE
        assert "1.5" in result.summary

    def test_opportunity_cost_exit_correct(self) -> None:
        """EXIT_OPPORTUNITY_COST -> OPPORTUNITY_COST_EXIT_CORRECT."""
        result = classify(
            _ctx(state=HoldingState.EXIT_OPPORTUNITY_COST, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.OPPORTUNITY_COST_EXIT_CORRECT

    def test_catalyst_false_positive_is_last_resort_for_down(self) -> None:
        """RESOLVED_DOWN with no persona-failure triggers -> CATALYST_FALSE_POSITIVE.

        The _classify_persona_failure function returns CATALYST_FALSE_POSITIVE as
        its final check for RESOLVED_DOWN/RESOLVED_MIXED before UNCLASSIFIED.
        """
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                # None of the persona-failure triggers present
                moat_strength=None,
                revenue_acceleration=None,
                forensics_flags=[],
                insider_signal=None,
                short_anomaly=None,
                realized_pnl_pct=None,
                fill_slippage_pct=None,
                correlation_with_sector=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.CATALYST_FALSE_POSITIVE

    def test_unclassified_abort_state(self) -> None:
        """ABORTED_PRE_LLM -> UNCLASSIFIED with severity 0."""
        result = classify(
            _ctx(state=HoldingState.ABORTED_PRE_LLM, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == 0.0

    def test_all_18_enums_covered(self) -> None:
        """Verify the FailureTaxonomy enum has 18 outcome + 5 reasoning-flaw members."""
        assert len(FailureTaxonomy) == 23


# ---------------------------------------------------------------------------
# Tests — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: missing data, ambiguous inputs, abort states."""

    def test_abort_pre_llm(self) -> None:
        result = classify(
            _ctx(state=HoldingState.ABORTED_PRE_LLM, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == 0.0
        assert "Aborted" in result.summary

    def test_abort_llm(self) -> None:
        result = classify(
            _ctx(state=HoldingState.ABORTED_LLM, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == 0.0

    def test_abort_risk(self) -> None:
        result = classify(
            _ctx(state=HoldingState.ABORTED_RISK, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == 0.0

    def test_panic_exit(self) -> None:
        result = classify(
            _ctx(state=HoldingState.PANIC_EXIT, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == 0.6
        assert "Force exit" in result.summary

    def test_exit_failed(self) -> None:
        result = classify(
            _ctx(state=HoldingState.EXIT_FAILED, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == 0.6

    def test_trailing_stop_routes_to_stop_classifier(self) -> None:
        """EXIT_TRAILING_STOP shares the stop classifier with STOPPED_OUT."""
        result = classify(
            _ctx(
                state=HoldingState.EXIT_TRAILING_STOP,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT

    def test_resolved_mixed_treated_as_down(self) -> None:
        """RESOLVED_MIXED triggers persona failure classifier."""
        result = classify(
            _ctx(state=HoldingState.RESOLVED_MIXED, actual_outcome=None),
            holding_id=_HID, cycle_id=_CID,
        )
        # Falls into _classify_persona_failure path
        assert result.primary in (
            FailureTaxonomy.CATALYST_FALSE_POSITIVE,
            FailureTaxonomy.UNCLASSIFIED,
        )

    def test_thesis_invalidation_default_fundamental(self) -> None:
        """EXIT_THESIS_INVALIDATED with no specific reason -> FUNDAMENTAL default."""
        result = classify(
            _ctx(
                state=HoldingState.EXIT_THESIS_INVALIDATED,
                exit_reason=None,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_FUNDAMENTAL
        assert result.severity == 0.5

    def test_thesis_invalidation_empty_reason(self) -> None:
        """Empty exit_reason -> FUNDAMENTAL default."""
        result = classify(
            _ctx(
                state=HoldingState.EXIT_THESIS_INVALIDATED,
                exit_reason="",
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_FUNDAMENTAL

    def test_stop_with_48h_not_recovered(self) -> None:
        """48h price exists but did not recover -> checks 30d."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                entry_price=100.0,
                price_48h_after_exit=98.0,  # < 100*1.02 = 102
                price_30d_after_exit=85.0,  # < stop_loss_price
                stop_loss_price=95.0,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT

    def test_sector_drop_exactly_10pct_not_shock(self) -> None:
        """Sector drop exactly -10.0 does NOT trigger macro shock (must be < -10)."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                sector_drop_5d_pct=-10.0,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary != FailureTaxonomy.EXOGENOUS_MACRO_SHOCK

    def test_sector_drop_just_below_threshold(self) -> None:
        """Sector drop -10.01% triggers macro shock."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                sector_drop_5d_pct=-10.01,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.EXOGENOUS_MACRO_SHOCK

    def test_slippage_exactly_1pct_not_triggered(self) -> None:
        """Fill slippage exactly 1.0 does NOT trigger (must be > 1.0)."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                fill_slippage_pct=1.0,
                realized_pnl_pct=-0.03,
                expected_max_loss_pct=0.10,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        # Falls through to CATALYST_FALSE_POSITIVE (RESOLVED_DOWN)
        assert result.primary == FailureTaxonomy.CATALYST_FALSE_POSITIVE

    def test_sizing_exactly_2x_not_triggered(self) -> None:
        """Realized loss exactly 2x expected does NOT trigger (must be > 2x)."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                realized_pnl_pct=-0.10,
                expected_max_loss_pct=0.05,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        # Falls through: |0.10| == 2*0.05, not > 2*0.05
        assert result.primary != FailureTaxonomy.SIZING_OVERLEVERAGED


# ---------------------------------------------------------------------------
# Tests — ClassifyResult well-formedness
# ---------------------------------------------------------------------------


class TestClassifyResultWellFormed:
    """ClassifyResult invariants hold for every classification."""

    @pytest.mark.parametrize(
        "state,outcome,extra",
        [
            (HoldingState.STOPPED_OUT, None, {}),
            (HoldingState.EXIT_THESIS_INVALIDATED, None,
             {"exit_reason": "fundamental breakdown"}),
            (HoldingState.EXIT_OPPORTUNITY_COST, None, {}),
            (HoldingState.RESOLVED_DOWN, "down", {}),
            (HoldingState.RESOLUTION_TIMEOUT, None, {}),
            (HoldingState.ABORTED_PRE_LLM, None, {}),
            (HoldingState.PANIC_EXIT, None, {}),
            (HoldingState.EXIT_TRAILING_STOP, None, {}),
            (HoldingState.EXIT_FAILED, None, {}),
            (HoldingState.ABORTED_LLM, None, {}),
            (HoldingState.ABORTED_RISK, None, {}),
            (HoldingState.RESOLVED_MIXED, None, {}),
        ],
    )
    def test_result_well_formed(self, state, outcome, extra) -> None:
        """Every classification produces a valid ClassifyResult."""
        ctx = _ctx(state=state, actual_outcome=outcome, **extra)
        result = classify(ctx, holding_id=_HID, cycle_id=_CID)
        assert isinstance(result, ClassifyResult)
        assert isinstance(result.primary, FailureTaxonomy)
        assert 0.0 <= result.severity <= 1.0
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0
        assert result.holding_id == _HID
        assert result.cycle_id == _CID

    def test_holding_id_and_cycle_id_propagated(self) -> None:
        """Custom holding_id and cycle_id are preserved in result."""
        result = classify(
            _ctx(state=HoldingState.STOPPED_OUT, actual_outcome=None),
            holding_id="custom-hold-42",
            cycle_id="custom-cycle-99",
        )
        assert result.holding_id == "custom-hold-42"
        assert result.cycle_id == "custom-cycle-99"

    def test_empty_holding_id_by_default(self) -> None:
        """Without holding_id/cycle_id, empty strings are used."""
        result = classify(_ctx(state=HoldingState.STOPPED_OUT, actual_outcome=None))
        assert result.holding_id == ""
        assert result.cycle_id == ""


# ---------------------------------------------------------------------------
# Tests — persona failure priority order
# ---------------------------------------------------------------------------


class TestPersonaFailurePriority:
    """Verify persona failure checks happen in the correct priority order."""

    def test_sizing_checked_before_slippage(self) -> None:
        """SIZING_OVERLEVERAGED has higher priority than EXECUTION_SLIPPAGE."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                realized_pnl_pct=-0.20,
                expected_max_loss_pct=0.05,
                fill_slippage_pct=2.0,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.SIZING_OVERLEVERAGED

    def test_slippage_checked_before_moat(self) -> None:
        """EXECUTION_SLIPPAGE has higher priority than MOAT_DRIFT_OVERESTIMATE."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                realized_pnl_pct=-0.03,
                expected_max_loss_pct=0.10,
                fill_slippage_pct=2.0,
                moat_strength=0.9,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.EXECUTION_SLIPPAGE

    def test_moat_checked_before_growth(self) -> None:
        """MOAT_DRIFT_OVERESTIMATE has higher priority than GROWTH_STALL_MISSED."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                moat_strength=0.85,
                revenue_acceleration="ACCELERATING",
                realized_pnl_pct=None,
                fill_slippage_pct=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE

    def test_forensics_checked_before_insider(self) -> None:
        """FORENSICS_FLAG_IGNORED has higher priority than INSIDER_SIGNAL_FALSE."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                forensics_flags=["REVENUE_DIVERGENCE"],
                insider_signal="CLUSTER_BUY",
                moat_strength=None,
                revenue_acceleration=None,
                realized_pnl_pct=None,
                fill_slippage_pct=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.FORENSICS_FLAG_IGNORED

    def test_short_checked_before_correlation(self) -> None:
        """SHORT_INTEREST_CORRECT has higher priority than CORRELATION_REGIME_SHIFT."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                short_anomaly="SPIKE_UP",
                correlation_with_sector=0.95,
                forensics_flags=[],
                moat_strength=None,
                revenue_acceleration=None,
                insider_signal=None,
                realized_pnl_pct=None,
                fill_slippage_pct=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.SHORT_INTEREST_CORRECT

    def test_correlation_checked_before_catalyst_false_positive(self) -> None:
        """CORRELATION_REGIME_SHIFT has higher priority than CATALYST_FALSE_POSITIVE."""
        result = classify(
            _ctx(
                state=HoldingState.RESOLVED_DOWN,
                actual_outcome="down",
                correlation_with_sector=0.9,
                forensics_flags=[],
                moat_strength=None,
                revenue_acceleration=None,
                insider_signal=None,
                short_anomaly=None,
                realized_pnl_pct=None,
                fill_slippage_pct=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.CORRELATION_REGIME_SHIFT


# ---------------------------------------------------------------------------
# Tests — stop classifier sub-paths
# ---------------------------------------------------------------------------


class TestStopClassifierPriority:
    """Verify stop sub-classifier priority: macro shock > stop hunted > 30d > default."""

    def test_macro_shock_beats_stop_hunted(self) -> None:
        """If both sector drop and 48h recovery exist, macro shock wins."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                sector_drop_5d_pct=-15.0,
                entry_price=100.0,
                price_48h_after_exit=105.0,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.EXOGENOUS_MACRO_SHOCK

    def test_stop_hunted_beats_30d(self) -> None:
        """48h recovery present -> STOP_HUNTED even if 30d data available."""
        result = classify(
            _ctx(
                state=HoldingState.STOPPED_OUT,
                entry_price=100.0,
                price_48h_after_exit=104.0,
                price_30d_after_exit=80.0,
                stop_loss_price=95.0,
                actual_outcome=None,
            ),
            holding_id=_HID, cycle_id=_CID,
        )
        assert result.primary == FailureTaxonomy.STOP_HUNTED
