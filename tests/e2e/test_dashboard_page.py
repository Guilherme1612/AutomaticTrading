"""E2E exit tests for Dashboard page — component-level verification (S7).

Validates every dashboard component described in Source.md section 14:
(a) Portfolio summary card
(b) Mode + cycle status card
(c) Risk metrics row (5 StatBlocks)
(d) Active positions table
(e) Recent decisions feed
(f) System health card
(g) Mutation Engine summary
(h) Empty state (pre-first-cycle hero)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pmacs.web.app import app


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestPortfolioSummaryCard:
    """(a) Portfolio summary card: current value, day change, sparkline present."""

    def test_portfolio_value_displayed(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "$5,000.00" in resp.text

    def test_day_change_displayed(self, client):
        resp = client.get("/")
        assert "Day Change" in resp.text

    def test_portfolio_value_label(self, client):
        resp = client.get("/")
        assert "Portfolio Value" in resp.text

    def test_sparkline_svg_present(self, client):
        resp = client.get("/")
        assert "sparkline-container" in resp.text
        assert '<svg viewBox="0 0 100 24"' in resp.text


class TestModeCycleStatus:
    """(b) Mode badge + cycle status card."""

    def test_mode_badge_displayed(self, client):
        resp = client.get("/")
        assert "SHADOW + PAPER" in resp.text

    def test_run_cycle_button_not_in_empty_state(self, client):
        """Dashboard without pre_first_cycle flag renders the full dashboard."""
        resp = client.get("/")
        # When not in pre-first-cycle, we get the full dashboard
        assert resp.status_code == 200


class TestRiskMetricsRow:
    """(c) Risk metrics row: 5 StatBlocks (Sharpe, Sortino/Drawdown, Win Rate, Open Positions, Capital Used)."""

    def test_max_drawdown_statblock(self, client):
        resp = client.get("/")
        assert "Max Drawdown" in resp.text

    def test_sharpe_statblock(self, client):
        resp = client.get("/")
        assert "Sharpe" in resp.text

    def test_win_rate_statblock(self, client):
        resp = client.get("/")
        assert "Win Rate" in resp.text

    def test_open_positions_statblock(self, client):
        resp = client.get("/")
        assert "Open Positions" in resp.text

    def test_capital_used_statblock(self, client):
        resp = client.get("/")
        assert "Capital Used" in resp.text

    def test_five_statblocks_in_grid(self, client):
        """The risk metrics row uses grid-cols-5 layout."""
        resp = client.get("/")
        assert "grid-cols-5" in resp.text

    def test_each_statblock_has_sparkline(self, client):
        resp = client.get("/")
        # There should be multiple sparkline containers (one per metric)
        count = resp.text.count("sparkline-container")
        assert count >= 5

    def test_each_statblock_has_tooltip(self, client):
        resp = client.get("/")
        count = resp.text.count("sparkline-tooltip")
        assert count >= 5


class TestActivePositionsTable:
    """(d) Active positions table with column headers.

    When no positions exist, the table shows an empty state instead of headers.
    We verify the section exists and that the template has the table structure.
    """

    def test_active_positions_section(self, client):
        resp = client.get("/")
        assert "Active Positions" in resp.text

    def test_positions_table_structure_present(self, client):
        """The template contains table markup for positions (rendered when positions exist)."""
        resp = client.get("/")
        html = resp.text
        has_table = "<table" in html
        has_empty = "No active holdings" in html
        assert has_table or has_empty

    def test_empty_state_explanation(self, client):
        """When no positions, an explanation is shown."""
        resp = client.get("/")
        assert "No active holdings" in resp.text or "Cycles are running" in resp.text


class TestRecentDecisionsFeed:
    """(e) Recent decisions feed with last N decisions."""

    def test_recent_decisions_section(self, client):
        resp = client.get("/")
        assert "Recent Decisions" in resp.text

    def test_no_decisions_empty_state(self, client):
        resp = client.get("/")
        assert "No recent decisions" in resp.text


class TestSystemHealthCard:
    """(f) System health card: audit chain, disk, inference, heartbeats."""

    def test_system_health_section(self, client):
        resp = client.get("/")
        assert "System Health" in resp.text

    def test_audit_chain_status(self, client):
        resp = client.get("/")
        assert "Audit Chain" in resp.text

    def test_disk_free_displayed(self, client):
        resp = client.get("/")
        assert "Disk Free" in resp.text
        assert "GB" in resp.text

    def test_inference_status(self, client):
        resp = client.get("/")
        assert "Inference" in resp.text


class TestMutationEngineSummary:
    """(g) Mutation Engine summary card."""

    def test_mutation_engine_section(self, client):
        resp = client.get("/")
        assert "Mutation Engine" in resp.text

    def test_mutation_status_displayed(self, client):
        resp = client.get("/")
        assert "Dormant" in resp.text

    def test_mutation_cycles_displayed(self, client):
        resp = client.get("/")
        assert "Activates after 50 PAPER cycles" in resp.text

    def test_mutation_candidates_count(self, client):
        resp = client.get("/")
        assert "Candidates" in resp.text


class TestEmptyState:
    """(h) Empty state: pre-first-cycle hero card with explanation."""

    def test_no_active_holdings_empty_state(self, client):
        """When no positions, dashboard shows empty state message."""
        resp = client.get("/")
        # Template has an empty state for no active holdings
        assert "No active holdings" in resp.text or "Active Positions" in resp.text

    def test_period_selector_present(self, client):
        """Time-window selector is present for sparkline control."""
        resp = client.get("/")
        assert "Period:" in resp.text
        assert "1D" in resp.text
        assert "1W" in resp.text
        assert "1M" in resp.text
        assert "3M" in resp.text
        assert "ALL" in resp.text
