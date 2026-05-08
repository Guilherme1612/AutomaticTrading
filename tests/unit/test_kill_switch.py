"""Unit tests for pmacs.cortex.kill_switch — kill switch state machine."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from pmacs.cortex.kill_switch import (
    KillSwitchState,
    TriggerResult,
    engage,
    disengage,
    get_engagement_info,
    get_state,
    is_engaged,
    check_all_triggers,
    TRIGGER_IDS,
)
from pmacs.cortex.totp import compute_totp, generate_totp_secret
from pmacs.storage.sqlite import init_db


@pytest.fixture
def tmp_env(tmp_path: Path) -> dict[str, Path]:
    """Create temp directory with SQLite DB and audit log."""
    db_path = tmp_path / "pmacs.db"
    audit_path = tmp_path / "audit.log"
    heartbeat_dir = tmp_path / "heartbeat"
    heartbeat_dir.mkdir()

    # Initialize DB with full schema
    conn = init_db(db_path)
    conn.close()

    return {
        "db_path": db_path,
        "audit_path": audit_path,
        "heartbeat_dir": heartbeat_dir,
    }


@pytest.fixture
def totp_secret() -> str:
    """Generate a fresh TOTP secret for testing."""
    return generate_totp_secret()


class TestEngage:
    """Tests for engage()."""

    def test_engage_sets_state_to_engaged(self, tmp_env: dict[str, Path]) -> None:
        """engage() sets state to ENGAGED."""
        engage("test reason", "AUDIT_CHAIN_INTEGRITY", db_path=tmp_env["db_path"])
        assert is_engaged(db_path=tmp_env["db_path"]) is True

    def test_engage_is_idempotent(self, tmp_env: dict[str, Path]) -> None:
        """Engaging when already ENGAGED does not error."""
        engage("first", "AUDIT_CHAIN_INTEGRITY", db_path=tmp_env["db_path"])
        engage("second", "DISK_SPACE_LOW", db_path=tmp_env["db_path"])
        assert is_engaged(db_path=tmp_env["db_path"]) is True

    def test_engage_writes_audit(self, tmp_env: dict[str, Path]) -> None:
        """engage() writes to audit log when audit_path provided."""
        engage(
            "test reason",
            "AUDIT_CHAIN_INTEGRITY",
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
        )
        content = tmp_env["audit_path"].read_text()
        assert "KILL_SWITCH_ENGAGED" in content

    def test_engage_without_audit(self, tmp_env: dict[str, Path]) -> None:
        """engage() works without audit_path."""
        engage("test reason", "AUDIT_CHAIN_INTEGRITY", db_path=tmp_env["db_path"])
        assert is_engaged(db_path=tmp_env["db_path"]) is True

    def test_engage_persists_state(self, tmp_env: dict[str, Path]) -> None:
        """State persists after engage — readable via get_state."""
        engage("test reason", "DISK_SPACE_LOW", db_path=tmp_env["db_path"])
        state = get_state(db_path=tmp_env["db_path"])
        assert state == KillSwitchState.ENGAGED


class TestDisengage:
    """Tests for disengage()."""

    def test_disengage_with_valid_totp(self, tmp_env: dict[str, Path], totp_secret: str) -> None:
        """disengage() with valid TOTP sets state back to ARMED."""
        engage("test", "AUDIT_CHAIN_INTEGRITY", db_path=tmp_env["db_path"])
        assert is_engaged(db_path=tmp_env["db_path"]) is True

        code = compute_totp(totp_secret)
        result = disengage(
            totp_secret, code, "operator cleared", db_path=tmp_env["db_path"]
        )
        assert result is True
        assert is_engaged(db_path=tmp_env["db_path"]) is False

    def test_disengage_with_invalid_totp(self, tmp_env: dict[str, Path], totp_secret: str) -> None:
        """disengage() with invalid TOTP returns False, state stays ENGAGED."""
        engage("test", "AUDIT_CHAIN_INTEGRITY", db_path=tmp_env["db_path"])

        result = disengage(
            totp_secret, "000000", "bad attempt", db_path=tmp_env["db_path"]
        )
        assert result is False
        assert is_engaged(db_path=tmp_env["db_path"]) is True

    def test_disengage_when_not_engaged_raises(self, tmp_env: dict[str, Path], totp_secret: str) -> None:
        """disengage() when state is ARMED raises ValueError."""
        code = compute_totp(totp_secret)
        with pytest.raises(ValueError, match="not ENGAGED"):
            disengage(
                totp_secret, code, "no-op", db_path=tmp_env["db_path"]
            )

    def test_disengage_writes_audit(self, tmp_env: dict[str, Path], totp_secret: str) -> None:
        """disengage() writes to audit log when audit_path provided."""
        engage("test", "AUDIT_CHAIN_INTEGRITY", db_path=tmp_env["db_path"])

        code = compute_totp(totp_secret)
        disengage(
            totp_secret,
            code,
            "operator cleared",
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
        )
        content = tmp_env["audit_path"].read_text()
        assert "KILL_SWITCH_DISENGAGED" in content


class TestStatePersistence:
    """Tests for state persistence across instances."""

    def test_state_persists_across_connections(self, tmp_env: dict[str, Path]) -> None:
        """State persists across separate database connections."""
        db = tmp_env["db_path"]

        engage("test", "AUDIT_CHAIN_INTEGRITY", db_path=db)
        assert is_engaged(db_path=db) is True

        # is_engaged opens a new connection each time
        assert is_engaged(db_path=db) is True

    def test_engage_disengage_persists(self, tmp_env: dict[str, Path], totp_secret: str) -> None:
        """Full cycle: engage -> disengage -> check persists."""
        db = tmp_env["db_path"]

        engage("test", "DISK_SPACE_LOW", db_path=db)
        assert is_engaged(db_path=db) is True

        code = compute_totp(totp_secret)
        disengage(totp_secret, code, "cleared", db_path=db)
        assert is_engaged(db_path=db) is False

        # Re-read from fresh connection
        assert is_engaged(db_path=db) is False


class TestGetEngagementInfo:
    """Tests for get_engagement_info()."""

    def test_returns_none_when_armed(self, tmp_env: dict[str, Path]) -> None:
        """get_engagement_info() returns None when ARMED."""
        info = get_engagement_info(db_path=tmp_env["db_path"])
        assert info is None

    def test_returns_info_when_engaged(self, tmp_env: dict[str, Path]) -> None:
        """get_engagement_info() returns details when ENGAGED."""
        engage("test reason", "DISK_SPACE_LOW", db_path=tmp_env["db_path"])
        info = get_engagement_info(db_path=tmp_env["db_path"])
        assert info is not None
        assert info["reason"] == "test reason"
        assert info["trigger_name"] == "DISK_SPACE_LOW"
        assert info["engaged_at"] is not None


class TestCheckAllTriggers:
    """Tests for check_all_triggers()."""

    def test_returns_all_10_triggers(self, tmp_env: dict[str, Path]) -> None:
        """check_all_triggers() returns exactly 10 results."""
        results = check_all_triggers(
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
            heartbeat_dir=tmp_env["heartbeat_dir"],
        )
        assert len(results) == 10
        trigger_ids = [r.trigger_id for r in results]
        for tid in TRIGGER_IDS:
            assert tid in trigger_ids, f"Missing trigger: {tid}"

    def test_triggers_have_correct_structure(self, tmp_env: dict[str, Path]) -> None:
        """Each TriggerResult has required fields."""
        results = check_all_triggers(
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
        )
        for r in results:
            assert isinstance(r, TriggerResult)
            assert isinstance(r.trigger_id, str)
            assert isinstance(r.triggered, bool)
            assert isinstance(r.reason, str)

    def test_audit_chain_trigger_ok(self, tmp_env: dict[str, Path]) -> None:
        """AUDIT_CHAIN_INTEGRITY is not triggered on valid/empty audit."""
        results = check_all_triggers(
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
        )
        audit_result = [r for r in results if r.trigger_id == "AUDIT_CHAIN_INTEGRITY"][0]
        assert audit_result.triggered is False

    def test_audit_chain_trigger_on_broken_chain(self, tmp_env: dict[str, Path]) -> None:
        """AUDIT_CHAIN_INTEGRITY triggers on tampered audit."""
        # Write a valid entry first
        from pmacs.storage.audit import AuditWriter

        writer = AuditWriter(tmp_env["audit_path"])
        writer.append("TEST_EVENT", {"key": "value"})
        writer.close()

        # Tamper with it
        content = tmp_env["audit_path"].read_text()
        tampered = content.replace("TEST_EVENT", "TAMPERED_EVENT")
        # Also need to break the hash — just append garbage
        tmp_env["audit_path"].write_text(tampered + "GARBAGE_LINE\n")

        results = check_all_triggers(
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
        )
        audit_result = [r for r in results if r.trigger_id == "AUDIT_CHAIN_INTEGRITY"][0]
        assert audit_result.triggered is True

    def test_no_triggers_on_clean_system(self, tmp_env: dict[str, Path]) -> None:
        """On a clean system, no triggers fire (except possibly disk/NTP)."""
        results = check_all_triggers(
            db_path=tmp_env["db_path"],
            audit_path=tmp_env["audit_path"],
            heartbeat_dir=tmp_env["heartbeat_dir"],
        )
        # Most should not be triggered; disk and NTP depend on environment
        non_env_triggers = [
            r for r in results
            if r.trigger_id in (
                "AUDIT_CHAIN_INTEGRITY",
                "ROLLING_5D_LOSS",
                "SINGLE_DAY_MTM_LOSS",
                "RECONCILIATION_MISMATCH",
                "BROKER_AUTH_FAILURE",
                "CRASH_LOOP",
                "MODEL_INTEGRITY",
            )
        ]
        for r in non_env_triggers:
            assert r.triggered is False, f"Unexpected trigger: {r.trigger_id} — {r.reason}"


class TestKillSwitchStateEnum:
    """Tests for KillSwitchState enum."""

    def test_states(self) -> None:
        assert KillSwitchState.ARMED.value == "ARMED"
        assert KillSwitchState.ENGAGED.value == "ENGAGED"

    def test_string_comparison(self) -> None:
        assert KillSwitchState("ARMED") == KillSwitchState.ARMED
        assert KillSwitchState("ENGAGED") == KillSwitchState.ENGAGED


class TestIsEngagedOnMissingDb:
    """Tests for edge cases with missing/corrupt DB."""

    def test_is_engaged_creates_db(self, tmp_path: Path) -> None:
        """is_engaged() on nonexistent DB creates it and returns False."""
        db = tmp_path / "new.db"
        assert is_engaged(db_path=db) is False

    def test_engage_creates_db(self, tmp_path: Path) -> None:
        """engage() on nonexistent DB creates it."""
        db = tmp_path / "new.db"
        engage("test", "DISK_SPACE_LOW", db_path=db)
        assert is_engaged(db_path=db) is True
