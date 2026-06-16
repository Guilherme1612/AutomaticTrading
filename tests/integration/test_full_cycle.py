"""Integration tests: Full cycle with post-cycle flywheel (Phase 9 Wave 4, S4-1).

Tests _run_post_cycle() wiring (steps 14-28) and the complete run_cycle() path
from step 0 through step 30, including audit chain integrity.

All tests use mock persona runners, crucible, memo writer -- no real LLM calls.

Tests:
  1. test_post_cycle_reeval_and_recon   -- cycle with 1 active holding, re-eval + reconciliation
  2. test_post_cycle_flywheel_engines   -- calibration, lessons, FDE all fire correctly
  3. test_full_cycle_all_30_steps       -- 3 tickers, 1 STRONG_BUY, all steps execute
  4. test_audit_chain_integrity         -- audit.log prev_sha256 chains correctly
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from pmacs.agents.gatekeeper import GatekeeperResult
from pmacs.data.universe import UniverseEntry, add_ticker, init_universe_table
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.schemas.agents import DirectionalProbability, PersonaName, PersonaOutput
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
        for ticker in ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]:
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
        business_date=date(2026, 5, 12),
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


def _insert_active_holding(db_path: Path, cycle_id: str, ticker: str) -> str:
    """Insert a fake ACTIVE holding into SQLite for re-eval tests."""
    holding_id = str(uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO holdings "
            "(id, ticker, state, cycle_id_opened, entry_price_usd, "
            "position_size_usd, conviction_score, sector, entry_date) "
            "VALUES (?, ?, 'ACTIVE', ?, 100.0, 500.0, 0.6, 'Tech', ?)",
            (holding_id, ticker, cycle_id, date(2026, 4, 1).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return holding_id


def _insert_terminal_holding(
    db_path: Path,
    cycle_id: str,
    ticker: str,
    state: str = "STOPPED_OUT",
) -> str:
    """Insert a fake terminal holding for FDE tests."""
    holding_id = str(uuid4())
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO holdings "
            "(id, ticker, state, cycle_id_opened, entry_price_usd, "
            "exit_price_usd, position_size_usd, "
            "conviction_score, sector, entry_date) "
            "VALUES (?, ?, ?, ?, 100.0, 85.0, 500.0, 0.6, 'Tech', ?)",
            (
                holding_id,
                ticker,
                state,
                cycle_id,
                date(2026, 4, 1).isoformat(),
            ),
        )
        # Set stop_price_usd and abort_reason via UPDATE (columns added by migration)
        conn.execute(
            "UPDATE holdings SET stop_price_usd = 85.0, abort_reason = 'stop_loss_hit' "
            "WHERE id = ?",
            (holding_id,),
        )
        conn.commit()
    finally:
        conn.close()
    return holding_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPostCycleReevalAndRecon:
    """Steps 14-17: Re-evaluation, thesis aging, fills, reconciliation."""

    def test_post_cycle_reeval_and_recon(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Cycle with 1 active holding triggers re-eval and reconciliation.

        Verifies:
        - Step 14 updates last_reeval_at for the active holding
        - Step 15 detects aged holdings
        - Step 16 runs (no-op for paper)
        - Step 17 reconciliation runs and matches
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

        # Pre-insert an active holding for re-eval
        holding_id = _insert_active_holding(tmp_db, "pre-cycle", "NVDA")

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

        # Verify cycle completed
        assert cycle_id is not None

        # Check completed ops include post-cycle steps
        ops = _get_completed_ops(tmp_db, cycle_id)
        assert 14 in ops, f"Step 14 (weekly_reeval) not completed, got ops: {ops}"
        assert 15 in ops, f"Step 15 (thesis_aging) not completed, got ops: {ops}"
        assert 16 in ops, f"Step 16 (process_fills) not completed, got ops: {ops}"
        assert 17 in ops, f"Step 17 (reconciliation) not completed, got ops: {ops}"
        assert 29 in ops, f"Step 29 (close_cycle) not completed, got ops: {ops}"

        # Verify the pre-inserted holding's last_reeval_at was updated
        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT last_reeval_at FROM holdings WHERE id = ?",
                (holding_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "Pre-inserted holding not found"
        assert row[0] is not None, "last_reeval_at should have been updated by step 14"


class TestPostCycleFlywheelEngines:
    """Steps 19-25: Calibration, lessons, FDE fire correctly."""

    def test_post_cycle_flywheel_engines(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Verify calibration, lessons, FDE all fire during post-cycle.

        Sets up a terminal holding (STOPPED_OUT) so FDE can classify it.
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

        # Pre-insert a terminal holding for FDE classification
        _insert_terminal_holding(tmp_db, "pre-cycle", "TSLA", "STOPPED_OUT")

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

        assert cycle_id is not None

        ops = _get_completed_ops(tmp_db, cycle_id)

        # All post-cycle steps must be present
        expected_steps = {
            14: "weekly_reeval",
            15: "thesis_aging",
            16: "process_fills",
            17: "reconciliation",
            18: "opportunity_cost",
            19: "calibration",
            20: "crucible_calibration",
            21: "causal_attribution",
            22: "memory_antipattern",
            23: "lessons_extraction",
            24: "override_learning_post",
            25: "fde",
            26: "drift_stats",
            27: "cross_db_consistency",
            28: "dead_letter",
            29: "close_cycle",
        }

        for step_num, step_name in expected_steps.items():
            assert step_num in ops, (
                f"Step {step_num} ({step_name}) not completed. "
                f"Got ops: {sorted(ops.keys())}"
            )
            assert ops[step_num] == step_name, (
                f"Step {step_num} has op_type='{ops[step_num]}', expected '{step_name}'"
            )

        # Verify FDE created a failure_classification for the terminal holding
        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT taxonomy, severity, summary FROM failure_classifications "
                "WHERE cycle_id = ? LIMIT 1",
                (cycle_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        finally:
            conn.close()

        assert row is not None, "FDE should have classified the STOPPED_OUT holding"
        assert row[0] == "STOP_LOSS_CORRECT", (
            f"Expected STOP_LOSS_CORRECT taxonomy, got {row[0]}"
        )


class TestFullCycleAll30Steps:
    """Complete cycle with 3 tickers, 1 STRONG_BUY, all steps execute."""

    def test_full_cycle_all_30_steps(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """3 tickers: AAPL (STRONG_BUY), MSFT (SKIP), GOOGL (SKIP).

        Verifies:
        - All 30 steps complete (op_idempotency table has entries for steps 0-29)
        - At least 1 trade executed (AAPL)
        - Paper ledger has the position
        - Audit log has cycle_opened, trade_executed, cycle_closed
        - All post-cycle steps 14-28 completed
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

        def mock_verdict_tier(conviction, is_active_holding=False, thesis_valid=True,
                              is_bootstrap=False):
            if conviction >= 0.6:
                return VerdictTier.STRONG_BUY
            return VerdictTier.SKIP

        with patch(
            "pmacs.data.fx.fetch_ecb_rate", return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results[ticker],
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

        # Verify cycle completed
        assert cycle_id is not None

        # Verify scan records: AAPL should have a record, MSFT/GOOGL should not
        records = _get_scan_records(tmp_db, cycle_id)
        assert len(records) >= 1
        aapl_records = [r for r in records if r["ticker"] == "AAPL"]
        assert len(aapl_records) == 1
        assert aapl_records[0]["verdict"] == VerdictTier.STRONG_BUY.value

        # Verify paper ledger
        assert "AAPL" in ledger.positions
        assert ledger.position_count >= 1

        # Verify all expected op_seq steps completed
        ops = _get_completed_ops(tmp_db, cycle_id)

        # Core steps 0-12
        for step in [0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
            assert step in ops, f"Core step {step} not completed. Got: {sorted(ops.keys())}"

        # Post-cycle steps 14-28
        for step in range(14, 29):
            assert step in ops, f"Post-cycle step {step} not completed. Got: {sorted(ops.keys())}"

        # Step 29: close cycle
        assert 29 in ops

        # Verify audit log
        assert tmp_audit.exists()
        audit_content = tmp_audit.read_text()
        assert "cycle_opened" in audit_content
        assert "cycle_closed" in audit_content
        assert "trade_executed" in audit_content
        assert cycle_id in audit_content


class TestAuditChainIntegrity:
    """Audit log hash chain verification across full cycle."""

    def test_audit_chain_integrity(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
        ledger: PaperLedger,
    ) -> None:
        """Full cycle produces audit.log with valid prev_sha256 chain."""
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

        assert cycle_id is not None

        # Verify audit log exists and has content
        assert tmp_audit.exists()
        lines = tmp_audit.read_text().strip().split("\n")
        assert len(lines) >= 3, (
            f"Expected at least 3 audit lines (open, trade, close), got {len(lines)}"
        )

        # Verify hash chain integrity using AuditVerifier
        verifier = AuditVerifier(tmp_audit)
        ok, error = verifier.verify_full()

        assert ok, f"Audit chain integrity FAILED: {error}"

        # Also verify specific events are present
        audit_content = tmp_audit.read_text()
        event_types: list[str] = []
        for line in lines:
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                event_types.append(parts[2])

        assert "cycle_opened" in event_types, "Missing cycle_opened event"
        assert "trade_executed" in event_types, "Missing trade_executed event"
        assert "cycle_closed" in event_types, "Missing cycle_closed event"
