"""Unit tests for pmacs.cortex.crash_loop_detector — crash loop detection."""
from __future__ import annotations

from pathlib import Path

import pytest

from pmacs.cortex.crash_loop_detector import (
    check_any_crash_loop,
    check_crash_loop,
    clear_crash_loop_mark,
    record_restart,
)
from pmacs.storage.sqlite import init_db


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create temp SQLite database with full schema."""
    db_path = tmp_path / "pmacs.db"
    conn = init_db(db_path)
    conn.close()
    return db_path


class TestRecordRestart:
    """Tests for record_restart()."""

    def test_creates_record(self, tmp_db: Path) -> None:
        """record_restart inserts a row."""
        record_restart("test-proc", db_path=tmp_db)
        import sqlite3

        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM process_restarts WHERE proc = 'test-proc'"
            ).fetchone()
            assert row[0] == 1
        finally:
            conn.close()

    def test_updates_process_state(self, tmp_db: Path) -> None:
        """record_restart also updates process_state table."""
        record_restart("test-proc", db_path=tmp_db)
        import sqlite3

        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT restart_count_60s FROM process_state WHERE proc = 'test-proc'"
            ).fetchone()
            assert row is not None
            assert row[0] >= 1
        finally:
            conn.close()


class TestCheckCrashLoop:
    """Tests for check_crash_loop()."""

    def test_no_crash_loop_when_few_restarts(self, tmp_db: Path) -> None:
        """Less than max_restarts is not a crash loop."""
        for _ in range(3):
            record_restart("test-proc", db_path=tmp_db)
        assert check_crash_loop("test-proc", db_path=tmp_db, max_restarts=5) is False

    def test_detects_crash_loop(self, tmp_db: Path) -> None:
        """5+ restarts within window is a crash loop."""
        for _ in range(5):
            record_restart("test-proc", db_path=tmp_db)
        assert check_crash_loop("test-proc", db_path=tmp_db, max_restarts=5) is True

    def test_marks_broken_on_detection(self, tmp_db: Path) -> None:
        """Detection marks process as BROKEN_CRASH_LOOP."""
        for _ in range(5):
            record_restart("test-proc", db_path=tmp_db)
        check_crash_loop("test-proc", db_path=tmp_db, max_restarts=5)
        import sqlite3

        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT is_broken_crash_loop FROM process_state WHERE proc = 'test-proc'"
            ).fetchone()
            assert row is not None
            assert row[0] == 1
        finally:
            conn.close()


class TestCheckAnyCrashLoop:
    """Tests for check_any_crash_loop()."""

    def test_returns_none_when_healthy(self, tmp_db: Path) -> None:
        """No crash loops returns None."""
        assert check_any_crash_loop(db_path=tmp_db) is None

    def test_returns_proc_on_crash_loop(self, tmp_db: Path) -> None:
        """Returns first crash-looping process name."""
        for _ in range(5):
            record_restart("bad-proc", db_path=tmp_db)
        result = check_any_crash_loop(db_path=tmp_db)
        assert result == "bad-proc"


class TestClearCrashLoopMark:
    """Tests for clear_crash_loop_mark()."""

    def test_clears_mark(self, tmp_db: Path) -> None:
        """clear_crash_loop_mark resets the BROKEN flag."""
        for _ in range(5):
            record_restart("test-proc", db_path=tmp_db)
        check_crash_loop("test-proc", db_path=tmp_db, max_restarts=5)

        clear_crash_loop_mark("test-proc", db_path=tmp_db)
        import sqlite3

        conn = sqlite3.connect(str(tmp_db))
        try:
            row = conn.execute(
                "SELECT is_broken_crash_loop FROM process_state WHERE proc = 'test-proc'"
            ).fetchone()
            assert row is not None
            assert row[0] == 0
        finally:
            conn.close()
