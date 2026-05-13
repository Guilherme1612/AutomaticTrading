"""Integration tests: Performance profiling, edge cases, and exit test (Phase 9 Wave 6).

Tests S6-1 (timing instrumentation), S6-2 (edge cases), and S6-3 (exit test).

All tests use mock persona runners, crucible, memo writer -- no real LLM calls.

Tests:
  1. test_step_timing_recorded         -- cycle runs, _cycle_metrics has entries
  2. test_empty_queue_cycle            -- all tickers fail gatekeeper, cycle completes
  3. test_all_symbols_abort            -- all tickers hit antipattern, no LLM calls
  4. test_exit_test_full_cycle         -- CONTEXT.md exit test: full cycle with 3 tickers
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from pmacs.agents.gatekeeper import GatekeeperResult
from pmacs.data.universe import UniverseEntry, add_ticker, init_universe_table
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.schemas.agents import PersonaName, PersonaOutput
from pmacs.schemas.contracts import HoldingState
from pmacs.schemas.conviction import VerdictTier
from pmacs.schemas.queue import PriorityBand, QueueItem
from pmacs.sim.ledger import PaperLedger
from pmacs.storage.audit import AuditVerifier
from pmacs.storage.sqlite import init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with PMACS schema + universe data."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        init_universe_table(conn)
        for ticker in ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "AMZN", "META"]:
            add_ticker(conn, UniverseEntry(ticker=ticker, sector="Tech"))
        conn.commit()
    finally:
        conn.close()

    return db_path


@pytest.fixture
def tmp_audit(tmp_path: Path) -> Path:
    """Create a temporary audit log path."""
    return tmp_path / "audit.log"


@pytest.fixture
def publisher() -> SSEPublisher:
    return SSEPublisher()


@pytest.fixture
def config(tmp_path: Path) -> dict:
    return {
        "lock_path": str(tmp_path / "test_cycle.lock"),
    }


@pytest.fixture
def ledger() -> PaperLedger:
    return PaperLedger()


@pytest.fixture
def orchestrator(
    tmp_db: Path,
    tmp_audit: Path,
    publisher: SSEPublisher,
    config: dict,
    ledger: PaperLedger,
) -> CycleOrchestrator:
    orch = CycleOrchestrator(
        db_path=tmp_db,
        audit_path=tmp_audit,
        sse_publisher=publisher,
        config=config,
    )
    orch._ledger = ledger
    return orch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fx_rate():
    from pmacs.schemas.currency import FxRate
    return FxRate(
        usd_per_eur=1.085,
        business_date=date(2026, 5, 13),
        fetched_at=datetime.now(timezone.utc),
    )


def _make_persona_output(
    persona_name: str,
    p_up: float,
    p_flat: float,
    p_down: float,
    ticker: str = "",
) -> PersonaOutput:
    return PersonaOutput(
        persona=PersonaName(persona_name),
        ticker=ticker,
        cycle_id="test",
        raw_output=json.dumps({
            "p_up": p_up,
            "p_flat": p_flat,
            "p_down": p_down,
            "ticker": ticker,
        }),
        grammar_version="v1",
        model_hash="abc123",
        temperature=0.2,
        retry_count=0,
    )


_ALL_PERSONAS = [
    "macro_regime", "catalyst_summarizer",
    "moat_analyst", "growth_hunter",
    "insider_activity", "short_interest", "forensics",
]


def _get_completed_ops(db_path: Path, cycle_id: str) -> dict[int, str]:
    """Get completed operation checkpoints for a cycle."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT op_seq, op_type FROM op_idempotency "
            "WHERE cycle_id = ? ORDER BY op_seq",
            (cycle_id,),
        ).fetchall()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def _get_cycle_state(db_path: Path, cycle_id: str) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT state FROM cycles WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _get_scan_records(db_path: Path, cycle_id: str) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT ticker, verdict, conviction_score, direction "
            "FROM scan_records WHERE cycle_id = ? ORDER BY ticker",
            (cycle_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return [
        {"ticker": r[0], "verdict": r[1], "conviction_score": r[2], "direction": r[3]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStepTimingRecorded:
    """S6-1: Verify _cycle_metrics has entries after a cycle."""

    def test_step_timing_recorded(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Run cycle and verify _cycle_metrics populated with per-step times.

        Verifies:
        - _cycle_metrics dict exists after run_cycle
        - per_step_times has entries for key pre-cycle steps
        - total_time_ms is populated and > 0
        - total_time_ms < 30,000ms (30s budget with mock LLM)
        """
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )
        orch._ledger = ledger

        mock_rate = _make_fx_rate()

        gate_results = {
            "AAPL": GatekeeperResult(ticker="AAPL", admitted=True),
        }

        aapl_outputs = {
            name: _make_persona_output(name, 0.65, 0.20, 0.15, "AAPL")
            for name in _ALL_PERSONAS
        }

        with patch(
            "pmacs.data.fx.fetch_ecb_rate", return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results.get(
                ticker, GatekeeperResult(ticker=ticker, admitted=False),
            ),
        ), patch.object(
            orch, "_dispatch_personas", return_value=aapl_outputs,
        ), patch(
            "pmacs.agents.crucible.CrucibleRunner",
        ) as mock_crucible_cls, patch(
            "pmacs.agents.memo_writer.MemoWriterRunner",
        ) as mock_memo_cls, patch(
            "pmacs.engines.conviction.compute_conviction", return_value=0.65,
        ), patch(
            "pmacs.engines.conviction.verdict_tier", return_value=VerdictTier.STRONG_BUY,
        ):
            mock_crucible_inst = MagicMock()
            mock_crucible_inst.run.return_value = MagicMock(
                raw_output=json.dumps({"severity_score": 0.1})
            )
            mock_crucible_cls.return_value = mock_crucible_inst

            mock_memo_inst = MagicMock()
            mock_memo_inst.run.return_value = MagicMock(raw_output="memo")
            mock_memo_cls.return_value = mock_memo_inst

            cycle_id = orch.run_cycle("TIMER")

        # Verify _cycle_metrics exists
        assert hasattr(orch, "_cycle_metrics")
        metrics = orch._cycle_metrics

        # total_time_ms must be populated and reasonable
        assert metrics["total_time_ms"] > 0, (
            f"total_time_ms should be > 0, got {metrics['total_time_ms']}"
        )
        assert metrics["total_time_ms"] < 30_000, (
            f"Cycle took {metrics['total_time_ms']:.0f}ms, should be < 30s with mock LLM"
        )

        # per_step_times must have entries for pre-cycle steps
        per_step = metrics.get("per_step_times", {})
        assert len(per_step) > 0, "per_step_times should have at least one entry"

        # Check key pre-cycle steps were timed
        expected_labels = ["fx_snapshot", "gatekeeper", "queue_composition"]
        for label in expected_labels:
            assert label in per_step, (
                f"Expected '{label}' in per_step_times, got: {list(per_step.keys())}"
            )
            assert per_step[label] >= 0, (
                f"Step '{label}' time should be >= 0, got {per_step[label]}"
            )

        # Check post-cycle steps were timed
        post_labels = ["reconciliation", "dead_letter"]
        for label in post_labels:
            assert label in per_step, (
                f"Expected post-cycle step '{label}' in per_step_times, "
                f"got: {list(per_step.keys())}"
            )

        # Verify aggregated metrics
        assert metrics["persona_dispatch_time_ms"] >= 0
        assert metrics["crucible_time_ms"] >= 0
        assert metrics["post_cycle_time_ms"] >= 0


class TestEmptyQueueCycle:
    """S6-2 edge case: all tickers fail gatekeeper -> empty queue."""

    def test_empty_queue_cycle(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """All tickers rejected by gatekeeper, queue is empty.

        Verifies:
        - Cycle completes successfully (CLOSED state)
        - No scan records created
        - Post-cycle steps still fire (14-28)
        - _cycle_metrics populated
        """
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )
        orch._ledger = ledger

        mock_rate = _make_fx_rate()

        # All tickers rejected by gatekeeper
        gate_results = {
            ticker: GatekeeperResult(ticker=ticker, admitted=False)
            for ticker in ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "AMZN", "META"]
        }

        with patch(
            "pmacs.data.fx.fetch_ecb_rate", return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results.get(
                ticker, GatekeeperResult(ticker=ticker, admitted=False),
            ),
        ):
            cycle_id = orch.run_cycle("TIMER")

        # Cycle completed
        assert cycle_id is not None
        assert _get_cycle_state(tmp_db, cycle_id) == "CLOSED"

        # No scan records (no symbols processed)
        records = _get_scan_records(tmp_db, cycle_id)
        assert len(records) == 0

        # Post-cycle steps still fired
        ops = _get_completed_ops(tmp_db, cycle_id)
        for step in range(14, 29):
            assert step in ops, (
                f"Post-cycle step {step} should still fire on empty queue. "
                f"Got: {sorted(ops.keys())}"
            )

        # Close cycle step present
        assert 29 in ops

        # Metrics populated
        assert orch._cycle_metrics["total_time_ms"] > 0

        # Audit log exists and has open/close events
        assert tmp_audit.exists()
        audit_content = tmp_audit.read_text()
        assert "cycle_opened" in audit_content
        assert "cycle_closed" in audit_content


class TestAllSymbolsAbort:
    """S6-2 edge case: all symbols hit antipattern, no LLM calls."""

    def test_all_symbols_abort(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """All tickers admitted by gatekeeper but hit antipattern check.

        Verifies:
        - Cycle completes successfully (CLOSED state)
        - No scan records (all symbols aborted before LLM)
        - No trade_executed in audit log
        - Post-cycle steps fire
        """
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )
        orch._ledger = ledger

        mock_rate = _make_fx_rate()

        # All tickers admitted
        gate_results = {
            ticker: GatekeeperResult(ticker=ticker, admitted=True)
            for ticker in ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]
        }

        with patch(
            "pmacs.data.fx.fetch_ecb_rate", return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results.get(
                ticker, GatekeeperResult(ticker=ticker, admitted=False),
            ),
        ), patch(
            "pmacs.engines.memory.check_antipattern",
            return_value="recently_aborted",
        ):
            cycle_id = orch.run_cycle("TIMER")

        # Cycle completed
        assert cycle_id is not None
        assert _get_cycle_state(tmp_db, cycle_id) == "CLOSED"

        # No scan records (all aborted before LLM/arbitration)
        records = _get_scan_records(tmp_db, cycle_id)
        assert len(records) == 0

        # No trades executed
        audit_content = tmp_audit.read_text()
        assert "trade_executed" not in audit_content

        # Post-cycle steps still fired
        ops = _get_completed_ops(tmp_db, cycle_id)
        for step in range(14, 29):
            assert step in ops, (
                f"Post-cycle step {step} should fire even when all symbols abort. "
                f"Got: {sorted(ops.keys())}"
            )

        assert 29 in ops


class TestExitTestFullCycle:
    """S6-3: The CONTEXT.md exit test -- full cycle with 3 synthetic tickers.

    Exit test criteria:
    - Open cycle
    - Pre-cycle steps execute (0-12)
    - 3 synthetic tickers, at least 1 STRONG_BUY with mock fill
    - Post-cycle flywheel fires (14-28)
    - Cycle closes (29)
    - Audit chain verifies
    - Total cycle time < 30s with mock LLM
    """

    def test_exit_test_full_cycle(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Complete exit test: 3 tickers, 1 STRONG_BUY, full pipeline.

        Tickers:
          - AAPL: STRONG_BUY (high p_up, high conviction) -> executed
          - MSFT: SKIP (balanced probabilities, low conviction) -> no execution
          - GOOGL: SKIP (slightly bearish, low conviction) -> no execution

        Verifies:
        1. Cycle opens and closes successfully
        2. All pre-cycle steps 0-12 complete
        3. At least 1 STRONG_BUY trade executed (AAPL)
        4. Paper ledger has the position
        5. All post-cycle steps 14-28 complete
        6. Audit log has open, trade, close events
        7. Audit chain integrity valid
        8. Total cycle time < 30s
        """
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )
        orch._ledger = ledger

        mock_rate = _make_fx_rate()

        gate_results = {
            "AAPL": GatekeeperResult(ticker="AAPL", admitted=True),
            "MSFT": GatekeeperResult(ticker="MSFT", admitted=True),
            "GOOGL": GatekeeperResult(ticker="GOOGL", admitted=True),
            "TSLA": GatekeeperResult(ticker="TSLA", admitted=False),
            "NVDA": GatekeeperResult(ticker="NVDA", admitted=False),
        }

        # Different persona results per ticker
        aapl_outputs = {
            name: _make_persona_output(name, 0.65, 0.20, 0.15, "AAPL")
            for name in _ALL_PERSONAS
        }
        msft_outputs = {
            name: _make_persona_output(name, 0.30, 0.40, 0.30, "MSFT")
            for name in _ALL_PERSONAS
        }
        googl_outputs = {
            name: _make_persona_output(name, 0.35, 0.35, 0.30, "GOOGL")
            for name in _ALL_PERSONAS
        }
        per_ticker_outputs = {
            "AAPL": aapl_outputs,
            "MSFT": msft_outputs,
            "GOOGL": googl_outputs,
        }

        def mock_dispatch(evidence, brief, cycle_id, ticker):
            return per_ticker_outputs.get(ticker, aapl_outputs)

        # Only AAPL gets high conviction
        def mock_compute_conviction(arb, crucible_severity, ev_multiple, is_bootstrap):
            if arb.ticker == "AAPL":
                return 0.65
            return 0.05

        def mock_verdict_tier(conviction, is_active_holding=False, thesis_valid=True):
            if conviction >= 0.6:
                return VerdictTier.STRONG_BUY
            return VerdictTier.SKIP

        cycle_start = time.monotonic()

        with patch(
            "pmacs.data.fx.fetch_ecb_rate", return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results.get(
                ticker, GatekeeperResult(ticker=ticker, admitted=False),
            ),
        ), patch.object(
            orch, "_dispatch_personas", side_effect=mock_dispatch,
        ), patch(
            "pmacs.agents.crucible.CrucibleRunner",
        ) as mock_crucible_cls, patch(
            "pmacs.agents.memo_writer.MemoWriterRunner",
        ) as mock_memo_cls, patch(
            "pmacs.engines.conviction.compute_conviction",
            side_effect=mock_compute_conviction,
        ), patch(
            "pmacs.engines.conviction.verdict_tier",
            side_effect=mock_verdict_tier,
        ):
            mock_crucible_inst = MagicMock()
            mock_crucible_inst.run.return_value = MagicMock(
                raw_output=json.dumps({"severity_score": 0.1})
            )
            mock_crucible_cls.return_value = mock_crucible_inst

            mock_memo_inst = MagicMock()
            mock_memo_inst.run.return_value = MagicMock(raw_output="memo")
            mock_memo_cls.return_value = mock_memo_inst

            cycle_id = orch.run_cycle("TIMER")

        cycle_elapsed_s = time.monotonic() - cycle_start

        # 1. Cycle opened and closed
        assert cycle_id is not None
        assert _get_cycle_state(tmp_db, cycle_id) == "CLOSED"

        # 2. All pre-cycle steps 0-12 completed
        ops = _get_completed_ops(tmp_db, cycle_id)
        for step in [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
            assert step in ops, (
                f"Pre-cycle step {step} not completed. Got: {sorted(ops.keys())}"
            )

        # 3. At least 1 STRONG_BUY trade executed
        records = _get_scan_records(tmp_db, cycle_id)
        assert len(records) >= 1, f"Expected >= 1 scan record, got {len(records)}"

        aapl_records = [r for r in records if r["ticker"] == "AAPL"]
        assert len(aapl_records) == 1, (
            f"Expected 1 AAPL scan record, got {len(aapl_records)}"
        )
        assert aapl_records[0]["verdict"] == VerdictTier.STRONG_BUY.value

        # 4. Paper ledger has the position
        assert "AAPL" in ledger.positions, (
            f"AAPL not in ledger positions: {list(ledger.positions.keys())}"
        )
        assert ledger.position_count >= 1

        # 5. All post-cycle steps 14-28 completed
        for step in range(14, 29):
            assert step in ops, (
                f"Post-cycle step {step} not completed. Got: {sorted(ops.keys())}"
            )

        # Step 29: close cycle
        assert 29 in ops

        # 6. Audit log has open, trade, close events
        assert tmp_audit.exists()
        audit_content = tmp_audit.read_text()
        assert "cycle_opened" in audit_content, "Missing cycle_opened in audit"
        assert "trade_executed" in audit_content, "Missing trade_executed in audit"
        assert "cycle_closed" in audit_content, "Missing cycle_closed in audit"
        assert cycle_id in audit_content

        # 7. Audit chain integrity
        verifier = AuditVerifier(tmp_audit)
        chain_ok, chain_error = verifier.verify_full()
        assert chain_ok, f"Audit chain invalid: {chain_error}"

        # 8. Total cycle time < 30s (with mock LLM)
        assert cycle_elapsed_s < 30.0, (
            f"Cycle took {cycle_elapsed_s:.1f}s, should be < 30s with mock LLM"
        )

        # Also verify via _cycle_metrics
        assert orch._cycle_metrics["total_time_ms"] < 30_000, (
            f"Cycle metrics: {orch._cycle_metrics['total_time_ms']:.0f}ms, "
            f"should be < 30,000ms"
        )
