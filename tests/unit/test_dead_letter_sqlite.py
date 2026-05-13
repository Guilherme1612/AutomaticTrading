"""Tests for SQLite-backed dead-letter queue."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from pmacs.storage.dead_letter import DeadLetterStore, MAX_ATTEMPTS
from pmacs.storage.sqlite import SCHEMA_SQL


@pytest.fixture
def db_conn():
    """Create an in-memory SQLite database with the dead_letter table."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def store(db_conn):
    """Create a DeadLetterStore backed by the test database."""
    return DeadLetterStore(db_conn, backoff_schedule=[0, 0, 0, 0, 0, 0])


class TestEnqueue:
    def test_enqueue_persists_to_sqlite(self, store: DeadLetterStore, db_conn):
        entry = store.enqueue("qdrant_upsert", "qdrant", {"collection": "theses", "id": "abc"})
        assert entry.id is not None
        assert entry.op_type == "qdrant_upsert"
        assert entry.target_db == "qdrant"
        assert entry.status == "PENDING"
        assert entry.retry_count == 0

        # Verify persisted
        row = db_conn.execute("SELECT * FROM dead_letter WHERE id = ?", (entry.id,)).fetchone()
        assert row is not None
        assert row[1] == "qdrant_upsert"  # op_type
        assert row[2] == "qdrant"  # target_db

    def test_enqueue_multiple(self, store: DeadLetterStore):
        e1 = store.enqueue("qdrant_upsert", "qdrant", {"a": 1})
        e2 = store.enqueue("kuzu_execute", "kuzudb", {"b": 2})
        assert e1.id != e2.id
        assert store.total_count == 2
        assert store.pending_count == 2


class TestProcessNext:
    def test_process_next_returns_pending(self, store: DeadLetterStore):
        store.enqueue("qdrant_upsert", "qdrant", {"a": 1})
        entry = store.process_next()
        assert entry is not None
        assert entry.op_type == "qdrant_upsert"
        assert entry.status == "PENDING"

    def test_process_next_returns_none_when_empty(self, store: DeadLetterStore):
        entry = store.process_next()
        assert entry is None

    def test_process_next_returns_oldest_first(self, store: DeadLetterStore):
        store.enqueue("op_a", "db_a", {"seq": 1})
        store.enqueue("op_b", "db_b", {"seq": 2})
        entry = store.process_next()
        assert entry is not None
        assert entry.op_type == "op_a"


class TestRetryCount:
    def test_retry_count_increments_on_failure(self, store: DeadLetterStore, db_conn):
        entry = store.enqueue("qdrant_upsert", "qdrant", {"a": 1})
        assert entry.retry_count == 0

        store.mark_failed(entry.id, "connection refused")
        row = db_conn.execute(
            "SELECT retry_count, status FROM dead_letter WHERE id = ?", (entry.id,)
        ).fetchone()
        assert row[0] == 1
        assert row[1] == "RETRYING"

        store.mark_failed(entry.id, "still failing")
        row = db_conn.execute(
            "SELECT retry_count, status FROM dead_letter WHERE id = ?", (entry.id,)
        ).fetchone()
        assert row[0] == 2
        assert row[1] == "RETRYING"

    def test_status_becomes_failed_after_max_attempts(self, store: DeadLetterStore, db_conn):
        entry = store.enqueue("qdrant_upsert", "qdrant", {"a": 1})

        for i in range(MAX_ATTEMPTS - 1):
            store.mark_failed(entry.id, f"attempt {i+1}")

        row = db_conn.execute(
            "SELECT retry_count, status FROM dead_letter WHERE id = ?", (entry.id,)
        ).fetchone()
        assert row[0] == MAX_ATTEMPTS - 1
        assert row[1] == "RETRYING"

        # Final attempt triggers FAILED
        store.mark_failed(entry.id, "exhausted")
        row = db_conn.execute(
            "SELECT retry_count, status FROM dead_letter WHERE id = ?", (entry.id,)
        ).fetchone()
        assert row[0] == MAX_ATTEMPTS
        assert row[1] == "FAILED"

    def test_exhausted_entry_not_returned_by_process_next(self, store: DeadLetterStore):
        entry = store.enqueue("qdrant_upsert", "qdrant", {"a": 1})
        for _ in range(MAX_ATTEMPTS):
            store.mark_failed(entry.id, "fail")
        assert store.process_next() is None
        assert store.failed_count == 1


class TestMarkResolved:
    def test_mark_resolved(self, store: DeadLetterStore, db_conn):
        entry = store.enqueue("qdrant_upsert", "qdrant", {"a": 1})
        store.mark_resolved(entry.id)
        row = db_conn.execute(
            "SELECT status FROM dead_letter WHERE id = ?", (entry.id,)
        ).fetchone()
        assert row[0] == "RESOLVED"

    def test_resolved_entry_not_returned_by_process_next(self, store: DeadLetterStore):
        entry = store.enqueue("qdrant_upsert", "qdrant", {"a": 1})
        store.mark_resolved(entry.id)
        assert store.process_next() is None
        assert store.pending_count == 0
