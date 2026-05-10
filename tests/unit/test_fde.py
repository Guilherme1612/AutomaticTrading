"""Tests for Failure Diagnostic Engine, Dead-Letter Queue, Reconciliation,
and Cross-DB Consistency (PMACS Phase 6 / Build Phase 12).

All 18 taxonomy types from ``pmacs.schemas.failure.FailureTaxonomy`` are
exercised, plus the auxiliary modules.
"""

from __future__ import annotations

import pytest

from pmacs.schemas.failure import FailureTaxonomy
from pmacs.engines.failure_diagnostic import HoldingContext, classify, ClassifyResult
from pmacs.engines.reconciliation import reconcile_paper_ledger, ReconciliationResult
from pmacs.storage.consistency import check_cross_db_consistency, ConsistencyResult
from pmacs.logsys.dead_letter import DeadLetterQueue, DeadLetterEntry


# ======================================================================
# Helpers
# ======================================================================

def _ctx(**overrides) -> HoldingContext:
    """Build a ``HoldingContext`` with sensible defaults."""
    defaults = dict(
        state="RESOLVED_DOWN",
        ticker="TEST",
        entry_price=100.0,
    )
    defaults.update(overrides)
    return HoldingContext(**defaults)


# ======================================================================
# 1. THESIS_INVALIDATED_FUNDAMENTAL
# ======================================================================

class TestThesisInvalidatedFundamental:
    def test_fundamental_reason(self) -> None:
        result = classify(
            _ctx(
                state="EXIT_THESIS_INVALIDATED",
                exit_reason="fundamental data deteriorated",
            )
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_FUNDAMENTAL
        assert result.severity == pytest.approx(0.6)

    def test_generic_exit_reason_defaults_to_fundamental(self) -> None:
        result = classify(
            _ctx(
                state="EXIT_THESIS_INVALIDATED",
                exit_reason="data changed",
            )
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_FUNDAMENTAL
        assert result.severity == pytest.approx(0.5)


# ======================================================================
# 2. THESIS_INVALIDATED_COMPETITIVE
# ======================================================================

class TestThesisInvalidatedCompetitive:
    def test_competitive_reason(self) -> None:
        result = classify(
            _ctx(
                state="EXIT_THESIS_INVALIDATED",
                exit_reason="competitive moat eroded",
            )
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_COMPETITIVE

    def test_moat_keyword(self) -> None:
        result = classify(
            _ctx(
                state="EXIT_THESIS_INVALIDATED",
                exit_reason="moat weakened by new entrant",
            )
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_COMPETITIVE


# ======================================================================
# 3. THESIS_INVALIDATED_REGULATORY
# ======================================================================

class TestThesisInvalidatedRegulatory:
    def test_regulatory_reason(self) -> None:
        result = classify(
            _ctx(
                state="EXIT_THESIS_INVALIDATED",
                exit_reason="regulatory action halted operations",
            )
        )
        assert result.primary == FailureTaxonomy.THESIS_INVALIDATED_REGULATORY
        assert result.severity == pytest.approx(0.6)


# ======================================================================
# 4. CATALYST_FALSE_POSITIVE
# ======================================================================

class TestCatalystFalsePositive:
    def test_resolved_down_no_other_signals(self) -> None:
        result = classify(
            _ctx(actual_outcome="down", state="RESOLVED_DOWN")
        )
        assert result.primary == FailureTaxonomy.CATALYST_FALSE_POSITIVE
        assert result.severity == pytest.approx(0.4)

    def test_resolved_mixed(self) -> None:
        result = classify(
            _ctx(actual_outcome="down", state="RESOLVED_MIXED")
        )
        assert result.primary == FailureTaxonomy.CATALYST_FALSE_POSITIVE


# ======================================================================
# 5. CATALYST_TIMEOUT
# ======================================================================

class TestCatalystTimeout:
    def test_resolution_timeout(self) -> None:
        result = classify(_ctx(state="RESOLUTION_TIMEOUT"))
        assert result.primary == FailureTaxonomy.CATALYST_TIMEOUT
        assert result.severity == pytest.approx(0.5)


# ======================================================================
# 6. STOP_HUNTED
# ======================================================================

class TestStopHunted:
    def test_price_recovered_48h(self) -> None:
        result = classify(
            _ctx(
                state="STOPPED_OUT",
                entry_price=100.0,
                exit_price=95.0,
                price_48h_after_exit=103.0,
            )
        )
        assert result.primary == FailureTaxonomy.STOP_HUNTED
        assert result.severity == pytest.approx(0.7)

    def test_no_recovery(self) -> None:
        result = classify(
            _ctx(
                state="STOPPED_OUT",
                entry_price=100.0,
                exit_price=95.0,
                price_48h_after_exit=94.0,
            )
        )
        assert result.primary != FailureTaxonomy.STOP_HUNTED

    def test_trailing_stop_recovered(self) -> None:
        result = classify(
            _ctx(
                state="EXIT_TRAILING_STOP",
                entry_price=100.0,
                exit_price=97.0,
                price_48h_after_exit=104.0,
            )
        )
        assert result.primary == FailureTaxonomy.STOP_HUNTED


# ======================================================================
# 7. STOP_LOSS_CORRECT
# ======================================================================

class TestStopLossCorrect:
    def test_price_stayed_below_stop_30d(self) -> None:
        result = classify(
            _ctx(
                state="STOPPED_OUT",
                entry_price=100.0,
                exit_price=90.0,
                stop_loss_price=91.0,
                price_30d_after_exit=80.0,
            )
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT
        assert result.severity == pytest.approx(0.2)

    def test_default_stop(self) -> None:
        result = classify(
            _ctx(state="STOPPED_OUT", entry_price=100.0, exit_price=90.0)
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT
        assert result.severity == pytest.approx(0.3)


# ======================================================================
# 8. EXOGENOUS_MACRO_SHOCK
# ======================================================================

class TestExogenousMacroShock:
    def test_sector_crash(self) -> None:
        result = classify(
            _ctx(
                state="STOPPED_OUT",
                entry_price=100.0,
                exit_price=80.0,
                sector_drop_5d_pct=-15.0,
            )
        )
        assert result.primary == FailureTaxonomy.EXOGENOUS_MACRO_SHOCK
        assert result.severity == pytest.approx(0.4)

    def test_moderate_sector_drop(self) -> None:
        result = classify(
            _ctx(
                state="STOPPED_OUT",
                entry_price=100.0,
                exit_price=95.0,
                sector_drop_5d_pct=-5.0,
            )
        )
        assert result.primary != FailureTaxonomy.EXOGENOUS_MACRO_SHOCK


# ======================================================================
# 9. CORRELATION_REGIME_SHIFT
# ======================================================================

class TestCorrelationRegimeShift:
    def test_high_correlation(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                correlation_with_sector=0.92,
            )
        )
        assert result.primary == FailureTaxonomy.CORRELATION_REGIME_SHIFT

    def test_low_correlation(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                correlation_with_sector=0.3,
            )
        )
        assert result.primary != FailureTaxonomy.CORRELATION_REGIME_SHIFT


# ======================================================================
# 10. MOAT_DRIFT_OVERESTIMATE
# ======================================================================

class TestMoatDriftOverestimate:
    def test_moat_high_outcome_down(self) -> None:
        result = classify(
            _ctx(actual_outcome="down", moat_strength=0.85, state="RESOLVED_DOWN")
        )
        assert result.primary == FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE
        assert result.severity == pytest.approx(0.5)

    def test_moat_boundary(self) -> None:
        result = classify(
            _ctx(actual_outcome="down", moat_strength=0.71, state="RESOLVED_DOWN")
        )
        assert result.primary == FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE

    def test_moat_below_threshold(self) -> None:
        result = classify(
            _ctx(actual_outcome="down", moat_strength=0.5, state="RESOLVED_DOWN")
        )
        assert result.primary != FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE


# ======================================================================
# 11. GROWTH_STALL_MISSED
# ======================================================================

class TestGrowthStallMissed:
    def test_growth_accelerating_but_failed(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                revenue_acceleration="ACCELERATING",
            )
        )
        assert result.primary == FailureTaxonomy.GROWTH_STALL_MISSED
        assert result.severity == pytest.approx(0.5)

    def test_growth_decelerating(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                revenue_acceleration="DECELERATING",
            )
        )
        assert result.primary != FailureTaxonomy.GROWTH_STALL_MISSED


# ======================================================================
# 12. FORENSICS_FLAG_IGNORED
# ======================================================================

class TestForensicsFlagIgnored:
    def test_with_flags(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                forensics_flags=["revenue_mismatch", "cashflow_divergence"],
            )
        )
        assert result.primary == FailureTaxonomy.FORENSICS_FLAG_IGNORED
        assert result.severity == pytest.approx(0.6)

    def test_empty_flags(self) -> None:
        result = classify(
            _ctx(actual_outcome="down", state="RESOLVED_DOWN", forensics_flags=[])
        )
        assert result.primary != FailureTaxonomy.FORENSICS_FLAG_IGNORED


# ======================================================================
# 13. INSIDER_SIGNAL_FALSE
# ======================================================================

class TestInsiderSignalFalse:
    def test_cluster_buy(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                insider_signal="CLUSTER_BUY",
            )
        )
        assert result.primary == FailureTaxonomy.INSIDER_SIGNAL_FALSE
        assert result.severity == pytest.approx(0.4)

    def test_ceo_buy(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                insider_signal="CEO_BUY",
            )
        )
        assert result.primary == FailureTaxonomy.INSIDER_SIGNAL_FALSE

    def test_no_signal(self) -> None:
        result = classify(
            _ctx(actual_outcome="down", state="RESOLVED_DOWN")
        )
        assert result.primary != FailureTaxonomy.INSIDER_SIGNAL_FALSE


# ======================================================================
# 14. SHORT_INTEREST_CORRECT
# ======================================================================

class TestShortInterestCorrect:
    def test_spike_up(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                short_anomaly="SPIKE_UP",
            )
        )
        assert result.primary == FailureTaxonomy.SHORT_INTEREST_CORRECT

    def test_no_anomaly(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                short_anomaly=None,
            )
        )
        assert result.primary != FailureTaxonomy.SHORT_INTEREST_CORRECT


# ======================================================================
# 15. SIZING_OVERLEVERAGED
# ======================================================================

class TestSizingOverleveraged:
    def test_realized_exceeds_2x_expected(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                realized_pnl_pct=-15.0,
                expected_max_loss_pct=5.0,
            )
        )
        assert result.primary == FailureTaxonomy.SIZING_OVERLEVERAGED
        assert result.severity == pytest.approx(0.6)

    def test_within_expected(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                realized_pnl_pct=-4.0,
                expected_max_loss_pct=5.0,
            )
        )
        assert result.primary != FailureTaxonomy.SIZING_OVERLEVERAGED


# ======================================================================
# 16. EXECUTION_SLIPPAGE
# ======================================================================

class TestExecutionSlippage:
    def test_high_slippage(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                fill_slippage_pct=2.5,
            )
        )
        assert result.primary == FailureTaxonomy.EXECUTION_SLIPPAGE
        assert result.severity == pytest.approx(0.3)

    def test_low_slippage(self) -> None:
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_DOWN",
                fill_slippage_pct=0.3,
            )
        )
        assert result.primary != FailureTaxonomy.EXECUTION_SLIPPAGE


# ======================================================================
# 17. OPPORTUNITY_COST_EXIT_CORRECT
# ======================================================================

class TestOpportunityCostExitCorrect:
    def test_exit_opportunity_cost(self) -> None:
        result = classify(_ctx(state="EXIT_OPPORTUNITY_COST"))
        assert result.primary == FailureTaxonomy.OPPORTUNITY_COST_EXIT_CORRECT
        assert result.severity == pytest.approx(0.2)


# ======================================================================
# 18. UNCLASSIFIED
# ======================================================================

class TestUnclassified:
    def test_abort_state(self) -> None:
        result = classify(_ctx(state="ABORTED_PRE_LLM"))
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == pytest.approx(0.0)

    def test_aborted_llm(self) -> None:
        result = classify(_ctx(state="ABORTED_LLM"))
        assert result.primary == FailureTaxonomy.UNCLASSIFIED

    def test_aborted_risk(self) -> None:
        result = classify(_ctx(state="ABORTED_RISK"))
        assert result.primary == FailureTaxonomy.UNCLASSIFIED

    def test_panic_exit(self) -> None:
        result = classify(_ctx(state="PANIC_EXIT"))
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == pytest.approx(0.6)

    def test_exit_failed(self) -> None:
        result = classify(_ctx(state="EXIT_FAILED"))
        assert result.primary == FailureTaxonomy.UNCLASSIFIED

    def test_fallback_for_down_no_signals(self) -> None:
        """When outcome is down but no specific persona failure triggers,
        the fallback is UNCLASSIFIED.  Use a state that isn't
        RESOLVED_DOWN/MIXED so CATALYST_FALSE_POSITIVE doesn't fire first."""
        result = classify(
            _ctx(
                actual_outcome="down",
                state="RESOLVED_FLAT",
                moat_strength=0.3,
                revenue_acceleration="STABLE",
                forensics_flags=[],
                insider_signal=None,
                short_anomaly=None,
                correlation_with_sector=None,
                realized_pnl_pct=-2.0,
                expected_max_loss_pct=5.0,
                fill_slippage_pct=0.2,
            )
        )
        assert result.primary == FailureTaxonomy.UNCLASSIFIED
        assert result.severity == pytest.approx(0.2)


# ======================================================================
# Dead-Letter Queue
# ======================================================================

class TestDeadLetterQueue:
    def test_enqueue_and_get_pending(self) -> None:
        dlq = DeadLetterQueue(max_attempts=3, retry_delay_s=0.0)
        entry = dlq.enqueue("qdrant_write", {"collection": "theses"}, "connection refused")
        assert dlq.pending_count == 1
        assert dlq.exhausted_count == 0

        pending = dlq.get_pending()
        assert len(pending) == 1
        assert pending[0].id == entry.id
        assert pending[0].status == "PENDING"
        assert pending[0].target == "qdrant_write"

    def test_mark_completed(self) -> None:
        dlq = DeadLetterQueue(retry_delay_s=0.0)
        entry = dlq.enqueue("kuzu_execute", {"query": "MATCH ..."}, "timeout")
        dlq.mark_completed(entry.id)
        assert dlq.pending_count == 0
        assert entry.status == "COMPLETED"

    def test_retry_to_exhaustion(self) -> None:
        dlq = DeadLetterQueue(max_attempts=2, retry_delay_s=0.0)
        entry = dlq.enqueue("duckdb_write", {"table": "metrics"}, "disk full")

        # First retry
        dlq.mark_retry(entry.id)
        assert entry.attempts == 1
        assert entry.status == "RETRYING"
        assert dlq.pending_count == 1

        # Second retry — should exhaust
        dlq.mark_retry(entry.id)
        assert entry.attempts == 2
        assert entry.status == "EXHAUSTED"
        assert dlq.pending_count == 0
        assert dlq.exhausted_count == 1

        # No more pending
        assert dlq.get_pending() == []

    def test_multiple_entries(self) -> None:
        dlq = DeadLetterQueue(max_attempts=3, retry_delay_s=0.0)
        e1 = dlq.enqueue("qdrant_write", {"a": 1}, "err1")
        e2 = dlq.enqueue("kuzu_execute", {"b": 2}, "err2")
        e3 = dlq.enqueue("duckdb_write", {"c": 3}, "err3")

        assert dlq.total_count == 3
        assert dlq.pending_count == 3

        dlq.mark_completed(e1.id)
        dlq.mark_retry(e2.id)
        dlq.mark_retry(e3.id)
        dlq.mark_retry(e3.id)
        dlq.mark_retry(e3.id)  # exhausts

        assert dlq.pending_count == 1  # only e2 still retrying
        assert dlq.exhausted_count == 1  # e3


# ======================================================================
# Reconciliation Engine
# ======================================================================

class TestReconciliation:
    def test_matched_within_tolerance(self) -> None:
        result = reconcile_paper_ledger(5000.0, 4950.0)
        assert result.matched is True
        assert result.requires_action is False
        assert result.difference_usd == pytest.approx(50.0, abs=0.01)
        assert result.pmacs_position_value == 5000.0
        assert result.broker_position_value == 4950.0

    def test_exact_match(self) -> None:
        result = reconcile_paper_ledger(5000.0, 5000.0)
        assert result.matched is True
        assert result.difference_usd == pytest.approx(0.0)

    def test_mismatch_exceeds_usd_tolerance(self) -> None:
        result = reconcile_paper_ledger(5000.0, 4800.0, tolerance_usd=100.0)
        assert result.matched is False
        assert result.requires_action is True
        assert result.difference_usd == pytest.approx(200.0, abs=0.01)

    def test_mismatch_exceeds_pct_tolerance(self) -> None:
        result = reconcile_paper_ledger(1000.0, 900.0, tolerance_usd=200.0, tolerance_pct=5.0)
        assert result.matched is False
        assert result.difference_pct == pytest.approx(10.0, abs=0.01)

    def test_zero_ledger(self) -> None:
        result = reconcile_paper_ledger(0.0, 0.0)
        assert result.matched is True
        assert result.difference_pct == pytest.approx(0.0)


# ======================================================================
# Cross-DB Consistency
# ======================================================================

class TestCrossDbConsistency:
    def test_no_paths_returns_unavailable(self) -> None:
        results = check_cross_db_consistency()
        assert len(results) == 4
        for r in results:
            assert r.status == "UNAVAILABLE"

    def test_with_paths_returns_consistent(self) -> None:
        results = check_cross_db_consistency(
            sqlite_path="/tmp/test.db",
            kuzu_path="/tmp/test.kuzu",
            qdrant_url="http://localhost:6333",
            duckdb_path="/tmp/test.duckdb",
        )
        assert len(results) == 4
        for r in results:
            assert r.status == "CONSISTENT"

    def test_partial_paths(self) -> None:
        results = check_cross_db_consistency(
            sqlite_path="/tmp/test.db",
            duckdb_path="/tmp/test.duckdb",
        )
        assert len(results) == 4
        stores = {r.store: r.status for r in results}
        assert stores["sqlite"] == "CONSISTENT"
        assert stores["kuzudb"] == "UNAVAILABLE"
        assert stores["qdrant"] == "UNAVAILABLE"
        assert stores["duckdb"] == "CONSISTENT"

    def test_result_structure(self) -> None:
        results = check_cross_db_consistency(sqlite_path="/tmp/test.db")
        r = results[0]
        assert r.store == "sqlite"
        assert isinstance(r.details, str)
        assert isinstance(r.drift_count, int)
