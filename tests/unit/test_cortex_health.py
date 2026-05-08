"""Unit tests for pmacs.cortex.health — heartbeat monitoring."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from pmacs.cortex.health import HeartbeatStatus, check_heartbeats, write_heartbeat


class TestWriteHeartbeat:
    """Tests for write_heartbeat()."""

    def test_creates_heartbeat_file(self, tmp_path: Path) -> None:
        """write_heartbeat creates a .ts file with current timestamp."""
        write_heartbeat("test-proc", heartbeat_dir=tmp_path)
        ts_file = tmp_path / "test-proc.ts"
        assert ts_file.exists()
        content = ts_file.read_text().strip()
        assert content.isdigit()

    def test_creates_directory(self, tmp_path: Path) -> None:
        """write_heartbeat creates heartbeat dir if missing."""
        hb_dir = tmp_path / "subdir" / "heartbeat"
        write_heartbeat("test-proc", heartbeat_dir=hb_dir)
        assert hb_dir.exists()

    def test_overwrites_previous(self, tmp_path: Path) -> None:
        """Second write overwrites first timestamp."""
        write_heartbeat("test-proc", heartbeat_dir=tmp_path)
        time.sleep(0.1)
        write_heartbeat("test-proc", heartbeat_dir=tmp_path)
        ts_file = tmp_path / "test-proc.ts"
        ts = float(ts_file.read_text())
        assert ts > time.time() - 2  # Within last 2s


class TestCheckHeartbeats:
    """Tests for check_heartbeats()."""

    def test_fresh_heartbeat_not_stale(self, tmp_path: Path) -> None:
        """Freshly written heartbeat is not stale."""
        write_heartbeat("proc-a", heartbeat_dir=tmp_path)
        results = check_heartbeats(["proc-a"], heartbeat_dir=tmp_path)
        assert len(results) == 1
        assert results[0].proc == "proc-a"
        assert results[0].is_stale is False
        assert results[0].last_ts is not None

    def test_missing_heartbeat_is_stale(self, tmp_path: Path) -> None:
        """Missing heartbeat file is reported as stale."""
        results = check_heartbeats(["nonexistent"], heartbeat_dir=tmp_path)
        assert len(results) == 1
        assert results[0].is_stale is True
        assert results[0].last_ts is None

    def test_old_heartbeat_is_stale(self, tmp_path: Path) -> None:
        """Old timestamp is reported as stale."""
        ts_file = tmp_path / "old-proc.ts"
        old_ts = int(time.time()) - 100  # 100s ago
        ts_file.write_text(str(old_ts))
        results = check_heartbeats(["old-proc"], heartbeat_dir=tmp_path, stale_threshold=30.0)
        assert results[0].is_stale is True

    def test_multiple_processes(self, tmp_path: Path) -> None:
        """Check multiple processes at once."""
        write_heartbeat("alive", heartbeat_dir=tmp_path)
        # "dead" has no heartbeat file
        results = check_heartbeats(["alive", "dead"], heartbeat_dir=tmp_path)
        assert len(results) == 2
        assert results[0].is_stale is False
        assert results[1].is_stale is True

    def test_custom_stale_threshold(self, tmp_path: Path) -> None:
        """Custom stale_threshold affects freshness."""
        ts_file = tmp_path / "proc.ts"
        ts_file.write_text(str(int(time.time()) - 10))
        # 10s old, threshold 5s → stale
        results = check_heartbeats(["proc"], heartbeat_dir=tmp_path, stale_threshold=5.0)
        assert results[0].is_stale is True
        # 10s old, threshold 20s → fresh
        results = check_heartbeats(["proc"], heartbeat_dir=tmp_path, stale_threshold=20.0)
        assert results[0].is_stale is False

    def test_corrupt_heartbeat_is_stale(self, tmp_path: Path) -> None:
        """Non-numeric content in heartbeat file is treated as stale."""
        ts_file = tmp_path / "bad-proc.ts"
        ts_file.write_text("not-a-number")
        results = check_heartbeats(["bad-proc"], heartbeat_dir=tmp_path)
        assert results[0].is_stale is True
        assert results[0].last_ts is None
