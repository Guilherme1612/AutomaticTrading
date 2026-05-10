"""Integration tests for cross-DB consistency, dead-letter queue, reconciliation,
and FDE stop classification.

Tests the interaction between storage adapters, consistency checks, dead-letter
retry logic, reconciliation engine, and the FDE stop-type differentiation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pmacs.storage.consistency import check_cross_db_consistency, ConsistencyResult
from pmacs.logsys.dead_letter import DeadLetterQueue, DeadLetterEntry
from pmacs.engines.reconciliation import reconcile_paper_ledger, ReconciliationResult
from pmacs.engines.failure_diagnostic import HoldingContext, classify, ClassifyResult
from pmacs.schemas.failure import FailureTaxonomy


# ======================================================================
# Cross-DB consistency
# ======================================================================

class TestCrossDbConsistencyIntegration:
    def test_returns_results_with_paths(self) -> None:
        """check_cross_db_consistency() returns results when called with paths."""
        results = check_cross_db_consistency(
            sqlite_path="/tmp/test.db",
            kuzu_path="/tmp/test.kuzu",
            qdrant_url="http://localhost:6333",
            duckdb_path="/tmp/test.duckdb",
        )
        assert len(results) == 4
        assert all(isinstance(r, ConsistencyResult) for r in results)

    def test_stub_adapters_return_consistent(self) -> None:
        """With path strings only (no real adapters), checks return CONSISTENT."""
        results = check_cross_db_consistency(
            sqlite_path="/tmp/test.db",
            kuzu_path="/tmp/test.kuzu",
            qdrant_url="http://localhost:6333",
            duckdb_path="/tmp/test.duckdb",
        )
        for r in results:
            assert r.status == "CONSISTENT"

    def test_no_paths_returns_unavailable(self) -> None:
        """Without any paths or adapters, returns UNAVAILABLE for all stores."""
        results = check_cross_db_consistency()
        for r in results:
            assert r.status == "UNAVAILABLE"

    def test_consistency_result_structure(self) -> None:
        """Each ConsistencyResult has store, status, details, drift_count."""
        results = check_cross_db_consistency(sqlite_path="/tmp/test.db")
        stores_seen = {r.store for r in results}
        assert "sqlite" in stores_seen
        for r in results:
            assert isinstance(r.store, str)
            assert isinstance(r.status, str)
            assert isinstance(r.details, str)
            assert isinstance(r.drift_count, int)


# ======================================================================
# Dead-letter queue exponential backoff
# ======================================================================

class TestDeadLetterBackoffSchedule:
    def test_default_backoff_schedule(self) -> None:
        """Default schedule is [1, 5, 30, 300, 3600, 86400] (6 steps)."""
        dlq = DeadLetterQueue()
        assert dlq.backoff_schedule == [1, 5, 30, 300, 3600, 86400]

    def test_custom_backoff_schedule(self) -> None:
        """Custom schedule overrides default."""
        custom = [2, 10, 60]
        dlq = DeadLetterQueue(backoff_schedule=custom)
        assert dlq.backoff_schedule == [2, 10, 60]

    def test_max_attempts_defaults_to_6(self) -> None:
        """Default max_attempts matches the 6-step schedule."""
        dlq = DeadLetterQueue()
        assert dlq.max_attempts == 6

    def test_exhaustion_after_6_attempts(self) -> None:
        """Entry becomes EXHAUSTED after exactly max_attempts retries."""
        dlq = DeadLetterQueue(max_attempts=6, retry_delay_s=0.0)
        entry = dlq.enqueue("qdrant_write", {"col": "theses"}, "timeout")

        for i in range(6):
            assert entry.status != "EXHAUSTED", f"Exhausted prematurely at attempt {i}"
            dlq.mark_retry(entry.id)

        assert entry.status == "EXHAUSTED"
        assert entry.attempts == 6
        assert dlq.exhausted_count == 1
        assert dlq.pending_count == 0

    def test_retry_delay_s_backward_compat(self) -> None:
        """retry_delay_s creates a fixed-delay schedule of max_attempts length."""
        dlq = DeadLetterQueue(max_attempts=3, retry_delay_s=5.0)
        assert dlq.backoff_schedule == [5.0, 5.0, 5.0]


# ======================================================================
# Reconciliation engine
# ======================================================================

class TestReconciliationIntegration:
    def test_mismatch_exceeds_100_usd_tolerance(self) -> None:
        """$200 difference with $100 tolerance -> requires_action=True."""
        result = reconcile_paper_ledger(
            ledger_total=5000.0,
            broker_total=4800.0,
            tolerance_usd=100.0,
            tolerance_pct=5.0,
        )
        assert result.matched is False
        assert result.requires_action is True
        assert result.difference_usd == pytest.approx(200.0, abs=0.01)

    def test_within_tolerance_passes(self) -> None:
        """$50 difference within $100 tolerance -> matched."""
        result = reconcile_paper_ledger(
            ledger_total=5000.0,
            broker_total=4950.0,
            tolerance_usd=100.0,
            tolerance_pct=5.0,
        )
        assert result.matched is True
        assert result.requires_action is False
        assert result.difference_usd == pytest.approx(50.0, abs=0.01)

    def test_exact_match(self) -> None:
        """Exact match -> zero difference."""
        result = reconcile_paper_ledger(
            ledger_total=5000.0,
            broker_total=5000.0,
        )
        assert result.matched is True
        assert result.difference_usd == pytest.approx(0.0)

    def test_pct_tolerance_triggers(self) -> None:
        """Small USD diff but large pct diff -> not matched."""
        result = reconcile_paper_ledger(
            ledger_total=200.0,
            broker_total=170.0,
            tolerance_usd=100.0,  # $30 diff < $100 tolerance
            tolerance_pct=5.0,    # but 15% diff > 5% tolerance
        )
        assert result.matched is False
        assert result.difference_pct == pytest.approx(15.0, abs=0.01)


# ======================================================================
# STOP_HUNTED vs STOP_LOSS_CORRECT differentiation (FDE integration)
# ======================================================================

class TestStopTypeDifferentiation:
    def test_stop_hunted_when_price_recovers(self) -> None:
        """STOPPED_OUT with 48h recovery -> STOP_HUNTED."""
        result = classify(
            HoldingContext(
                state="STOPPED_OUT",
                ticker="AAPL",
                entry_price=100.0,
                exit_price=95.0,
                price_48h_after_exit=103.0,  # > 102% of entry
            ),
            holding_id="h-001",
            cycle_id="c-001",
        )
        assert result.primary == FailureTaxonomy.STOP_HUNTED
        assert result.severity == pytest.approx(0.7)
        assert "95" in result.summary
        assert "103" in result.summary

    def test_stop_loss_correct_when_price_stays_low(self) -> None:
        """STOPPED_OUT with no recovery -> STOP_LOSS_CORRECT."""
        result = classify(
            HoldingContext(
                state="STOPPED_OUT",
                ticker="TSLA",
                entry_price=200.0,
                exit_price=180.0,
                stop_loss_price=181.0,
                price_30d_after_exit=150.0,  # stayed below stop
            ),
            holding_id="h-002",
            cycle_id="c-002",
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT
        assert result.severity == pytest.approx(0.2)

    def test_trailing_stop_hunted(self) -> None:
        """EXIT_TRAILING_STOP with recovery -> STOP_HUNTED."""
        result = classify(
            HoldingContext(
                state="EXIT_TRAILING_STOP",
                ticker="NVDA",
                entry_price=500.0,
                exit_price=490.0,
                price_48h_after_exit=520.0,
            ),
            holding_id="h-003",
            cycle_id="c-003",
        )
        assert result.primary == FailureTaxonomy.STOP_HUNTED

    def test_exogenous_macro_shock_takes_priority(self) -> None:
        """Sector crash >10% in 5d -> EXOGENOUS_MACRO_SHOCK even with 48h recovery."""
        result = classify(
            HoldingContext(
                state="STOPPED_OUT",
                ticker="XOM",
                entry_price=100.0,
                exit_price=85.0,
                sector_drop_5d_pct=-15.0,
                price_48h_after_exit=103.0,  # recovered, but macro shock takes priority
            ),
            holding_id="h-004",
            cycle_id="c-004",
        )
        assert result.primary == FailureTaxonomy.EXOGENOUS_MACRO_SHOCK
        assert result.severity == pytest.approx(0.4)

    def test_default_stop_loss_when_unknown_recovery(self) -> None:
        """STOPPED_OUT with no recovery data -> STOP_LOSS_CORRECT (default)."""
        result = classify(
            HoldingContext(
                state="STOPPED_OUT",
                ticker="MSFT",
                entry_price=300.0,
                exit_price=285.0,
                # no 48h or 30d data
            ),
            holding_id="h-005",
            cycle_id="c-005",
        )
        assert result.primary == FailureTaxonomy.STOP_LOSS_CORRECT
        assert result.severity == pytest.approx(0.3)  # default severity
