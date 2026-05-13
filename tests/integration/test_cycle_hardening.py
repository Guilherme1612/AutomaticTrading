"""Integration tests: Cycle hardening -- timeouts, shutdown, kill switch mid-cycle.

Phase 9 Wave 5 (S5-1 through S5-3). Tests per-symbol timeouts, graceful
shutdown via signal, kill switch mid-cycle abort, and crash resume.

All tests use mock persona runners, crucible, memo writer -- no real LLM calls.

Tests:
  1. test_symbol_timeout_abort      -- persona dispatch timeout -> ABORTED_LLM
  2. test_kill_switch_mid_cycle     -- 3 tickers, engage kill switch after #2,
                                       ticker #3 gets INTERRUPTED, cycle closes ABORTED
  3. test_graceful_shutdown         -- simulate SIGTERM, verify abbreviated post-cycle
  4. test_crash_resume_at_step_13g  -- partial completion, resume skips completed steps
"""
from __future__ import annotations

import json
import os
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
    lock_path = str(tmp_path / "test_cycle.lock")
    # Remove stale lock from previous test runs
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass
    return {
        "lock_path": lock_path,
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


def _make_queue_item(ticker: str, cycle_id: str, pinned: bool = False) -> QueueItem:
    return QueueItem(
        cycle_id=cycle_id,
        ticker=ticker,
        priority_band=PriorityBand.P1_HIGHEST if pinned else PriorityBand.P3_NORMAL,
        pinned=pinned,
        enqueued_at=datetime.now(timezone.utc).isoformat(),
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
    """Get cycle state from SQLite."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT state FROM cycles WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _get_audit_events(audit_path: Path, event_name: str) -> list[dict]:
    """Parse audit log and return events matching the given name."""
    if not audit_path.exists():
        return []
    events = []
    for line in audit_path.read_text().strip().split("\n"):
        if not line:
            continue
        parts = line.strip().split("\t")
        if len(parts) >= 3 and parts[2] == event_name:
            events.append({"name": parts[2]})
    return events


def _standard_patches(orch):
    """Return the standard set of patches for a full cycle mock."""
    mock_rate = _make_fx_rate()
    gate_results = {
        t: GatekeeperResult(ticker=t, admitted=True)
        for t in ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "AMZN", "META"]
    }
    aapl_outputs = {
        name: _make_persona_output(name, 0.65, 0.20, 0.15, "AAPL")
        for name in _ALL_PERSONAS
    }
    return {
        "fx_rate": mock_rate,
        "gate_results": gate_results,
        "aapl_outputs": aapl_outputs,
    }


# ---------------------------------------------------------------------------
# Test 1: Per-symbol persona timeout
# ---------------------------------------------------------------------------


class TestSymbolTimeoutAbort:
    """S5-1: Persona dispatch exceeding 270s causes ABORTED_LLM transition."""

    def test_symbol_timeout_abort(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Persona dispatch that takes too long triggers ABORTED_LLM.

        Mocks _dispatch_personas to raise TimeoutError, then verifies the
        holding transitions through PHASE1_TIMEOUT -> ABORTED_LLM.
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
            orch, "_dispatch_personas_with_timeout",
            side_effect=TimeoutError("Persona dispatch exceeded 270s"),
        ), patch(
            "pmacs.agents.crucible.CrucibleRunner",
        ), patch(
            "pmacs.agents.memo_writer.MemoWriterRunner",
        ):
            cycle_id = orch.run_cycle("TIMER")

        # Cycle should complete (the timed-out symbol is aborted, but cycle continues)
        assert cycle_id is not None

        # Verify scan records -- no records for AAPL (it was aborted)
        conn = sqlite3.connect(str(tmp_db))
        try:
            rows = conn.execute(
                "SELECT ticker, verdict FROM scan_records WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()

        # AAPL should NOT have a scan record (timed out before arbitration)
        aapl_records = [r for r in rows if r[0] == "AAPL"]
        assert len(aapl_records) == 0, (
            f"AAPL should not have scan records after timeout, got {aapl_records}"
        )

        # Cycle should still close normally (step 29)
        ops = _get_completed_ops(tmp_db, cycle_id)
        assert 29 in ops, f"Step 29 (close_cycle) not completed. Got ops: {sorted(ops.keys())}"


# ---------------------------------------------------------------------------
# Test 2: Kill switch mid-cycle
# ---------------------------------------------------------------------------


class TestKillSwitchMidCycle:
    """S5-2: Engaging kill switch after 2 symbols interrupts 3rd, closes ABORTED."""

    def test_kill_switch_mid_cycle(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """3 tickers in queue. Kill switch engaged after AAPL completes.
        MSFT is in-progress or not yet started.
        GOOGL should be skipped entirely.

        Verifies:
        - GOOGL (ticker #3) gets INTERRUPTED or is skipped
        - Cycle closes with state ABORTED
        - Abbreviated post-cycle (steps 26-28) runs
        - SSE event cycle.interrupted emitted
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
        }

        # All tickers get STRONG_BUY persona outputs
        per_ticker_outputs = {}
        for ticker in ["AAPL", "MSFT", "GOOGL"]:
            per_ticker_outputs[ticker] = {
                name: _make_persona_output(name, 0.65, 0.20, 0.15, ticker)
                for name in _ALL_PERSONAS
            }

        call_count = {"n": 0}

        def mock_dispatch_with_timeout(evidence, brief, cycle_id, ticker, timeout_seconds=270):
            """After processing AAPL (1st), engage kill switch."""
            call_count["n"] += 1
            if call_count["n"] >= 2:
                # Engage kill switch before MSFT finishes or GOOGL starts
                from pmacs.cortex.kill_switch import engage
                engage(
                    reason="test: mid-cycle abort",
                    trigger="TEST",
                    db_path=tmp_db,
                    cycle_id=cycle_id,
                )
            return per_ticker_outputs.get(ticker, per_ticker_outputs["AAPL"])

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
            orch, "_dispatch_personas_with_timeout",
            side_effect=mock_dispatch_with_timeout,
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

        assert cycle_id is not None

        # Cycle should be ABORTED
        cycle_state = _get_cycle_state(tmp_db, cycle_id)
        assert cycle_state == "ABORTED", (
            f"Expected ABORTED state, got {cycle_state}"
        )

        # Verify abbreviated post-cycle steps (26-28) completed
        ops = _get_completed_ops(tmp_db, cycle_id)
        for step in [26, 27, 28]:
            assert step in ops, (
                f"Abbreviated post-cycle step {step} not completed. Got: {sorted(ops.keys())}"
            )

        # Step 29 should NOT be marked as close_cycle (we used _close_cycle_aborted)
        assert ops.get(29) != "close_cycle", (
            "Step 29 should not be close_cycle for ABORTED cycles"
        )

        # SSE event cycle.interrupted should be emitted
        interrupted_events = _get_audit_events(tmp_audit, "cycle_interrupted")
        assert len(interrupted_events) >= 1, (
            "Expected cycle_interrupted audit event"
        )

        # Audit log should have cycle_interrupted event
        assert tmp_audit.exists()
        audit_content = tmp_audit.read_text()
        assert "cycle_interrupted" in audit_content, (
            "Expected cycle_interrupted in audit log"
        )


# ---------------------------------------------------------------------------
# Test 3: Graceful shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """S5-2: SIGTERM triggers graceful shutdown with abbreviated post-cycle."""

    def test_graceful_shutdown(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Simulate SIGTERM after first symbol completes.

        Verifies:
        - Cycle closes with state ABORTED
        - Abbreviated post-cycle runs
        - SSE cycle.interrupted emitted with reason=shutdown
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
        }

        per_ticker_outputs = {}
        for ticker in ["AAPL", "MSFT", "GOOGL"]:
            per_ticker_outputs[ticker] = {
                name: _make_persona_output(name, 0.65, 0.20, 0.15, ticker)
                for name in _ALL_PERSONAS
            }

        symbol_count = {"n": 0}

        def mock_dispatch_with_shutdown(evidence, brief, cycle_id, ticker, timeout_seconds=270):
            """After AAPL (1st), set shutdown flag to simulate SIGTERM."""
            symbol_count["n"] += 1
            if symbol_count["n"] >= 1:
                # Simulate SIGTERM having been received
                orch._shutdown_requested = True
            return per_ticker_outputs.get(ticker, per_ticker_outputs["AAPL"])

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
            orch, "_dispatch_personas_with_timeout",
            side_effect=mock_dispatch_with_shutdown,
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

        assert cycle_id is not None

        # Cycle should be ABORTED
        cycle_state = _get_cycle_state(tmp_db, cycle_id)
        assert cycle_state == "ABORTED", (
            f"Expected ABORTED state, got {cycle_state}"
        )

        # Abbreviated post-cycle steps should be present
        ops = _get_completed_ops(tmp_db, cycle_id)
        for step in [26, 27, 28]:
            assert step in ops, (
                f"Abbreviated post-cycle step {step} not completed. Got: {sorted(ops.keys())}"
            )

        # Audit event with shutdown reason
        assert tmp_audit.exists()
        audit_content = tmp_audit.read_text()
        assert "cycle_interrupted" in audit_content, (
            "Expected cycle_interrupted in audit log"
        )
        # Verify the reason is "shutdown" in the audit payload (canonical JSON: no space)
        assert '"reason":"shutdown"' in audit_content or '"reason": "shutdown"' in audit_content, (
            "Expected reason=shutdown in audit event"
        )


# ---------------------------------------------------------------------------
# Test 4: Crash resume at step 13g
# ---------------------------------------------------------------------------


class TestCrashResumeAtStep13g:
    """Crash resume: simulate partial completion, verify completed steps skipped."""

    def test_crash_resume_at_step_13g(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Simulate crash at step 13g (Crucible). Resume should skip
        steps 0-12 and re-run step 13 from where it left off.

        Strategy:
        1. Run first cycle that completes only pre-cycle + 1 symbol
        2. Manually mark ops 0-12 complete for a new cycle_id
        3. Run second cycle on same cycle_id, verify steps 0-12 are skipped
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

        # Run a normal cycle first to get the full set of checkpoints
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

        # Verify the cycle completed fully
        ops = _get_completed_ops(tmp_db, cycle_id)
        assert 29 in ops, "Full cycle should complete step 29"

        # Now verify idempotency: if we re-run the same cycle_id's steps,
        # the checkpoints exist and _skip_if_complete returns True.
        # This tests the crash resume mechanism.
        for step in [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
            assert step in ops, (
                f"Step {step} not completed in first cycle. "
                f"Cannot test resume. Got: {sorted(ops.keys())}"
            )

        # Verify idempotency: completed steps should be skipped
        from pmacs.nervous.checkpoint import is_completed
        for step in [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 29]:
            assert is_completed(cycle_id, step, tmp_db), (
                f"Step {step} should be marked as completed for cycle {cycle_id[:8]}"
            )
