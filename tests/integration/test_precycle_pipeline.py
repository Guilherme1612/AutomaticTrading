"""Integration tests: Pre-cycle pipeline (Phase 9 Wave 2).

Tests steps 2-3, 6-12 of the cycle orchestrator:
  Step 2  -- FX snapshot
  Step 3  -- Corporate actions
  Step 6  -- Macro regime
  Step 7  -- Catalyst resolution
  Step 8  -- Universe sync
  Step 9  -- Gatekeeper
  Step 10 -- Lessons flagger
  Step 11 -- Override learning
  Step 12 -- Queue composition

All tests use mock data -- no real LLM calls, no real HTTP.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.agents.gatekeeper import GatekeeperResult
from pmacs.data.universe import UniverseEntry, add_ticker, init_universe_table
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.schemas.queue import PriorityBand
from pmacs.storage.sqlite import init_db


# -- Fixtures --


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with PMACS schema + universe data."""
    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        init_universe_table(conn)
        # Add 5 tickers to the universe
        for ticker in ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]:
            add_ticker(conn, UniverseEntry(ticker=ticker, sector="Tech"))
        # Add a halted ticker (should not appear in universe sync)
        add_ticker(conn, UniverseEntry(ticker="HALT", sector="Tech", halted=True))
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
    """Provide an SSE publisher."""
    return SSEPublisher()


@pytest.fixture
def config(tmp_path: Path) -> dict:
    """Provide a test config that uses a temp lock path."""
    return {
        "lock_path": str(tmp_path / "test_cycle.lock"),
    }


@dataclass
class _MockRisk:
    max_concurrent_positions: int = 5


@dataclass
class _MockConfig:
    risk: _MockRisk  # type: ignore[assignment]


MOCK_CONFIG = _MockRisk()  # Used as risk directly


@pytest.fixture
def orchestrator(
    tmp_db: Path,
    tmp_audit: Path,
    publisher: SSEPublisher,
    config: dict,
) -> CycleOrchestrator:
    """Provide a CycleOrchestrator wired for testing."""
    return CycleOrchestrator(
        db_path=tmp_db,
        audit_path=tmp_audit,
        sse_publisher=publisher,
        config=config,
    )


def _make_mock_fx_rate():
    """Create a mock FxRate for testing."""
    from pmacs.schemas.currency import FxRate
    return FxRate(
        usd_per_eur=1.085,
        business_date=date(2026, 5, 12),
        fetched_at=datetime.now(timezone.utc),
    )


def _make_mock_persona_output():
    """Create a mock PersonaOutput for macro regime testing."""
    from pmacs.schemas.agents import PersonaOutput, PersonaName
    return PersonaOutput(
        persona=PersonaName.MACRO_REGIME,
        ticker="",
        cycle_id="test",
        raw_output='{"regime": "EXPANSION"}',
        grammar_version="macro_regime",
        model_hash="abc123",
        temperature=0.2,
        retry_count=0,
    )


# -- Helper --


def _get_op_idempotency(db_path: Path, cycle_id: str) -> list[dict]:
    """Fetch all idempotency rows for a cycle."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT cycle_id, op_seq, op_type, completed_at "
            "FROM op_idempotency WHERE cycle_id = ? ORDER BY op_seq",
            (cycle_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"cycle_id": r[0], "op_seq": r[1], "op_type": r[2], "completed_at": r[3]}
        for r in rows
    ]


def _get_queue_items(db_path: Path, cycle_id: str) -> list[dict]:
    """Fetch queue items for a cycle."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT cycle_id, ticker, priority_band, pinned, enqueued_at "
            "FROM queue WHERE cycle_id = ? ORDER BY priority_band, ticker",
            (cycle_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "cycle_id": r[0], "ticker": r[1], "priority_band": r[2],
            "pinned": r[3], "enqueued_at": r[4],
        }
        for r in rows
    ]


# -- Tests --


class TestFxAndCorpActions:
    """Step 2: FX fetch completes. Step 3: Corporate actions processed."""

    def test_fx_and_corp_actions(
        self,
        orchestrator: CycleOrchestrator,
        tmp_db: Path,
    ) -> None:
        """FX fetch stores rate in fx_snapshots, corp actions checks holdings."""
        mock_rate = _make_mock_fx_rate()

        with patch(
            "pmacs.data.fx.fetch_ecb_rate",
            return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner"
        ):
            cycle_id = orchestrator.run_cycle("TIMER")

        # Verify FX snapshot in SQLite
        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT usd_per_eur, business_date FROM fx_snapshots WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert row[0] == 1.085
        assert row[1] == "2026-05-12"

        # Verify op_idempotency recorded steps
        ops = _get_op_idempotency(tmp_db, cycle_id)
        op_types = {o["op_type"] for o in ops}
        assert "fx_snapshot" in op_types

    def test_fx_failure_aborts_cycle(
        self,
        orchestrator: CycleOrchestrator,
        tmp_db: Path,
    ) -> None:
        """FX fetch failure aborts the cycle with FX_UNAVAILABLE."""

        with patch(
            "pmacs.data.fx.fetch_ecb_rate",
            side_effect=RuntimeError("ECB timeout"),
        ):
            with pytest.raises(RuntimeError, match="FX_RATE_UNAVAILABLE"):
                orchestrator.run_cycle("TIMER")


class TestGatekeeperFiltersUniverse:
    """Step 9: Gatekeeper admits/rejects universe tickers."""

    def test_gatekeeper_filters_universe(
        self,
        orchestrator: CycleOrchestrator,
        tmp_db: Path,
    ) -> None:
        """5 tickers in universe, 3 pass gatekeeper, 2 rejected."""
        mock_rate = _make_mock_fx_rate()

        # Create gatekeeper results: 3 admitted, 2 rejected
        gate_results = {
            "AAPL": GatekeeperResult(ticker="AAPL", admitted=True),
            "MSFT": GatekeeperResult(ticker="MSFT", admitted=True),
            "GOOGL": GatekeeperResult(ticker="GOOGL", admitted=True),
            "TSLA": GatekeeperResult(ticker="TSLA", admitted=False, reject_reason="PORTFOLIO_LIMIT_HIT"),
            "NVDA": GatekeeperResult(ticker="NVDA", admitted=False, reject_reason="ADV_BELOW_THRESHOLD"),
        }

        with patch(
            "pmacs.data.fx.fetch_ecb_rate",
            return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner"
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results[ticker],
        ):
            cycle_id = orchestrator.run_cycle("TIMER")

        # Verify gatekeeper results stored on orchestrator
        assert len(orchestrator._gatekeeper_results) == 5
        admitted = {
            t for t, r in orchestrator._gatekeeper_results.items() if r.admitted
        }
        assert admitted == {"AAPL", "MSFT", "GOOGL"}

        # Verify op_idempotency
        ops = _get_op_idempotency(tmp_db, cycle_id)
        op_types = {o["op_type"] for o in ops}
        assert "gatekeeper" in op_types


class TestQueueCompositionPriority:
    """Step 12: Queue sorted by priority, active holdings in P1."""

    def test_queue_composition_priority(
        self,
        orchestrator: CycleOrchestrator,
        tmp_db: Path,
    ) -> None:
        """Queue items sorted by priority band, pinned tickers in P1."""
        mock_rate = _make_mock_fx_rate()

        # All admitted, AAPL and MSFT pinned
        gate_results = {
            "AAPL": GatekeeperResult(ticker="AAPL", admitted=True),
            "MSFT": GatekeeperResult(ticker="MSFT", admitted=True),
            "GOOGL": GatekeeperResult(ticker="GOOGL", admitted=True),
            "TSLA": GatekeeperResult(ticker="TSLA", admitted=True),
            "NVDA": GatekeeperResult(ticker="NVDA", admitted=True),
        }

        # Add persistent pins for AAPL and MSFT
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO persistent_pins (ticker, priority_band, pinned_at) "
                "VALUES (?, ?, ?)",
                ("AAPL", 1, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO persistent_pins (ticker, priority_band, pinned_at) "
                "VALUES (?, ?, ?)",
                ("MSFT", 1, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        with patch(
            "pmacs.data.fx.fetch_ecb_rate",
            return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner"
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results[ticker],
        ):
            cycle_id = orchestrator.run_cycle("TIMER")

        # Verify queue in SQLite
        queue_items = _get_queue_items(tmp_db, cycle_id)
        assert len(queue_items) == 5

        # Pinned items should be P1
        pinned_items = [q for q in queue_items if q["pinned"] == 1]
        assert len(pinned_items) == 2
        pinned_tickers = {q["ticker"] for q in pinned_items}
        assert pinned_tickers == {"AAPL", "MSFT"}
        for item in pinned_items:
            assert item["priority_band"] == PriorityBand.P1_HIGHEST

        # Non-pinned admitted should be P3
        unpinned = [q for q in queue_items if q["pinned"] == 0]
        for item in unpinned:
            assert item["priority_band"] == PriorityBand.P3_NORMAL

        # Queue on orchestrator matches
        assert len(orchestrator._queue) == 5


class TestMacroRegimeStored:
    """Step 6: Macro regime result available after pre-cycle."""

    def test_macro_regime_stored(
        self,
        orchestrator: CycleOrchestrator,
        tmp_db: Path,
    ) -> None:
        """MacroRegime result is stored on self._macro_regime_result."""
        mock_rate = _make_mock_fx_rate()
        mock_output = _make_mock_persona_output()

        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = mock_output

        with patch(
            "pmacs.data.fx.fetch_ecb_rate",
            return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
            return_value=mock_runner_instance,
        ), patch(
            "pmacs.config.load_config",
            side_effect=Exception("no config"),
        ):
            cycle_id = orchestrator.run_cycle("TIMER")

        # Verify macro regime result stored
        assert orchestrator._macro_regime_result is not None
        assert orchestrator._macro_regime_result is mock_output

        # Verify runner was called
        mock_runner_instance.run.assert_called_once()

        # Verify op_idempotency
        ops = _get_op_idempotency(tmp_db, cycle_id)
        op_types = {o["op_type"] for o in ops}
        assert "macro_regime" in op_types

    def test_macro_regime_failure_continues(
        self,
        orchestrator: CycleOrchestrator,
        tmp_db: Path,
    ) -> None:
        """MacroRegime failure does not abort the cycle."""
        mock_rate = _make_mock_fx_rate()

        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = None  # Simulates LLM failure

        with patch(
            "pmacs.data.fx.fetch_ecb_rate",
            return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
            return_value=mock_runner_instance,
        ), patch(
            "pmacs.config.load_config",
            side_effect=Exception("no config"),
        ):
            cycle_id = orchestrator.run_cycle("TIMER")

        # Cycle should still complete
        assert cycle_id is not None
        # Macro regime result should be None
        assert orchestrator._macro_regime_result is None


class TestPreCycleIdempotency:
    """Re-running pre-cycle skips completed steps."""

    def test_pre_cycle_idempotency(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """Steps 2-12 recorded in op_idempotency; all present after full cycle."""
        mock_rate = _make_mock_fx_rate()
        mock_output = _make_mock_persona_output()

        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = mock_output

        gate_results = {
            "AAPL": GatekeeperResult(ticker="AAPL", admitted=True),
            "MSFT": GatekeeperResult(ticker="MSFT", admitted=True),
            "GOOGL": GatekeeperResult(ticker="GOOGL", admitted=True),
            "TSLA": GatekeeperResult(ticker="TSLA", admitted=True),
            "NVDA": GatekeeperResult(ticker="NVDA", admitted=True),
        }

        with patch(
            "pmacs.data.fx.fetch_ecb_rate",
            return_value=mock_rate,
        ), patch(
            "pmacs.agents.macro_regime.MacroRegimeRunner",
            return_value=mock_runner_instance,
        ), patch(
            "pmacs.agents.gatekeeper.gate",
            side_effect=lambda ticker, cycle_id, **kw: gate_results[ticker],
        ):
            orch = CycleOrchestrator(
                db_path=tmp_db,
                audit_path=tmp_audit,
                sse_publisher=publisher,
                config=config,
            )
            cycle_id = orch.run_cycle("TIMER")

        # Verify all pre-cycle steps are in op_idempotency
        ops = _get_op_idempotency(tmp_db, cycle_id)
        op_types = {o["op_type"] for o in ops}

        expected_pre_cycle = {
            "fx_snapshot",       # op_seq 3: FX + corporate actions combined
            "macro_regime",
            "catalyst_resolution",
            "universe_sync",
            "gatekeeper",
            "lessons_flagger",
            "override_learning",
            "queue_composition",
        }
        assert expected_pre_cycle.issubset(op_types), (
            f"Missing pre-cycle ops: {expected_pre_cycle - op_types}"
        )

        # Verify no duplicate op_seq entries
        seq_counts: dict[int, int] = {}
        for op in ops:
            seq_counts[op["op_seq"]] = seq_counts.get(op["op_seq"], 0) + 1
        for seq, count in seq_counts.items():
            assert count == 1, f"op_seq {seq} recorded {count} times (expected 1)"

        # Verify the full expected op_seq set
        expected_seqs = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 29}
        actual_seqs = {o["op_seq"] for o in ops}
        assert expected_seqs == actual_seqs, (
            f"Op seq mismatch: expected {expected_seqs}, got {actual_seqs}"
        )
