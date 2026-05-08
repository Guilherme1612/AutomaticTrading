"""Unit tests for pmacs.cortex.disk_monitor and clock_monitor."""
from __future__ import annotations

from pathlib import Path

import pytest


class TestDiskMonitor:
    """Tests for disk_monitor.check_disk_space()."""

    def test_returns_tuple(self) -> None:
        """check_disk_space returns (bool, float) tuple."""
        from pmacs.cortex.disk_monitor import check_disk_space

        triggered, free_gb = check_disk_space(path=Path("/tmp"))
        assert isinstance(triggered, bool)
        assert isinstance(free_gb, float)
        assert free_gb >= 0

    def test_normal_disk_not_triggered(self) -> None:
        """Normal disk space should not trigger."""
        from pmacs.cortex.disk_monitor import check_disk_space

        triggered, free_gb = check_disk_space(path=Path("/tmp"))
        # /tmp should have more than 2GB free
        if free_gb >= 2.0:
            assert triggered is False

    def test_high_threshold_triggers(self) -> None:
        """Unreasonably high threshold triggers."""
        from pmacs.cortex.disk_monitor import check_disk_space

        triggered, free_gb = check_disk_space(path=Path("/tmp"), min_free_gb=1_000_000.0)
        assert triggered is True


class TestClockMonitor:
    """Tests for clock_monitor.check_ntp_drift()."""

    def test_returns_tuple(self) -> None:
        """check_ntp_drift returns (bool, float|None) tuple."""
        from pmacs.cortex.clock_monitor import check_ntp_drift

        triggered, drift_s = check_ntp_drift()
        assert isinstance(triggered, bool)
        assert drift_s is None or isinstance(drift_s, float)

    def test_ntp_check_graceful_failure(self) -> None:
        """NTP check to unreachable host returns (False, None)."""
        from pmacs.cortex.clock_monitor import check_ntp_drift

        triggered, drift_s = check_ntp_drift(host="192.0.2.1")  # TEST-NET, should fail
        assert triggered is False
        assert drift_s is None
