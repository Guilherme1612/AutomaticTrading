"""Tests for Nervous SQLite stop event poller.

Task 3 [S1]: StopEventPoller -- poll, process, state transitions.
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.nervous.stop_poller import StopEventPoller
from pmacs.schemas.contracts import Holding, HoldingState
from pmacs.schemas.stop_loss import StopEventStatus, StopType
from pmacs.storage.sqlite import init_db


def _make_holding(
    holding_id: str = "h-001",
    state: HoldingState = HoldingState.ACTIVE,
) -> Holding:
    """Create a test Holding."""
    return Holding(
        id=holding_id,
        ticker="TEST",
        state=state,
        cycle_id_opened="cycle-001",
        entry_date=date(2026, 1, 15),
        entry_price_usd=100.0,
        position_size_usd=1000.0,
        stop_price_usd=85.0,
        conviction_score=0.5,
        created_at=datetime(2026, 1, 15, 12, 0, 0),
        updated_at=datetime(2026, 1, 15, 12, 0, 0),
    )


def _setup_db(db_path: Path) -> sqlite3.Connection:
    """Initialize a test database with stop_events."""
    conn = init_db(db_path)
    return conn


def _insert_stop_event(
    conn: sqlite3.Connection,
    holding_id: str = "h-001",
    ticker: str = "TEST",
    stop_type: str = "FIXED_STOP",
    status: str = "PENDING",
    stop_type_category: str = "FIXED",
    cycle_id: str = "cycle-001",
) -> int:
    """Insert a stop event and return its ID."""
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        """INSERT INTO stop_events
           (holding_id, ticker, stop_type, trigger_price_usd, stop_price_usd,
            detected_at, cycle_id, status, stop_type_category, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (holding_id, ticker, stop_type, 84.0, 85.0, now, cycle_id, status, stop_type_category, now),
    )
    conn.commit()
    return cursor.lastrowid


class TestPollPending:
    """poll_pending tests."""

    def test_poll_returns_empty_when_no_pending(self):
        """Poll returns empty list when no PENDING triggers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = _setup_db(db_path)
            # Insert a FILLED event (not PENDING)
            _insert_stop_event(conn, status="FILLED")
            conn.close()

            poller = StopEventPoller(db_path)
            results = poller.poll_pending()
            assert results == []

    def test_poll_returns_triggers_with_status_pending(self):
        """Poll returns only PENDING triggers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = _setup_db(db_path)
            _insert_stop_event(conn, status="PENDING", holding_id="h-001")
            _insert_stop_event(conn, status="FILLED", holding_id="h-002")
            _insert_stop_event(conn, status="PENDING", holding_id="h-003")
            conn.close()

            poller = StopEventPoller(db_path)
            results = poller.poll_pending()
            assert len(results) == 2
            holding_ids = {r["holding_id"] for r in results}
            assert holding_ids == {"h-001", "h-003"}
            for r in results:
                assert r["stop_type"] is not None


class TestProcessTrigger:
    """process_trigger tests."""

    def test_process_trigger_calls_execute_exit(self):
        """process_trigger calls execute_exit for the holding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = _setup_db(db_path)
            event_id = _insert_stop_event(conn, status="PENDING")
            conn.close()

            holding = _make_holding()
            trigger = {
                "id": event_id,
                "holding_id": "h-001",
                "ticker": "TEST",
                "stop_type": "FIXED_STOP",
                "trigger_price_usd": 84.0,
                "stop_price_usd": 85.0,
                "detected_at": datetime.utcnow().isoformat(),
                "cycle_id": "cycle-001",
                "stop_type_category": "FIXED",
            }

            with patch("pmacs.nervous.stop_poller.execute_exit") as mock_exit:
                poller = StopEventPoller(db_path)
                poller.process_trigger(trigger, holding=holding, cycle_id="cycle-001")
                mock_exit.assert_called_once()

    def test_process_trigger_updates_status_to_filled(self):
        """process_trigger updates stop_events status to FILLED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = _setup_db(db_path)
            event_id = _insert_stop_event(conn, status="PENDING")
            conn.close()

            holding = _make_holding()
            trigger = {
                "id": event_id,
                "holding_id": "h-001",
                "ticker": "TEST",
                "stop_type": "FIXED_STOP",
                "trigger_price_usd": 84.0,
                "stop_price_usd": 85.0,
                "detected_at": datetime.utcnow().isoformat(),
                "cycle_id": "cycle-001",
                "stop_type_category": "FIXED",
            }

            with patch("pmacs.nervous.stop_poller.execute_exit"):
                poller = StopEventPoller(db_path)
                poller.process_trigger(trigger, holding=holding, cycle_id="cycle-001")

            # Verify status is FILLED in DB
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT status FROM stop_events WHERE id = ?", (event_id,)
            ).fetchone()
            conn.close()
            assert row[0] == "FILLED"

    def test_process_trigger_transitions_to_stopped_out_for_fixed(self):
        """Fixed stop triggers STOPPED_OUT state transition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = _setup_db(db_path)
            event_id = _insert_stop_event(conn, stop_type="FIXED_STOP", status="PENDING")
            conn.close()

            holding = _make_holding()
            trigger = {
                "id": event_id,
                "holding_id": "h-001",
                "ticker": "TEST",
                "stop_type": "FIXED_STOP",
                "trigger_price_usd": 84.0,
                "stop_price_usd": 85.0,
                "detected_at": datetime.utcnow().isoformat(),
                "cycle_id": "cycle-001",
                "stop_type_category": "FIXED",
            }

            with patch("pmacs.nervous.stop_poller.execute_exit"):
                poller = StopEventPoller(db_path)
                poller.process_trigger(trigger, holding=holding, cycle_id="cycle-001")

            assert holding.state == HoldingState.STOPPED_OUT

    def test_process_trigger_transitions_to_exit_trailing_for_trailing(self):
        """Trailing stop triggers EXIT_TRAILING_STOP state transition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = _setup_db(db_path)
            event_id = _insert_stop_event(conn, stop_type="TRAILING_STOP", status="PENDING")
            conn.close()

            holding = _make_holding()
            trigger = {
                "id": event_id,
                "holding_id": "h-001",
                "ticker": "TEST",
                "stop_type": "TRAILING_STOP",
                "trigger_price_usd": 89.0,
                "stop_price_usd": 90.0,
                "detected_at": datetime.utcnow().isoformat(),
                "cycle_id": "cycle-001",
                "stop_type_category": "TRAILING",
            }

            with patch("pmacs.nervous.stop_poller.execute_exit"):
                poller = StopEventPoller(db_path)
                poller.process_trigger(trigger, holding=holding, cycle_id="cycle-001")

            assert holding.state == HoldingState.EXIT_TRAILING_STOP


class TestPollLoopRTH:
    """Poll loop RTH gating tests."""

    def test_poll_loop_skips_non_rth(self):
        """Poll loop skips processing when not in RTH."""
        import time as time_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            conn = _setup_db(db_path)
            _insert_stop_event(conn, status="PENDING")
            conn.close()

            poller = StopEventPoller(db_path)

            # Patch is_rth to return False and time.sleep to break loop
            call_count = {"n": 0}

            def mock_sleep(seconds):
                call_count["n"] += 1
                if call_count["n"] >= 2:
                    raise SystemExit("loop exited")

            with patch("pmacs.stop_loss_daemon.is_rth", return_value=False), \
                 patch("pmacs.nervous.stop_poller.time") as mock_time:
                mock_time.sleep = mock_sleep
                mock_time.time = time_mod.time

                # Poll_pending should NOT be called
                with patch.object(poller, "poll_pending") as mock_poll:
                    with pytest.raises(SystemExit):
                        poller.run_poll_loop(interval_s=1)
                    mock_poll.assert_not_called()
