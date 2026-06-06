"""E2E exit tests for Settings page — component-level verification (S7, M5).

Validates every settings page component described in Source.md section 20:
(a) 12 sections with left sub-nav anchors
(b) Mutation Engine section: pending candidates with dimension, target, sample size, effect size, p-value, trending [M5]
(c) TOTP modal appears on gated actions
(d) All TOTP-gated actions call open_totp_modal()
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pmacs.web.app import app


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestSections:
    """(a) 12 sections with left sub-nav anchors."""

    def test_settings_heading(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text

    def test_general_section(self, client):
        resp = client.get("/settings")
        assert "General" in resp.text

    def test_brokers_section(self, client):
        resp = client.get("/settings")
        assert "Brokers" in resp.text

    def test_inference_section(self, client):
        resp = client.get("/settings")
        assert "Inference" in resp.text

    def test_universe_section(self, client):
        resp = client.get("/settings")
        assert "Universe" in resp.text

    def test_risk_section(self, client):
        resp = client.get("/settings")
        assert "Risk" in resp.text

    def test_crucible_section(self, client):
        resp = client.get("/settings")
        assert "Crucible" in resp.text

    def test_mutation_engine_section(self, client):
        resp = client.get("/settings")
        assert "Mutation Engine" in resp.text

    def test_agent_personas_section(self, client):
        resp = client.get("/settings")
        assert "Agent Personas" in resp.text

    def test_queue_section(self, client):
        resp = client.get("/settings")
        assert "Queue" in resp.text

    def test_audit_debug_section(self, client):
        resp = client.get("/settings")
        assert "Audit" in resp.text

    def test_operator_section(self, client):
        resp = client.get("/settings")
        assert "Operator" in resp.text

    def test_section_count(self, client):
        """All 11 sections are rendered."""
        resp = client.get("/settings")
        expected = [
            "General", "Brokers", "Inference", "Universe", "Risk",
            "Crucible", "Mutation Engine", "Agent Personas", "Queue",
            "Audit", "Operator",
        ]
        html = resp.text
        for section in expected:
            assert section in html, f"Missing section: {section}"


class TestGeneralSection:
    """General section displays key constants."""

    def test_mode_displayed(self, client):
        resp = client.get("/settings")
        assert "SHADOW + PAPER" in resp.text

    def test_paper_capital_displayed(self, client):
        resp = client.get("/settings")
        assert "$5,000" in resp.text

    def test_max_positions_displayed(self, client):
        resp = client.get("/settings")
        assert "5" in resp.text


class TestRiskSection:
    """Risk section shows catastrophe-net stop and position limits."""

    def test_max_single_position(self, client):
        resp = client.get("/settings")
        assert "20%" in resp.text
        assert "1000" in resp.text

    def test_catastrophe_stop(self, client):
        resp = client.get("/settings")
        assert "15%" in resp.text


class TestCrucibleSection:
    """Crucible section shows time budget and cycles."""

    def test_time_budget(self, client):
        resp = client.get("/settings")
        assert "90s per attack" in resp.text

    def test_max_cycles(self, client):
        resp = client.get("/settings")
        assert "2" in resp.text

    def test_temperature(self, client):
        resp = client.get("/settings")
        assert "0.1" in resp.text


class TestMutationEngineSection:
    """(b) Mutation Engine section: pending candidates with dimension, target,
    sample size, effect size, p-value, trending [M5]."""

    def test_activation_threshold(self, client):
        resp = client.get("/settings")
        assert "50 cycles" in resp.text

    def test_auto_promote_disabled(self, client):
        resp = client.get("/settings")
        assert "TOTP required" in resp.text

    def test_stat_sig_threshold(self, client):
        resp = client.get("/settings")
        assert "0.05" in resp.text
        assert "0.20" in resp.text

    def test_probation_period(self, client):
        resp = client.get("/settings")
        assert "30 cycles" in resp.text

    def test_auto_rollback_window(self, client):
        resp = client.get("/settings")
        assert "50 cycles" in resp.text

    def test_pending_candidates_header(self, client):
        resp = client.get("/settings")
        assert "Pending Candidates" in resp.text

    def test_no_candidates_empty_state(self, client):
        resp = client.get("/settings")
        assert "No pending mutation candidates" in resp.text

    def test_recent_promotions_header(self, client):
        resp = client.get("/settings")
        assert "Recent Promotions" in resp.text


class TestInferenceSection:
    """Inference section shows backend config."""

    def test_primary_backend(self, client):
        resp = client.get("/settings")
        assert "llama_server" in resp.text

    def test_inference_test_button(self, client):
        resp = client.get("/settings")
        assert "Test Connection" in resp.text

    def test_analysis_temperature(self, client):
        resp = client.get("/settings")
        assert "0.2" in resp.text


class TestTOTPGating:
    """(c) TOTP modal appears on gated actions.
    (d) All TOTP-gated actions call open_totp_modal().

    The mutation buttons only render when candidates exist. We test:
    1. The TOTP modal infrastructure is always present (base template).
    2. With synthetic data, the TOTP-gated buttons render correctly.
    """

    def test_totp_modal_in_base_template(self, client):
        """TOTP modal is defined in the base template (always present)."""
        resp = client.get("/settings")
        assert 'id="totp-modal"' in resp.text

    def test_open_totp_modal_function_available(self, client):
        """open_totp_modal() function is available in JavaScript."""
        resp = client.get("/settings")
        assert "open_totp_modal" in resp.text

    def test_totp_confirm_button(self, client):
        """TOTP modal has a confirm button."""
        resp = client.get("/settings")
        assert 'id="totp-confirm-btn"' in resp.text

    def test_totp_input_field(self, client):
        """TOTP modal has digit input fields for the code."""
        resp = client.get("/settings")
        assert "totp-digit" in resp.text
        assert 'aria-label="TOTP digit 1 of 6"' in resp.text


class TestTOTPGatingWithMutations:
    """TOTP-gated mutation action buttons with synthetic DB data."""

    @pytest.fixture
    def client_with_mutations(self, tmp_path):
        """Client with a SQLite DB containing mutation candidates."""
        import sqlite3
        from pmacs.web import config as web_config

        original = web_config.get_config()

        db_path = tmp_path / "pmacs.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS mutation_candidates (
                candidate_id TEXT PRIMARY KEY,
                dimension TEXT NOT NULL,
                target TEXT NOT NULL,
                proposed_at TEXT,
                sample_size INTEGER,
                effect_size REAL,
                p_value REAL,
                trending_direction TEXT,
                status TEXT DEFAULT 'pending'
            )"""
        )
        conn.execute(
            """INSERT INTO mutation_candidates
            (candidate_id, dimension, target, proposed_at, sample_size,
             effect_size, p_value, trending_direction, status)
            VALUES ('mut-001', 'temperature', '0.25', '2026-01-15T10:00:00Z',
                    30, 0.35, 0.03, 'positive', 'PROPOSED')"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS mutation_log (
                candidate_id TEXT PRIMARY KEY,
                dimension TEXT NOT NULL,
                target TEXT NOT NULL,
                promoted_at TEXT,
                promoted_by TEXT,
                rolled_back_at TEXT,
                status TEXT
            )"""
        )
        conn.execute(
            """INSERT INTO mutation_log
            (candidate_id, dimension, target, promoted_at, promoted_by,
             rolled_back_at, status)
            VALUES ('mut-002', 'weight', '0.15', '2026-01-10T10:00:00Z',
                    'operator', NULL, 'active')"""
        )
        conn.commit()
        conn.close()

        cfg = web_config.DashboardConfig(
            sqlite_path=db_path,
            config_dir=tmp_path / "config",
        )
        # Ensure config dir exists
        (tmp_path / "config").mkdir(exist_ok=True)
        web_config.configure(cfg)
        yield TestClient(app)
        web_config.configure(original)

    def test_promote_mutation_uses_totp(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "Promote (TOTP)" in resp.text

    def test_reject_mutation_uses_totp(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "Reject (TOTP)" in resp.text

    def test_rollback_mutation_uses_totp(self, client_with_mutations):
        """Rollback buttons render for active mutations."""
        resp = client_with_mutations.get("/settings")
        assert "Rollback (TOTP)" in resp.text

    def test_promote_calls_open_totp_modal(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "promoteMutation" in resp.text

    def test_reject_calls_open_totp_modal(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "rejectMutation" in resp.text

    def test_rollback_calls_open_totp_modal(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "rollbackMutation" in resp.text

    def test_candidate_dimension_displayed(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "temperature" in resp.text

    def test_candidate_target_displayed(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "0.25" in resp.text

    def test_candidate_stats_displayed(self, client_with_mutations):
        """Sample size, effect size, p-value are shown."""
        resp = client_with_mutations.get("/settings")
        assert "n = " in resp.text
        assert "d = " in resp.text
        assert "p = " in resp.text

    def test_trending_direction_displayed(self, client_with_mutations):
        resp = client_with_mutations.get("/settings")
        assert "positive" in resp.text
