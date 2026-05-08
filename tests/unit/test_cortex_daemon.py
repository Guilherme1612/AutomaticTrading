"""Unit tests for pmacs.cortex.daemon — Cortex main process loop.

Tests daemon startup, heartbeat writing, stale process detection,
kill switch trigger on audit chain failure, and disk space monitoring.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.cortex.daemon import (
    ALL_PROCESSES,
    DaemonConfig,
    MONITORED_PROCESSES,
    _startup_check,
    _check_triggers_and_engage,
)
from pmacs.cortex.kill_switch import engage, is_engaged
from pmacs.cortex.health import write_heartbeat
from pmacs.storage.sqlite import init_db


@pytest.fixture
def tmp_env(tmp_path: Path) -> dict:
    """Create temp env with DB, audit log, and heartbeat dir."""
    db_path = tmp_path / "pmacs.db"
    audit_path = tmp_path / "audit.log"
    heartbeat_dir = tmp_path / "heartbeat"
    heartbeat_dir.mkdir()

    conn = init_db(db_path)
    conn.close()

    return {
        "db_path": db_path,
        "audit_path": audit_path,
        "heartbeat_dir": heartbeat_dir,
    }


@pytest.fixture
def daemon_config(tmp_env: dict) -> DaemonConfig:
    """Create a DaemonConfig pointing to temp paths.

    Also writes a fresh heartbeat for cortex-self-check so the
    META_MONITOR_UNRESPONSIVE trigger does not fire spuriously.
    """
    config = DaemonConfig(
        heartbeat_interval=1,
        health_check_interval=2,
        audit_check_interval=5,
        startup_grace_period=30,
        db_path=str(tmp_env["db_path"]),
        audit_path=str(tmp_env["audit_path"]),
        heartbeat_dir=str(tmp_env["heartbeat_dir"]),
    )

    # Write fresh heartbeat for cortex-self-check to prevent
    # META_MONITOR_UNRESPONSIVE from firing on clean tests.
    write_heartbeat("cortex-self-check", heartbeat_dir=Path(config.heartbeat_dir))

    return config


class TestDaemonConfig:
    """Tests for DaemonConfig dataclass."""

    def test_default_config(self) -> None:
        """DaemonConfig has sensible defaults."""
        config = DaemonConfig()
        assert config.heartbeat_interval == 5
        assert config.health_check_interval == 10
        assert config.audit_check_interval == 60
        assert config.startup_grace_period == 30

    def test_custom_config(self, tmp_env: dict) -> None:
        """DaemonConfig accepts custom values."""
        config = DaemonConfig(
            heartbeat_interval=1,
            db_path=str(tmp_env["db_path"]),
        )
        assert config.heartbeat_interval == 1


class TestDaemonStartsAndWritesHeartbeat:
    """Tests for heartbeat writing on daemon startup."""

    def test_startup_check_creates_no_errors_when_healthy(
        self, daemon_config: DaemonConfig, tmp_env: dict
    ) -> None:
        """startup_check runs without error when processes have fresh heartbeats."""
        # Write fresh heartbeats for all monitored processes
        for proc in MONITORED_PROCESSES:
            write_heartbeat(proc, heartbeat_dir=Path(daemon_config.heartbeat_dir))

        # Should not raise
        _startup_check(daemon_config)

    def test_startup_check_detects_stale_processes(
        self, daemon_config: DaemonConfig, tmp_env: dict
    ) -> None:
        """startup_check detects processes with no heartbeat."""
        # No heartbeats written — all should be stale
        _startup_check(daemon_config)
        # Function just logs, no exception expected

    def test_all_processes_defined(self) -> None:
        """ALL_PROCESSES contains the 8 expected processes."""
        assert len(ALL_PROCESSES) == 8
        assert "pmacs-cortex" in ALL_PROCESSES
        assert "pmacs-inference" in ALL_PROCESSES
        assert "pmacs-nervous" in ALL_PROCESSES
        assert "pmacs-execution" in ALL_PROCESSES
        assert "pmacs-stoploss" in ALL_PROCESSES
        assert "pmacs-mutation" in ALL_PROCESSES
        assert "pmacs-dashboard" in ALL_PROCESSES
        assert "pmacs-cortex-self-check" in ALL_PROCESSES

    def test_monitored_excludes_cortex(self) -> None:
        """MONITORED_PROCESSES excludes pmacs-cortex (it monitors itself)."""
        assert "pmacs-cortex" not in MONITORED_PROCESSES
        assert len(MONITORED_PROCESSES) == 7


class TestDaemonDetectsStaleProcess:
    """Tests for stale process detection."""

    def test_stale_heartbeat_detected(
        self, daemon_config: DaemonConfig, tmp_env: dict
    ) -> None:
        """Daemon detects a stale heartbeat via check_heartbeats."""
        from pmacs.cortex.health import check_heartbeats

        hb_dir = Path(daemon_config.heartbeat_dir)

        # Write a fresh heartbeat for one process
        write_heartbeat("pmacs-inference", heartbeat_dir=hb_dir)

        # Don't write for pmacs-nervous — should be stale
        statuses = check_heartbeats(
            ["pmacs-inference", "pmacs-nervous"],
            heartbeat_dir=hb_dir,
        )

        assert len(statuses) == 2
        assert statuses[0].is_stale is False  # pmacs-inference has fresh heartbeat
        assert statuses[1].is_stale is True  # pmacs-nervous has no heartbeat


class TestDaemonTriggersKillSwitchOnAuditChainFailure:
    """Tests for kill switch trigger when audit chain is broken."""

    def test_broken_audit_chain_triggers_kill_switch(
        self, daemon_config: DaemonConfig, tmp_env: dict
    ) -> None:
        """A tampered audit log triggers kill switch via _check_triggers_and_engage."""
        audit = tmp_env["audit_path"]

        # Write a valid audit entry
        from pmacs.storage.audit import AuditWriter

        writer = AuditWriter(audit)
        writer.append("TEST_EVENT", {"key": "value"})
        writer.close()

        # Tamper with it — break hash chain
        content = audit.read_text()
        tampered = content.replace("TEST_EVENT", "TAMPERED") + "GARBAGE\n"
        audit.write_text(tampered)

        # Run trigger check
        _check_triggers_and_engage(daemon_config)

        # Kill switch should be engaged
        assert is_engaged(db_path=tmp_env["db_path"]) is True

    def test_clean_audit_does_not_trigger(
        self, daemon_config: DaemonConfig, tmp_env: dict
    ) -> None:
        """A clean audit log does NOT trigger kill switch."""
        # Empty audit log is valid (genesis state)
        _check_triggers_and_engage(daemon_config)
        assert is_engaged(db_path=tmp_env["db_path"]) is False


class TestDaemonChecksDiskSpace:
    """Tests for disk space monitoring."""

    @patch("pmacs.cortex.kill_switch._check_disk_space")
    def test_low_disk_triggers_kill_switch(
        self, mock_disk: MagicMock,
        daemon_config: DaemonConfig,
        tmp_env: dict,
    ) -> None:
        """Low disk space triggers kill switch."""
        from pmacs.cortex.kill_switch import TriggerResult

        mock_disk.return_value = TriggerResult(
            trigger_id="DISK_SPACE_LOW",
            triggered=True,
            reason="Free space: 0.50GB",
            details={"free_gb": 0.50},
        )

        _check_triggers_and_engage(daemon_config)
        assert is_engaged(db_path=tmp_env["db_path"]) is True

    @patch("pmacs.cortex.kill_switch._check_disk_space")
    def test_sufficient_disk_does_not_trigger(
        self, mock_disk: MagicMock,
        daemon_config: DaemonConfig,
        tmp_env: dict,
    ) -> None:
        """Sufficient disk space does NOT trigger kill switch."""
        from pmacs.cortex.kill_switch import TriggerResult

        mock_disk.return_value = TriggerResult(
            trigger_id="DISK_SPACE_LOW",
            triggered=False,
            reason="Free space: 50.00GB",
            details={"free_gb": 50.00},
        )

        _check_triggers_and_engage(daemon_config)
        assert is_engaged(db_path=tmp_env["db_path"]) is False
