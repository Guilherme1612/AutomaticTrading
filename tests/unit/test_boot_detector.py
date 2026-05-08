"""Unit tests for pmacs.cortex.boot_detector — boot cycle detection."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pmacs.cortex.boot_detector import maybe_initiate_cycle
from pmacs.storage.sqlite import init_db


# A known weekday (Wednesday May 6, 2026) at 22:00 UTC = 17:00 ET (after EOD)
WEEKDAY_AFTER_EOD = datetime(2026, 5, 6, 22, 0, 0, tzinfo=timezone.utc)
# A known Saturday
SATURDAY = datetime(2026, 5, 9, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Create temp SQLite database with full schema."""
    db_path = tmp_path / "pmacs.db"
    conn = init_db(db_path)
    conn.close()
    return db_path


def _mock_datetime(target_now: datetime):
    """Create a mock for datetime that returns target_now for .now().

    Preserves all other datetime functionality (fromisoformat, etc).
    """
    _real = datetime

    class MockDatetime(_real):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return target_now.astimezone(tz)
            return target_now.replace(tzinfo=None)

    return MockDatetime


class TestMaybeInitiateCycle:
    """Tests for maybe_initiate_cycle()."""

    def test_first_run_initiates_cycle(self, tmp_path: Path) -> None:
        """No database → first run, returns cycle_id."""
        with patch("pmacs.cortex.boot_detector.datetime", _mock_datetime(WEEKDAY_AFTER_EOD)):
            result = maybe_initiate_cycle(db_path=tmp_path / "nonexistent.db")
        assert result is not None
        assert len(result) > 0  # UUID string

    def test_no_closed_cycles_initiates(self, tmp_db: Path) -> None:
        """Database exists but no closed cycles → initiate."""
        with patch("pmacs.cortex.boot_detector.datetime", _mock_datetime(WEEKDAY_AFTER_EOD)):
            result = maybe_initiate_cycle(db_path=tmp_db)
        assert result is not None

    def test_recent_cycle_skips(self, tmp_db: Path) -> None:
        """Cycle closed <24h ago → skip."""
        # Use mock time as "now", insert a cycle closed 2h ago relative to that
        mock_now = WEEKDAY_AFTER_EOD
        two_hours_ago = (mock_now - timedelta(hours=2)).isoformat()
        now_iso = mock_now.isoformat()

        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode)
               VALUES (?, ?, ?, 'CLOSED', 'BOOT', 'PAPER')""",
            ("test-cycle", two_hours_ago, now_iso),
        )
        conn.commit()
        conn.close()

        with patch("pmacs.cortex.boot_detector.datetime", _mock_datetime(mock_now)):
            result = maybe_initiate_cycle(db_path=tmp_db)
        assert result is None

    def test_old_cycle_initiates(self, tmp_db: Path) -> None:
        """Cycle closed >24h ago and after EOD → initiate."""
        mock_now = WEEKDAY_AFTER_EOD
        old_time = (mock_now - timedelta(hours=48)).isoformat()

        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode)
               VALUES (?, ?, ?, 'CLOSED', 'BOOT', 'PAPER')""",
            ("old-cycle", old_time, old_time),
        )
        conn.commit()
        conn.close()

        with patch("pmacs.cortex.boot_detector.datetime", _mock_datetime(mock_now)):
            result = maybe_initiate_cycle(db_path=tmp_db)
        assert result is not None

    def test_weekend_skips(self, tmp_db: Path) -> None:
        """Weekend → skip regardless of gap."""
        mock_now = SATURDAY  # Saturday
        old_time = (mock_now - timedelta(hours=100)).isoformat()

        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode)
               VALUES (?, ?, ?, 'CLOSED', 'BOOT', 'PAPER')""",
            ("old-cycle", old_time, old_time),
        )
        conn.commit()
        conn.close()

        with patch("pmacs.cortex.boot_detector.datetime", _mock_datetime(mock_now)):
            result = maybe_initiate_cycle(db_path=tmp_db)
        assert result is None

    def test_before_eod_skips(self, tmp_db: Path) -> None:
        """Before EOD data time (16:30 ET) → skip."""
        # 15:00 ET = 20:00 UTC (before 16:30 ET)
        mock_now = datetime(2026, 5, 6, 20, 0, 0, tzinfo=timezone.utc)  # Wed 15:00 ET
        old_time = (mock_now - timedelta(hours=48)).isoformat()

        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            """INSERT INTO cycles (cycle_id, opened_at, closed_at, state, trigger, mode)
               VALUES (?, ?, ?, 'CLOSED', 'BOOT', 'PAPER')""",
            ("old-cycle", old_time, old_time),
        )
        conn.commit()
        conn.close()

        with patch("pmacs.cortex.boot_detector.datetime", _mock_datetime(mock_now)):
            result = maybe_initiate_cycle(db_path=tmp_db)
        assert result is None
