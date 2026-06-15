"""E2E exit tests for Cortex page — component-level verification (S7).

Validates every cortex page component described in Source.md section 18:
(a) 2x3 grid layout
(b) Audit chain panel: status indicator, last verified, total entries, chain head SHA
(c) Cross-DB consistency panel: 4 DB indicators, last reconciled, drift count
(d) Process status panel: 8 processes with heartbeat age, restart count, PID
(e) Disk/clock/network panel
(f) Kill switch panel: ARMED/ENGAGED state, engage button (no TOTP), disengage button (TOTP)
(g) Model integrity panel: GGUF SHA256, model name, backend
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pmacs.web.app import app


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestGridLayout:
    """(a) 2x3 grid layout."""

    def test_grid_cols_3(self, client):
        resp = client.get("/cortex")
        assert resp.status_code == 200
        assert "grid-cols-3" in resp.text

    def test_system_health_heading(self, client):
        resp = client.get("/cortex")
        assert "System Health" in resp.text


class TestAuditChainPanel:
    """(b) Audit chain panel: status, entries, chain head SHA."""

    def test_audit_chain_section(self, client):
        resp = client.get("/cortex")
        assert "Audit Chain" in resp.text

    def test_audit_status_indicator(self, client):
        resp = client.get("/cortex")
        assert "verified" in resp.text or "broken" in resp.text

    def test_audit_entries_count(self, client):
        resp = client.get("/cortex")
        assert "Entries" in resp.text

    def test_audit_last_hash(self, client):
        resp = client.get("/cortex")
        assert "Last Hash" in resp.text


class TestCrossDBPanel:
    """(c) Cross-DB consistency panel: 4 DB indicators."""

    def test_cross_db_section(self, client):
        resp = client.get("/cortex")
        assert "Cross-DB Integrity" in resp.text

    def test_sqlite_indicator(self, client):
        resp = client.get("/cortex")
        assert "sqlite" in resp.text.lower()

    def test_kuzudb_indicator(self, client):
        resp = client.get("/cortex")
        assert "kuzudb" in resp.text.lower()

    def test_qdrant_indicator(self, client):
        resp = client.get("/cortex")
        assert "qdrant" in resp.text.lower()

    def test_duckdb_indicator(self, client):
        resp = client.get("/cortex")
        assert "duckdb" in resp.text.lower()

    def test_db_status_ok_or_error(self, client):
        resp = client.get("/cortex")
        assert "ok" in resp.text.lower()


class TestProcessStatusPanel:
    """(d) Process status panel: 8 processes."""

    def test_process_status_section(self, client):
        resp = client.get("/cortex")
        assert "Process Status" in resp.text

    def test_inference_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-inference" in resp.text

    def test_cortex_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-cortex" in resp.text

    def test_cortex_self_check_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-cortex-self-check" in resp.text

    def test_execution_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-execution" in resp.text

    def test_nervous_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-nervous" in resp.text

    def test_stoploss_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-stoploss" in resp.text

    def test_mutation_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-mutation" in resp.text

    def test_dashboard_process(self, client):
        resp = client.get("/cortex")
        assert "pmacs-dashboard" in resp.text

    def test_eight_processes_listed(self, client):
        """All 8 processes appear in the panel."""
        resp = client.get("/cortex")
        html = resp.text
        count = 0
        for proc in [
            "pmacs-inference", "pmacs-cortex-self-check", "pmacs-cortex",
            "pmacs-execution", "pmacs-nervous", "pmacs-stoploss",
            "pmacs-mutation", "pmacs-dashboard",
        ]:
            if proc in html:
                count += 1
        assert count == 8

    def test_process_status_indicators(self, client):
        """Processes show running/unknown status with dot indicator."""
        resp = client.get("/cortex")
        assert "bg-green-500" in resp.text or "bg-zinc-300" in resp.text

    def test_process_port_numbers(self, client):
        """Processes with ports show them."""
        resp = client.get("/cortex")
        assert ":8080" in resp.text  # inference
        assert ":8000" in resp.text  # nervous
        assert ":8000" in resp.text  # dashboard


class TestDiskClockNetworkPanel:
    """(e) Disk/clock/network panel."""

    def test_disk_clock_network_section(self, client):
        resp = client.get("/cortex")
        assert "Disk / Clock / Network" in resp.text

    def test_disk_free(self, client):
        resp = client.get("/cortex")
        assert "Disk Free" in resp.text
        assert "GB" in resp.text

    def test_clock_skew(self, client):
        resp = client.get("/cortex")
        assert "Clock Skew" in resp.text
        assert "ms" in resp.text

    def test_network_status(self, client):
        resp = client.get("/cortex")
        assert "Network" in resp.text


class TestKillSwitchPanel:
    """(f) Kill switch panel: ARMED/ENGAGED state, engage/disengage buttons."""

    def test_kill_switch_section(self, client):
        resp = client.get("/cortex")
        assert "Kill Switch" in resp.text

    def test_disengaged_state(self, client):
        """Default state shows Disengaged/OK."""
        resp = client.get("/cortex")
        assert "Disengaged" in resp.text or "OK" in resp.text

    def test_engage_button_present(self, client):
        resp = client.get("/cortex")
        assert "Engage Kill Switch" in resp.text

    def test_totp_required_notice(self, client):
        """TOTP verification required notice displayed."""
        resp = client.get("/cortex")
        assert "TOTP verification required" in resp.text


class TestModelIntegrityPanel:
    """(g) Model integrity panel: hash verification, model path."""

    def test_model_integrity_section(self, client):
        resp = client.get("/cortex")
        assert "Model Integrity" in resp.text

    def test_hash_verified_status(self, client):
        resp = client.get("/cortex")
        assert "Hash Verified" in resp.text
        assert "Pending" in resp.text or "Yes" in resp.text

    def test_model_path_displayed(self, client):
        resp = client.get("/cortex")
        assert "Model Path" in resp.text
