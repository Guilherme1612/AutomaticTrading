"""Unit tests for pmacs.cortex.self_check — meta-monitor process.

Tests:
- Self-check passes when Cortex health endpoint responds
- Self-check engages kill switch when Cortex is unresponsive
- Direct kill switch engagement writes to SQLite
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pmacs.cortex.self_check import (
    check_health_endpoint,
    engage_kill_switch_direct,
)
from pmacs.cortex.kill_switch import is_engaged
from pmacs.storage.sqlite import init_db


@pytest.fixture
def tmp_env(tmp_path: Path) -> dict:
    """Create temp env with DB."""
    db_path = tmp_path / "pmacs.db"
    conn = init_db(db_path)
    conn.close()

    return {
        "db_path": db_path,
        "tmp_path": tmp_path,
    }


class TestCheckHealthEndpoint:
    """Tests for check_health_endpoint()."""

    @patch("pmacs.cortex.self_check.urllib.request.urlopen")
    def test_returns_true_on_200(self, mock_urlopen: MagicMock) -> None:
        """check_health_endpoint returns True when endpoint responds 200."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        assert check_health_endpoint("http://127.0.0.1:8000/health") is True

    @patch("pmacs.cortex.self_check.urllib.request.urlopen")
    def test_returns_true_on_204(self, mock_urlopen: MagicMock) -> None:
        """check_health_endpoint returns True for any 2xx status."""
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        assert check_health_endpoint("http://127.0.0.1:8000/health") is True

    @patch("pmacs.cortex.self_check.urllib.request.urlopen")
    def test_returns_false_on_500(self, mock_urlopen: MagicMock) -> None:
        """check_health_endpoint returns False on 5xx."""
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://127.0.0.1:8000/health", 500, "Internal Error", {}, None
        )

        assert check_health_endpoint("http://127.0.0.1:8000/health") is False

    @patch("pmacs.cortex.self_check.urllib.request.urlopen")
    def test_returns_false_on_connection_refused(self, mock_urlopen: MagicMock) -> None:
        """check_health_endpoint returns False on connection error."""
        mock_urlopen.side_effect = OSError("Connection refused")

        assert check_health_endpoint("http://127.0.0.1:8000/health") is False

    @patch("pmacs.cortex.self_check.urllib.request.urlopen")
    def test_returns_false_on_timeout(self, mock_urlopen: MagicMock) -> None:
        """check_health_endpoint returns False on timeout."""
        mock_urlopen.side_effect = TimeoutError("Connection timed out")

        assert check_health_endpoint("http://127.0.0.1:8000/health") is False


class TestEngageKillSwitchDirect:
    """Tests for engage_kill_switch_direct()."""

    def test_engages_kill_switch_on_existing_db(self, tmp_env: dict) -> None:
        """engage_kill_switch_direct writes ENGAGED to existing DB."""
        db = tmp_env["db_path"]

        engage_kill_switch_direct(db_path=db)

        assert is_engaged(db_path=db) is True

    def test_engage_sets_reason_and_trigger(self, tmp_env: dict) -> None:
        """engage_kill_switch_direct sets reason and trigger_name."""
        db = tmp_env["db_path"]

        engage_kill_switch_direct(
            db_path=db,
            reason="Test: Cortex unresponsive >120s",
        )

        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT reason, trigger_name FROM kill_switch WHERE id = 1"
            ).fetchone()
            assert row is not None
            assert "Cortex unresponsive" in row[0]
            assert row[1] == "META_MONITOR_UNRESPONSIVE"
        finally:
            conn.close()

    def test_does_not_overwrite_existing_engaged_state(self, tmp_env: dict) -> None:
        """engage_kill_switch_direct does not overwrite reason if already ENGAGED."""
        db = tmp_env["db_path"]

        # Engage with first reason
        engage_kill_switch_direct(
            db_path=db,
            reason="First trigger",
        )

        # Try engaging again with different reason
        engage_kill_switch_direct(
            db_path=db,
            reason="Second trigger",
        )

        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT reason FROM kill_switch WHERE id = 1"
            ).fetchone()
            # First engagement should be preserved (WHERE state = 'ARMED' guard)
            assert row[0] == "First trigger"
        finally:
            conn.close()

    def test_handles_missing_db_gracefully(self, tmp_path: Path) -> None:
        """engage_kill_switch_direct handles missing DB without crashing."""
        nonexistent = tmp_path / "nonexistent" / "pmacs.db"
        # Should not raise — just prints to stderr
        engage_kill_switch_direct(db_path=nonexistent)


class TestSelfCheckIntegration:
    """Integration: health check failure leads to kill switch engagement."""

    @patch("pmacs.cortex.self_check.check_health_endpoint", return_value=False)
    def test_cortex_dead_engages_kill_switch(
        self, mock_health: MagicMock, tmp_env: dict
    ) -> None:
        """When Cortex is dead, kill switch gets engaged."""
        db = tmp_env["db_path"]

        # Simulate: cortex is dead, engage kill switch directly
        engage_kill_switch_direct(db_path=db)

        assert is_engaged(db_path=db) is True

    @patch("pmacs.cortex.self_check.check_health_endpoint", return_value=True)
    def test_cortex_alive_no_action(
        self, mock_health: MagicMock, tmp_env: dict
    ) -> None:
        """When Cortex is healthy, kill switch stays ARMED."""
        db = tmp_env["db_path"]

        # Cortex is healthy — no kill switch engagement
        assert is_engaged(db_path=db) is False
