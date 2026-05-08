"""Integration tests for heartbeat monitoring (Architecture.md §4.6).

Tests write_heartbeat and check_heartbeats from pmacs.cortex.health
using temp directories. No launchd dependency.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from pmacs.cortex.health import (
    STALE_THRESHOLD_S,
    HeartbeatStatus,
    check_heartbeats,
    write_heartbeat,
)


class TestHeartbeatWrite:
    """Test heartbeat file creation."""

    def test_write_creates_directory_and_file(self, tmp_path: Path) -> None:
        hb_dir = tmp_path / "heartbeat"
        write_heartbeat("cortex", heartbeat_dir=hb_dir)

        assert hb_dir.is_dir()
        ts_file = hb_dir / "cortex.ts"
        assert ts_file.exists()

    def test_write_contains_timestamp(self, tmp_path: Path) -> None:
        before = int(time.time())
        write_heartbeat("inference", heartbeat_dir=tmp_path)
        after = int(time.time()) + 1

        ts = int((tmp_path / "inference.ts").read_text().strip())
        assert before <= ts <= after

    def test_write_overwrites_previous(self, tmp_path: Path) -> None:
        # Write an old timestamp manually, then overwrite with write_heartbeat
        (tmp_path / "cortex.ts").write_text(str(int(time.time() - 100)))
        ts1 = (tmp_path / "cortex.ts").read_text()

        write_heartbeat("cortex", heartbeat_dir=tmp_path)
        ts2 = (tmp_path / "cortex.ts").read_text()

        assert ts1 != ts2
        assert int(ts2) > int(ts1)

    def test_write_multiple_processes(self, tmp_path: Path) -> None:
        procs = ["cortex", "inference", "execution", "nervous"]
        for proc in procs:
            write_heartbeat(proc, heartbeat_dir=tmp_path)

        for proc in procs:
            assert (tmp_path / f"{proc}.ts").exists()


class TestHeartbeatCheck:
    """Test heartbeat freshness detection."""

    def test_fresh_heartbeat_not_stale(self, tmp_path: Path) -> None:
        write_heartbeat("cortex", heartbeat_dir=tmp_path)

        results = check_heartbeats(["cortex"], heartbeat_dir=tmp_path)
        assert len(results) == 1
        assert results[0].proc == "cortex"
        assert results[0].is_stale is False
        assert results[0].last_ts is not None

    def test_missing_heartbeat_is_stale(self, tmp_path: Path) -> None:
        results = check_heartbeats(["nonexistent"], heartbeat_dir=tmp_path)
        assert len(results) == 1
        assert results[0].proc == "nonexistent"
        assert results[0].is_stale is True
        assert results[0].last_ts is None

    def test_old_heartbeat_is_stale(self, tmp_path: Path) -> None:
        # Write a heartbeat with an old timestamp (60s ago)
        old_ts = str(int(time.time() - 60))
        (tmp_path / "cortex.ts").write_text(old_ts)

        results = check_heartbeats(
            ["cortex"],
            heartbeat_dir=tmp_path,
            stale_threshold=30.0,
        )
        assert results[0].is_stale is True
        assert results[0].last_ts == float(old_ts)

    def test_custom_stale_threshold(self, tmp_path: Path) -> None:
        # Write heartbeat 5s ago
        old_ts = str(int(time.time() - 5))
        (tmp_path / "cortex.ts").write_text(old_ts)

        # With 10s threshold: not stale
        results = check_heartbeats(
            ["cortex"],
            heartbeat_dir=tmp_path,
            stale_threshold=10.0,
        )
        assert results[0].is_stale is False

        # With 3s threshold: stale
        results = check_heartbeats(
            ["cortex"],
            heartbeat_dir=tmp_path,
            stale_threshold=3.0,
        )
        assert results[0].is_stale is True

    def test_mixed_freshness(self, tmp_path: Path) -> None:
        write_heartbeat("cortex", heartbeat_dir=tmp_path)
        write_heartbeat("inference", heartbeat_dir=tmp_path)
        # "execution" has no heartbeat

        results = check_heartbeats(
            ["cortex", "inference", "execution"],
            heartbeat_dir=tmp_path,
        )
        assert len(results) == 3

        by_proc = {r.proc: r for r in results}
        assert by_proc["cortex"].is_stale is False
        assert by_proc["inference"].is_stale is False
        assert by_proc["execution"].is_stale is True

    def test_corrupt_heartbeat_file_is_stale(self, tmp_path: Path) -> None:
        (tmp_path / "cortex.ts").write_text("not-a-number")

        results = check_heartbeats(["cortex"], heartbeat_dir=tmp_path)
        assert results[0].is_stale is True
        assert results[0].last_ts is None

    def test_empty_heartbeat_file_is_stale(self, tmp_path: Path) -> None:
        (tmp_path / "cortex.ts").write_text("")

        results = check_heartbeats(["cortex"], heartbeat_dir=tmp_path)
        assert results[0].is_stale is True
        assert results[0].last_ts is None

    def test_all_pmacs_processes(self, tmp_path: Path) -> None:
        """Check all 8 PMACS processes from the process topology."""
        all_procs = [
            "inference",
            "cortex",
            "cortex-self-check",
            "execution",
            "nervous",
            "stoploss",
            "mutation",
            "dashboard",
        ]

        # Write heartbeats for all
        for proc in all_procs:
            write_heartbeat(proc, heartbeat_dir=tmp_path)

        results = check_heartbeats(all_procs, heartbeat_dir=tmp_path)
        assert len(results) == 8
        assert all(r.is_stale is False for r in results)
        assert all(r.last_ts is not None for r in results)
