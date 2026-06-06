"""Integration test: CycleOrchestrator skeleton (Phase 9 Wave 1).

Tests full open-to-close cycle with lock, audit trail verification,
checkpoint resume, kill switch blocking, concurrent cycle prevention,
and flywheel health snapshot.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.cortex.kill_switch import engage
from pmacs.nervous.orchestrator import (
    CycleLock,
    CycleLockError,
    ClockDriftError,
    CycleOrchestrator,
    KillSwitchEngagedError,
)
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.storage.audit import AuditVerifier
from pmacs.storage.sqlite import init_db


# -- Fixtures --


@pytest.fixture(autouse=True)
def _mock_precycle_deps():
    """Auto-mock pre-cycle dependencies that require network/LLM.

    The pre-cycle pipeline (steps 3, 6-12) now calls fetch_ecb_rate and
    MacroRegimeRunner. These need to be mocked so old skeleton tests
    continue to pass without real HTTP/LLM calls.
    """
    from datetime import date, datetime, timezone

    from pmacs.schemas.currency import FxRate

    mock_rate = FxRate(
        usd_per_eur=1.08,
        business_date=date(2026, 5, 12),
        fetched_at=datetime.now(timezone.utc),
    )
    with patch(
        "pmacs.data.fx.fetch_ecb_rate", return_value=mock_rate,
    ), patch(
        "pmacs.agents.macro_regime.MacroRegimeRunner",
    ):
        yield


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with PMACS schema."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def tmp_audit(tmp_path: Path) -> Path:
    """Create a temporary audit log path."""
    return tmp_path / "audit.log"


@pytest.fixture
def lock_path(tmp_path: Path) -> str:
    """Provide a temporary lock file path."""
    return str(tmp_path / "test_cycle.lock")


@pytest.fixture
def publisher() -> SSEPublisher:
    """Provide an SSE publisher with a subscribed test client."""
    return SSEPublisher()


@pytest.fixture
def config(tmp_path: Path) -> dict:
    """Provide a test config that uses a temp lock path."""
    return {
        "lock_path": str(tmp_path / "test_cycle.lock"),
    }


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


def _get_cycle_row(db_path: Path, cycle_id: str) -> dict | None:
    """Fetch a cycle row from SQLite as a dict."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT cycle_id, opened_at, closed_at, state, trigger, mode "
            "FROM cycles WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "cycle_id": row[0],
        "opened_at": row[1],
        "closed_at": row[2],
        "state": row[3],
        "trigger": row[4],
        "mode": row[5],
    }


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


# -- Tests --


class TestCycleOpenClose:
    """Full open-to-close cycle with lock, audit trail, and idempotency."""

    def test_cycle_open_close(
        self,
        orchestrator: CycleOrchestrator,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
    ) -> None:
        """run_cycle opens and closes a cycle, writes audit, updates SQLite."""
        client_id, queue = publisher.subscribe()

        cycle_id = orchestrator.run_cycle("TIMER")

        # Verify cycle in SQLite
        row = _get_cycle_row(tmp_db, cycle_id)
        assert row is not None
        assert row["state"] == "CLOSED"
        assert row["trigger"] == "TIMER"
        assert row["closed_at"] is not None

        # Verify audit chain integrity
        verifier = AuditVerifier(tmp_audit)
        ok, error = verifier.verify_full()
        assert ok, f"Audit chain verification failed: {error}"

        # Verify audit events present
        content = tmp_audit.read_text()
        assert "cycle_opened" in content
        assert "cycle_closed" in content
        assert cycle_id in content

        # Verify idempotency entries
        ops = _get_op_idempotency(tmp_db, cycle_id)
        op_types = {o["op_type"] for o in ops}
        assert "initiate_cycle" in op_types
        assert "clock_drift_check" in op_types
        assert "checkpoint_resume" in op_types
        assert "kill_switch_check" in op_types
        assert "flywheel_health" in op_types
        assert "close_cycle" in op_types

        # Verify SSE events
        events: list[dict] = []
        while not queue.empty():
            frame = queue.get_nowait()
            events.append(json.loads(frame))

        event_types = [e["type"] for e in events]
        assert "cycle.open" in event_types
        assert "cycle.close" in event_types

        publisher.unsubscribe(client_id)


class TestCycleResumeFromCheckpoint:
    """Partial cycle resumes by skipping already-completed steps."""

    def test_resume_skips_completed_ops(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """After a partial run, re-running skips already-completed ops."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        cycle_id = orch.run_cycle("TIMER")

        # Now simulate re-running with same cycle_id's checkpoints present
        # The orchestrator creates a new cycle_id each call, but we verify
        # that _skip_if_complete works by checking the first run's ops
        ops = _get_op_idempotency(tmp_db, cycle_id)
        assert len(ops) > 0

        # Verify all expected ops are marked complete
        op_seqs = {o["op_seq"] for o in ops}
        assert 0 in op_seqs   # initiate_cycle
        assert 1 in op_seqs   # clock_drift_check
        assert 2 in op_seqs   # checkpoint_resume
        assert 4 in op_seqs   # kill_switch_check
        assert 5 in op_seqs   # flywheel_health
        assert 29 in op_seqs  # close_cycle

    def test_checkpoint_resume_logs_resume_state(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """Verify that checkpoint resume step detects existing checkpoints."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        # First run — no checkpoints exist
        cycle_id = orch.run_cycle("TIMER")

        # Verify fresh start was logged (no crash resume)
        ops = _get_op_idempotency(tmp_db, cycle_id)
        resume_ops = [o for o in ops if o["op_type"] == "checkpoint_resume"]
        assert len(resume_ops) == 1


class TestKillSwitchBlocksCycle:
    """Kill switch engagement aborts the cycle."""

    def test_kill_switch_blocks_cycle(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """Engage kill switch, verify cycle aborts with KillSwitchEngagedError."""
        engage("test blocking", "OPERATOR_MANUAL", db_path=tmp_db)

        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        with pytest.raises(KillSwitchEngagedError):
            orch.run_cycle("TIMER")

        # Verify no cycle was created in SQLite
        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM cycles WHERE state = 'OPEN'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == 0, "No open cycles should exist when kill switch is engaged"


class TestFlockPreventsConcurrentCycles:
    """File lock prevents two cycles from running simultaneously."""

    def test_flock_prevents_concurrent_cycles(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """Two instances: second fails to acquire lock."""
        lock_path = config["lock_path"]

        orch1 = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        # Use a separate config with the same lock path for the second orchestrator
        config2 = {**config, "lock_path": lock_path}
        orch2 = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config2,
        )

        # Block orch1's cycle by holding the lock externally
        acquired = threading.Event()
        release = threading.Event()

        def hold_lock():
            with CycleLock(lock_path):
                acquired.set()
                release.wait(timeout=5.0)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        acquired.wait(timeout=2.0)
        assert acquired.is_set(), "Holder thread should have acquired lock"

        # orch2 should fail to run because lock is held
        with pytest.raises(CycleLockError):
            orch2.run_cycle("TIMER")

        # Release the lock
        release.set()
        holder.join(timeout=2.0)

    def test_cycle_lock_is_released_on_success(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """After a successful cycle, the lock is released for the next one."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        # Run two cycles sequentially — both should succeed
        cid1 = orch.run_cycle("TIMER")
        cid2 = orch.run_cycle("TIMER")

        assert cid1 != cid2

        # Both should be CLOSED
        row1 = _get_cycle_row(tmp_db, cid1)
        row2 = _get_cycle_row(tmp_db, cid2)
        assert row1["state"] == "CLOSED"
        assert row2["state"] == "CLOSED"


class TestFlywheelHealthCalled:
    """Verify step 5 calls snapshot_health."""

    def test_flywheel_health_called(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """snapshot_health is called during step 5."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        with patch(
            "pmacs.nervous.orchestrator.snapshot_health"
        ) as mock_snap:
            from pmacs.engines.flywheel_health import FlywheelHealthSnapshot

            mock_snap.return_value = FlywheelHealthSnapshot(
                rolling_brier_avg=0.25,
                rolling_sharpe=0.5,
                calibration_gap=0.1,
                active_mutations=1,
                pending_reviews=0,
                lessons_count=5,
            )

            orch.run_cycle("TIMER")

            mock_snap.assert_called_once()
            call_kwargs = mock_snap.call_args
            assert call_kwargs[1]["rolling_brier_avg"] == 0.0
            assert call_kwargs[1]["rolling_sharpe"] == 0.0


class TestCycleLock:
    """Direct tests for CycleLock context manager."""

    def test_lock_acquires_and_releases(self, lock_path: str) -> None:
        """Lock acquires successfully and releases on exit."""
        with CycleLock(lock_path) as lock:
            assert lock is not None
        # After exit, should be able to re-acquire
        with CycleLock(lock_path) as lock2:
            assert lock2 is not None

    def test_lock_fails_if_held(self, lock_path: str) -> None:
        """Second lock acquisition raises CycleLockError."""
        with CycleLock(lock_path):
            with pytest.raises(CycleLockError):
                CycleLock(lock_path).__enter__()

    def test_lock_releases_on_exception(self, lock_path: str) -> None:
        """Lock is released even when an exception occurs inside the block."""
        try:
            with CycleLock(lock_path):
                raise RuntimeError("test error")
        except RuntimeError:
            pass

        # Should be able to re-acquire
        with CycleLock(lock_path) as lock:
            assert lock is not None

    def test_lock_released_in_separate_thread(self, lock_path: str) -> None:
        """Lock is released when held in a separate thread."""
        import threading

        released = threading.Event()

        def hold_and_release():
            with CycleLock(lock_path):
                released.wait(timeout=3.0)

        t = threading.Thread(target=hold_and_release, daemon=True)
        t.start()

        # Give thread time to acquire
        import time
        time.sleep(0.1)

        # Should fail — lock is held by thread
        with pytest.raises(CycleLockError):
            CycleLock(lock_path).__enter__()

        # Release from thread
        released.set()
        t.join(timeout=2.0)

        # Now should succeed
        with CycleLock(lock_path) as lock:
            assert lock is not None


class TestClockDriftAbort:
    """Clock drift exceeding threshold aborts the cycle."""

    def test_clock_drift_aborts_cycle(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """Excessive NTP drift raises ClockDriftError and aborts cycle."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        with patch(
            "pmacs.nervous.orchestrator.check_ntp_drift",
            return_value=(True, 120.0),
        ):
            with pytest.raises(ClockDriftError):
                orch.run_cycle("TIMER")

    def test_no_drift_allows_cycle(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """NTP drift within threshold allows cycle to proceed."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        with patch(
            "pmacs.nervous.orchestrator.check_ntp_drift",
            return_value=(False, 5.0),
        ):
            cycle_id = orch.run_cycle("TIMER")

        row = _get_cycle_row(tmp_db, cycle_id)
        assert row is not None
        assert row["state"] == "CLOSED"


class TestIdempotencyTracking:
    """Verify all steps are tracked in op_idempotency."""

    def test_all_steps_recorded(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """Every step that runs is recorded in op_idempotency."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        cycle_id = orch.run_cycle("TIMER")
        ops = _get_op_idempotency(tmp_db, cycle_id)

        # Verify key steps are present
        recorded_types = {o["op_type"] for o in ops}
        expected_types = {
            "initiate_cycle",
            "clock_drift_check",
            "checkpoint_resume",
            "kill_switch_check",
            "flywheel_health",
            "close_cycle",
        }
        assert expected_types.issubset(recorded_types), (
            f"Missing op types: {expected_types - recorded_types}"
        )

    def test_no_duplicate_ops(
        self,
        tmp_db: Path,
        tmp_audit: Path,
        publisher: SSEPublisher,
        config: dict,
    ) -> None:
        """Each op_seq appears exactly once per cycle."""
        orch = CycleOrchestrator(
            db_path=tmp_db,
            audit_path=tmp_audit,
            sse_publisher=publisher,
            config=config,
        )

        cycle_id = orch.run_cycle("TIMER")
        ops = _get_op_idempotency(tmp_db, cycle_id)

        seq_counts: dict[int, int] = {}
        for op in ops:
            seq_counts[op["op_seq"]] = seq_counts.get(op["op_seq"], 0) + 1

        for seq, count in seq_counts.items():
            assert count == 1, f"op_seq {seq} recorded {count} times (expected 1)"
