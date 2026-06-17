"""E2E exit tests for Settings page — component-level verification (S7, M5).

Validates the redesigned settings page (Source.md §20). The page exposes:
Appearance, AI Provider, Budget, Notifications, Reset Progress, Mutation Diff,
and Keyboard Shortcuts sections. Operator-action authorization is session-token
based (TOTP removed — see CLAUDE.md non-negotiable #5).
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
    """Settings page renders its section headings."""

    def test_settings_heading(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text

    def test_appearance_section(self, client):
        assert "Appearance" in client.get("/settings").text

    def test_ai_provider_section(self, client):
        assert "AI Provider" in client.get("/settings").text

    def test_budget_section(self, client):
        assert "Budget" in client.get("/settings").text

    def test_notifications_section(self, client):
        assert "Notifications" in client.get("/settings").text

    def test_reset_progress_section(self, client):
        assert "Reset Progress" in client.get("/settings").text

    def test_mutation_diff_section(self, client):
        assert "Mutation Diff" in client.get("/settings").text

    def test_keyboard_shortcuts_section(self, client):
        assert "Keyboard Shortcuts" in client.get("/settings").text

    def test_all_sections_rendered(self, client):
        html = client.get("/settings").text
        for section in [
            "Appearance", "AI Provider", "Budget", "Notifications",
            "Reset Progress", "Mutation Diff", "Keyboard Shortcuts",
        ]:
            assert section in html, f"Missing section: {section}"


class TestAppearanceSection:
    """Appearance section: theme, density, currency, timezone."""

    def test_theme_toggle(self, client):
        assert 'id="theme-toggle"' in client.get("/settings").text

    def test_density_toggle(self, client):
        assert 'id="density-toggle"' in client.get("/settings").text

    def test_currency_select(self, client):
        assert 'id="currency-select"' in client.get("/settings").text

    def test_timezone_select(self, client):
        assert 'id="timezone-select"' in client.get("/settings").text


class TestAIProviderSection:
    """AI Provider section: local/cloud inference config + test button."""

    def test_inference_mode_toggle(self, client):
        assert 'id="inference-mode-toggle"' in client.get("/settings").text

    def test_local_panel(self, client):
        assert 'id="inference-local-panel"' in client.get("/settings").text

    def test_cloud_panel(self, client):
        assert 'id="inference-cloud-panel"' in client.get("/settings").text

    def test_test_connection_button(self, client):
        assert 'id="inference-test-btn"' in client.get("/settings").text


class TestBudgetSection:
    """Budget section: token-cost daily/monthly caps with usage bars."""

    def test_daily_cap_input(self, client):
        assert 'id="cost-daily-cap-input"' in client.get("/settings").text

    def test_monthly_cap_input(self, client):
        assert 'id="cost-monthly-cap-input"' in client.get("/settings").text

    def test_usage_bars(self, client):
        html = client.get("/settings").text
        assert 'id="settings-cost-daily-bar"' in html
        assert 'id="settings-cost-monthly-bar"' in html


class TestResetProgressSection:
    """Reset Progress section: destructive reset with confirmation."""

    def test_reset_button(self, client):
        assert 'id="reset-btn"' in client.get("/settings").text

    def test_reset_confirmation_required(self, client):
        assert 'id="reset-confirm-check"' in client.get("/settings").text


class TestMutationDiffSection:
    """Mutation Diff section: operator review of proposed mutations."""

    def test_diff_modal_present(self, client):
        assert 'id="diff-modal"' in client.get("/settings").text

    def test_diff_container_present(self, client):
        assert 'id="diff-container"' in client.get("/settings").text


class TestKeyboardShortcutsSection:
    """Keyboard Shortcuts section + command palette."""

    def test_shortcut_overlay(self, client):
        assert 'id="shortcut-overlay"' in client.get("/settings").text

    def test_command_palette(self, client):
        assert 'id="cmd-k"' in client.get("/settings").text
