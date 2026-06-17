"""Integration test: cycle stub lifecycle (Phase 2 Wave 5).

Tests initiate_cycle -> close_cycle with audit events, SSE events,
and kill switch blocking. Uses temp SQLite paths.
"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from pmacs.cortex.kill_switch import engage
from pmacs.nervous.orchestrator import (
    KillSwitchEngagedError,
    close_cycle,
    initiate_cycle,
)
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.storage.audit import AuditVerifier
from pmacs.storage.sqlite import init_db


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
def publisher() -> SSEPublisher:
    return SSEPublisher()


class TestCycleStub:
    """Test cycle open/close lifecycle."""

    def test_initiate_and_close_cycle(
        self, tmp_db: Path, tmp_audit: Path, publisher: SSEPublisher
    ) -> None:
        """initiate_cycle creates OPEN cycle, close_cycle sets CLOSED."""
        # Wire publisher into orchestrator
        initiate_cycle._publisher = publisher  # type: ignore[attr-defined]
        close_cycle._publisher = publisher  # type: ignore[attr-defined]

        cycle_id = initiate_cycle("TIMER", tmp_db, audit_path=tmp_audit)
        assert cycle_id, "cycle_id should be returned"
        assert len(cycle_id) == 36, "cycle_id should be UUID4 format"

        # Verify SQLite state
        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT state, trigger, mode FROM cycles WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "Cycle should be in DB"
        assert row[0] == "OPEN"
        assert row[1] == "TIMER"

        # Close cycle
        close_cycle(cycle_id, tmp_db, audit_path=tmp_audit)

        # Verify closed state
        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT state, closed_at FROM cycles WHERE cycle_id = ?",
                (cycle_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "CLOSED"
        assert row[1] is not None

    def test_audit_events_written(
        self, tmp_db: Path, tmp_audit: Path, publisher: SSEPublisher
    ) -> None:
        """Audit log records cycle_opened and cycle_closed events."""
        initiate_cycle._publisher = publisher  # type: ignore[attr-defined]
        close_cycle._publisher = publisher  # type: ignore[attr-defined]

        cycle_id = initiate_cycle("TIMER", tmp_db, audit_path=tmp_audit)
        close_cycle(cycle_id, tmp_db, audit_path=tmp_audit)

        # Verify audit chain
        verifier = AuditVerifier(tmp_audit)
        ok, error = verifier.verify_full()
        assert ok, f"Audit chain verification failed: {error}"

        # Verify both events present
        content = tmp_audit.read_text()
        assert "cycle_opened" in content
        assert "cycle_closed" in content
        assert cycle_id in content

    def test_sse_events_emitted(
        self, tmp_db: Path, tmp_audit: Path, publisher: SSEPublisher
    ) -> None:
        """SSE publisher emits cycle.open and cycle.close events."""
        initiate_cycle._publisher = publisher  # type: ignore[attr-defined]
        close_cycle._publisher = publisher  # type: ignore[attr-defined]

        # Subscribe a test client
        client_id, queue = publisher.subscribe()

        cycle_id = initiate_cycle("TIMER", tmp_db, audit_path=tmp_audit)
        close_cycle(cycle_id, tmp_db, audit_path=tmp_audit)

        # Drain events from the queue
        events: list[dict] = []
        while not queue.empty():
            import json

            frame = queue.get_nowait()
            events.append(json.loads(frame))

        # Should have at least 2 events (open + close)
        streams = [e["stream"] for e in events]
        types = [e["type"] for e in events]

        assert "cycle" in streams, "Should have cycle stream events"
        assert "cycle.open" in types, "Should have cycle.open event"
        assert "cycle.close" in types, "Should have cycle.close event"

        # Verify event data contains cycle_id
        open_events = [e for e in events if e["type"] == "cycle.open"]
        assert open_events[0]["data"]["cycle_id"] == cycle_id

        publisher.unsubscribe(client_id)

    def test_kill_switch_blocks_cycle(
        self, tmp_db: Path, tmp_audit: Path, publisher: SSEPublisher
    ) -> None:
        """Kill switch engaged state prevents cycle initiation."""
        initiate_cycle._publisher = publisher  # type: ignore[attr-defined]

        # Engage kill switch
        engage("test", "OPERATOR_MANUAL", db_path=tmp_db, audit_path=tmp_audit)

        # Attempting to initiate should raise
        with pytest.raises(KillSwitchEngagedError):
            initiate_cycle("TIMER", tmp_db, audit_path=tmp_audit)

    def test_multiple_cycles(
        self, tmp_db: Path, tmp_audit: Path, publisher: SSEPublisher
    ) -> None:
        """Multiple cycles can be opened and closed sequentially."""
        initiate_cycle._publisher = publisher  # type: ignore[attr-defined]
        close_cycle._publisher = publisher  # type: ignore[attr-defined]

        ids = []
        for _ in range(3):
            cid = initiate_cycle("TIMER", tmp_db, audit_path=tmp_audit)
            close_cycle(cid, tmp_db, audit_path=tmp_audit)
            ids.append(cid)

        # All 3 should be distinct
        assert len(set(ids)) == 3

        # All should be CLOSED in DB
        conn = sqlite3.connect(str(tmp_db))
        try:
            rows = conn.execute(
                "SELECT cycle_id, state FROM cycles ORDER BY opened_at"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 3
        assert all(r[1] == "CLOSED" for r in rows)

    def test_no_audit_path_no_error(
        self, tmp_db: Path, publisher: SSEPublisher
    ) -> None:
        """Cycles work without audit_path (None)."""
        initiate_cycle._publisher = publisher  # type: ignore[attr-defined]
        close_cycle._publisher = publisher  # type: ignore[attr-defined]

        cycle_id = initiate_cycle("TIMER", tmp_db, audit_path=None)
        close_cycle(cycle_id, tmp_db, audit_path=None)
        # Should not raise

    def test_no_publisher_no_error(self, tmp_db: Path, tmp_audit: Path) -> None:
        """Cycles work without a wired publisher."""
        initiate_cycle._publisher = None  # type: ignore[attr-defined]
        close_cycle._publisher = None  # type: ignore[attr-defined]

        cycle_id = initiate_cycle("TIMER", tmp_db, audit_path=tmp_audit)
        close_cycle(cycle_id, tmp_db, audit_path=tmp_audit)
        # Should not raise


class TestCheckpoint:
    """Test checkpoint system."""

    def test_save_and_load(
        self, tmp_db: Path
    ) -> None:
        """save_checkpoint stores, load_checkpoint retrieves."""
        from pmacs.nervous.checkpoint import load_checkpoint, save_checkpoint

        save_checkpoint("cycle-1", 1, "fetch_data", tmp_db)
        state = load_checkpoint("cycle-1", tmp_db)
        assert state is not None
        assert state.cycle_id == "cycle-1"
        assert state.op_seq == 1
        assert state.op_type == "fetch_data"
        assert state.completed_at is not None

    def test_is_completed(
        self, tmp_db: Path
    ) -> None:
        """is_completed returns True for saved ops, False for unsaved."""
        from pmacs.nervous.checkpoint import is_completed, save_checkpoint

        assert not is_completed("cycle-1", 1, tmp_db)
        save_checkpoint("cycle-1", 1, "fetch_data", tmp_db)
        assert is_completed("cycle-1", 1, tmp_db)
        assert not is_completed("cycle-1", 2, tmp_db)

    def test_load_nonexistent(
        self, tmp_db: Path
    ) -> None:
        """load_checkpoint returns None for unknown cycle."""
        from pmacs.nervous.checkpoint import load_checkpoint

        assert load_checkpoint("nonexistent", tmp_db) is None

    def test_last_checkpoint_wins(
        self, tmp_db: Path
    ) -> None:
        """load_checkpoint returns the highest op_seq checkpoint."""
        from pmacs.nervous.checkpoint import load_checkpoint, save_checkpoint

        save_checkpoint("cycle-1", 1, "fetch_data", tmp_db)
        save_checkpoint("cycle-1", 3, "run_persona", tmp_db)
        save_checkpoint("cycle-1", 5, "decide", tmp_db)

        state = load_checkpoint("cycle-1", tmp_db)
        assert state is not None
        assert state.op_seq == 5
        assert state.op_type == "decide"


class TestSSEPublisher:
    """Test SSE publisher directly."""

    def test_publish_without_clients(self) -> None:
        """Publishing with no clients does not raise."""
        pub = SSEPublisher()
        eid = pub.publish("cycle", "cycle.open", {"cycle_id": "test"})
        # IDs are monotonic numeric strings seeded from a ms timestamp.
        assert eid.isdigit()
        eid2 = pub.publish("cycle", "cycle.open", {"cycle_id": "test2"})
        assert int(eid2) == int(eid) + 1

    def test_publish_delivers_to_client(self) -> None:
        """Events are delivered to subscribed clients."""
        pub = SSEPublisher()
        cid, queue = pub.subscribe()

        import json

        eid = pub.publish("cycle", "cycle.open", {"cycle_id": "abc"})
        frame = queue.get_nowait()
        event = json.loads(frame)

        assert event["stream"] == "cycle"
        assert event["type"] == "cycle.open"
        assert event["data"]["cycle_id"] == "abc"
        assert event["id"] == eid

        pub.unsubscribe(cid)

    def test_auto_incrementing_ids(self) -> None:
        """Event IDs auto-increment."""
        pub = SSEPublisher()
        id1 = pub.publish("system", "test", {})
        id2 = pub.publish("system", "test", {})
        id3 = pub.publish("system", "test", {})
        assert int(id1) < int(id2) < int(id3)


class TestSessionManager:
    """Test session auth."""

    def test_create_and_verify(self) -> None:
        """Created session verifies successfully."""
        from pmacs.nervous.auth import SessionManager

        mgr = SessionManager()
        info = mgr.create_session()
        assert mgr.verify_session(info.token)

    def test_new_invalidates_old(self) -> None:
        """New session creation invalidates previous."""
        from pmacs.nervous.auth import SessionManager

        mgr = SessionManager()
        info1 = mgr.create_session()
        info2 = mgr.create_session()
        assert not mgr.verify_session(info1.token)
        assert mgr.verify_session(info2.token)

    def test_expired_session_fails(self) -> None:
        """Expired sessions fail verification."""
        from pmacs.nervous.auth import SessionManager

        mgr = SessionManager(session_duration_s=-1)  # Already expired
        info = mgr.create_session()
        assert not mgr.verify_session(info.token)

    def test_invalidate_session(self) -> None:
        """Explicit invalidation works."""
        from pmacs.nervous.auth import SessionManager

        mgr = SessionManager()
        info = mgr.create_session()
        mgr.invalidate_session(info.token)
        assert not mgr.verify_session(info.token)

    def test_verify_write_access_valid_session(self) -> None:
        """Write access succeeds with a valid session (no second-factor gate)."""
        from pmacs.nervous.auth import SessionManager

        mgr = SessionManager()
        info = mgr.create_session()
        assert mgr.verify_write_access(info.token)

    def test_verify_write_access_bad_session(self) -> None:
        """Write access with bad session fails."""
        from pmacs.nervous.auth import SessionManager

        mgr = SessionManager()
        assert not mgr.verify_write_access("bad_token")
