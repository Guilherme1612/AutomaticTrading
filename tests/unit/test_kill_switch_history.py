"""Unit tests for get_kill_switch_history() — Source.md §18.6."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pmacs.web.data import get_kill_switch_history


def _write_audit(path: Path, entries: list[dict]) -> None:
    """Write JSON-lines audit entries to a file."""
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def test_returns_empty_for_missing_file(tmp_path: Path) -> None:
    result = get_kill_switch_history(tmp_path / "nonexistent.log")
    assert result == []


def test_returns_kill_switch_engaged_events(tmp_path: Path) -> None:
    audit = tmp_path / "audit.log"
    _write_audit(audit, [
        {"event": "kill_switch_engaged", "ts": "2026-05-15T10:00:00Z", "payload": {"reason": "Daily loss exceeded"}},
        {"event": "other_event", "ts": "2026-05-15T09:00:00Z"},
        {"event": "kill_switch_engaged", "ts": "2026-05-14T14:00:00Z", "payload": {"reason": "Operator triggered"}},
    ])
    result = get_kill_switch_history(audit)
    assert len(result) == 2
    assert result[0]["reason"] == "Operator triggered"  # most recent first
    assert result[0]["trigger_type"] == "manual"
    assert result[1]["reason"] == "Daily loss exceeded"


def test_returns_auto_demotion_events(tmp_path: Path) -> None:
    audit = tmp_path / "audit.log"
    _write_audit(audit, [
        {"event": "mode_changed", "ts": "2026-05-15T12:00:00Z",
         "payload": {"triggered_by": "AUTO_DEMOTION", "reason": "Sharpe < 0"}},
        {"event": "mode_changed", "ts": "2026-05-15T11:00:00Z",
         "payload": {"triggered_by": "OPERATOR", "reason": "Manual promote"}},
    ])
    result = get_kill_switch_history(audit)
    assert len(result) == 1
    assert result[0]["trigger_type"] == "auto_demotion"
    assert result[0]["reason"] == "Sharpe < 0"


def test_respects_limit(tmp_path: Path) -> None:
    audit = tmp_path / "audit.log"
    entries = [
        {"event": "kill_switch_engaged", "ts": f"2026-05-{15-i:02d}T10:00:00Z", "payload": {"reason": f"Reason {i}"}}
        for i in range(15)
    ]
    _write_audit(audit, entries)
    result = get_kill_switch_history(audit, limit=5)
    assert len(result) == 5


def test_ignores_malformed_json(tmp_path: Path) -> None:
    audit = tmp_path / "audit.log"
    with open(audit, "w") as f:
        f.write('{"event": "kill_switch_engaged", "ts": "2026-05-15T10:00:00Z", "payload": {"reason": "Valid"}}\n')
        f.write("NOT JSON\n")
        f.write('{"event": "kill_switch_engaged", "ts": "2026-05-15T09:00:00Z", "payload": {"reason": "Also valid"}}\n')
    result = get_kill_switch_history(audit)
    assert len(result) == 2


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    audit = tmp_path / "audit.log"
    audit.touch()
    result = get_kill_switch_history(audit)
    assert result == []


def test_no_matching_events_returns_empty(tmp_path: Path) -> None:
    audit = tmp_path / "audit.log"
    _write_audit(audit, [
        {"event": "cycle_complete", "ts": "2026-05-15T10:00:00Z"},
        {"event": "trade_filled", "ts": "2026-05-15T09:00:00Z"},
    ])
    result = get_kill_switch_history(audit)
    assert result == []
