"""Tests for the cortex kill-switch route handlers — specifically the
``is_test`` flag that distinguishes §20.12 wiring-test events from real
auto-triggers (operator UX fix Jun 30, commit 36417ae).

Without the flag, the Settings UI test button was indistinguishable from a
real auto-trigger and fired the same critical-alert modal. These tests pin
that the flag propagates through both ``/api/cortex/kill-switch/engage``
and ``/api/cortex/kill-switch/disengage`` to the JSON response, AND is
carried on the underlying audit/debug events.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pmacs.storage.sqlite import init_db
from pmacs.web.app import app
from pmacs.web.config import DashboardConfig, configure, get_config


@pytest.fixture(autouse=True)
def _setup(tmp_path: Path):
    """Configure dashboard with temp DB + audit log + debug log."""
    db_path = tmp_path / "pmacs.db"
    conn = init_db(db_path)
    conn.close()

    hb_dir = tmp_path / "heartbeat"
    hb_dir.mkdir()
    (hb_dir / "dashboard.ts").write_text(str(int(time.time())))

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "risk.toml").write_text("[risk]\nmax_position_pct = 0.20\n")

    audit_path = tmp_path / "audit.log"

    debug_path = tmp_path / "debug.jsonl"
    debug_path.write_text("")

    cfg = DashboardConfig(
        sqlite_path=db_path,
        duckdb_path=tmp_path / "analytics.duckdb",
        heartbeat_dir=hb_dir,
        audit_path=audit_path,
        debug_log_path=debug_path,
        config_dir=config_dir,
    )
    _original = get_config()
    configure(cfg)
    yield
    configure(_original)


class TestKillSwitchIsTestFlag:
    """Pin that ``is_test`` flows through engage/disengage routes."""

    def test_engage_default_is_test_false(self):
        """POST without is_test defaults to False (operator convention)."""
        with TestClient(app) as client:
            response = client.post(
                "/api/cortex/kill-switch/engage",
                json={"reason": "Manual test from cortex page"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["state"] == "ENGAGED"
            assert body["is_test"] is False

    def test_engage_with_is_test_true_propagates(self):
        """POST with is_test=True surfaces on response (UX: suppress alert)."""
        with TestClient(app) as client:
            response = client.post(
                "/api/cortex/kill-switch/engage",
                json={"reason": "Wiring test from settings", "is_test": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["is_test"] is True

    def test_engage_is_test_carried_to_audit(self):
        """is_test flag is recorded on the audit log (compliance traceability)."""
        with TestClient(app) as client:
            response = client.post(
                "/api/cortex/kill-switch/engage",
                json={"reason": "Wiring test", "is_test": True},
            )
            assert response.status_code == 200

        # Audit log is TSV: iso_ts\tprev_sha\tevent_type\tcanonical_json\tsha
        audit_path: Path = get_config().audit_path
        engaged_payloads = []
        with open(audit_path) as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 4 and parts[2] == "KILL_SWITCH_ENGAGED":
                    engaged_payloads.append(json.loads(parts[3]))

        assert engaged_payloads, "no KILL_SWITCH_ENGAGED audit event written"
        assert engaged_payloads[-1]["is_test"] is True

    def test_disengage_default_is_test_false(self):
        """Disengage without is_test defaults to False."""
        with TestClient(app) as client:
            # Engage first
            client.post("/api/cortex/kill-switch/engage", json={"reason": "Setup"})
            # Disengage without flag
            response = client.post(
                "/api/cortex/kill-switch/disengage",
                json={"reason": "Operator override"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["state"] == "ARMED"
            assert body["is_test"] is False

    def test_disengage_with_is_test_true_propagates(self):
        """Disengage with is_test=True surfaces on response."""
        with TestClient(app) as client:
            client.post(
                "/api/cortex/kill-switch/engage",
                json={"reason": "Wiring test", "is_test": True},
            )
            response = client.post(
                "/api/cortex/kill-switch/disengage",
                json={"reason": "Wiring test cleanup", "is_test": True},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["is_test"] is True

    def test_engage_disengage_is_test_round_trip(self):
        """End-to-end: §20.12 wiring-test engages AND disengages with is_test=True."""
        with TestClient(app) as client:
            engage_resp = client.post(
                "/api/cortex/kill-switch/engage",
                json={"reason": "Settings wiring test", "is_test": True},
            )
            assert engage_resp.json()["is_test"] is True

            disengage_resp = client.post(
                "/api/cortex/kill-switch/disengage",
                json={"reason": "Settings wiring test cleanup", "is_test": True},
            )
            assert disengage_resp.json()["is_test"] is True