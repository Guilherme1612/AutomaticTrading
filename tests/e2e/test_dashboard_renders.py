"""E2E tests for PMACS dashboard — all 7 pages render correctly."""

import pytest
from fastapi.testclient import TestClient

from pmacs.web.app import app


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestDashboardPage:
    """Tests for / (Dashboard) page."""

    def test_dashboard_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_dashboard_contains_pmacs_wordmark(self, client):
        response = client.get("/")
        assert "PMACS" in response.text

    def test_dashboard_contains_portfolio_value(self, client):
        response = client.get("/")
        assert "Portfolio Value" in response.text
        assert "$5,000.00" in response.text

    def test_dashboard_contains_risk_metrics(self, client):
        response = client.get("/")
        assert "Max Drawdown" in response.text
        assert "Sharpe" in response.text
        assert "Win Rate" in response.text

    def test_dashboard_contains_kill_switch(self, client):
        response = client.get("/")
        assert "Kill Switch" in response.text

    def test_dashboard_has_active_positions_section(self, client):
        response = client.get("/")
        assert "Active Positions" in response.text

    def test_dashboard_has_system_health(self, client):
        response = client.get("/")
        assert "System Health" in response.text

    def test_dashboard_has_mutation_summary(self, client):
        response = client.get("/")
        assert "Mutation Engine" in response.text


class TestAgentsPage:
    """Tests for /agents page."""

    def test_agents_returns_200(self, client):
        response = client.get("/agents")
        assert response.status_code == 200

    def test_agents_contains_pmacs(self, client):
        response = client.get("/agents")
        assert "PMACS" in response.text

    def test_agents_contains_personas(self, client):
        response = client.get("/agents")
        assert "Gatekeeper" in response.text
        assert "Catalyst Summarizer" in response.text
        assert "Growth Hunter" in response.text
        assert "Moat Analyst" in response.text
        assert "Macro Regime" in response.text
        assert "Insider Activity" in response.text
        assert "Short Interest" in response.text
        assert "Forensics" in response.text
        assert "Crucible" in response.text

    def test_agents_has_queue_strip(self, client):
        response = client.get("/agents")
        assert "Queue" in response.text

    def test_agents_has_sankey_placeholder(self, client):
        response = client.get("/agents")
        assert "Sankey" in response.text

    def test_agents_has_sidebar(self, client):
        response = client.get("/agents")
        assert "Agents" in response.text
        assert "/agents" in response.text


class TestPipelinePage:
    """Tests for /pipeline page."""

    def test_pipeline_returns_200(self, client):
        response = client.get("/pipeline")
        assert response.status_code == 200

    def test_pipeline_contains_pmacs(self, client):
        response = client.get("/pipeline")
        assert "PMACS" in response.text

    def test_pipeline_has_verdict_columns(self, client):
        response = client.get("/pipeline")
        assert "STRONG_BUY" in response.text
        assert "BUY" in response.text
        assert "HOLD" in response.text
        assert "SKIP" in response.text

    def test_pipeline_has_filter_bar(self, client):
        response = client.get("/pipeline")
        assert "Filter tickers" in response.text

    def test_pipeline_has_queue_info(self, client):
        response = client.get("/pipeline")
        assert "Queue:" in response.text


class TestUniversePage:
    """Tests for /universe page."""

    def test_universe_returns_200(self, client):
        response = client.get("/universe")
        assert response.status_code == 200

    def test_universe_contains_pmacs(self, client):
        response = client.get("/universe")
        assert "PMACS" in response.text

    def test_universe_has_ticker_heading(self, client):
        response = client.get("/universe")
        assert "Ticker Universe" in response.text

    def test_universe_has_add_ticker_button(self, client):
        response = client.get("/universe")
        assert "Add Ticker" in response.text

    def test_universe_has_group_tabs(self, client):
        response = client.get("/universe")
        assert "Watchlist" in response.text
        assert "Portfolio" in response.text
        assert "Sectors" in response.text

    def test_universe_empty_state(self, client):
        response = client.get("/universe")
        assert "No tickers in universe" in response.text


class TestCortexPage:
    """Tests for /cortex page."""

    def test_cortex_returns_200(self, client):
        response = client.get("/cortex")
        assert response.status_code == 200

    def test_cortex_contains_pmacs(self, client):
        response = client.get("/cortex")
        assert "PMACS" in response.text

    def test_cortex_has_audit_chain(self, client):
        response = client.get("/cortex")
        assert "Audit Chain" in response.text

    def test_cortex_has_cross_db(self, client):
        response = client.get("/cortex")
        assert "Cross-DB" in response.text or "sqlite" in response.text.lower()

    def test_cortex_has_process_status(self, client):
        response = client.get("/cortex")
        assert "Process Status" in response.text
        assert "pmacs-inference" in response.text
        assert "pmacs-nervous" in response.text

    def test_cortex_has_disk_clock_network(self, client):
        response = client.get("/cortex")
        assert "Disk" in response.text
        assert "Clock" in response.text
        assert "Network" in response.text

    def test_cortex_has_kill_switch(self, client):
        response = client.get("/cortex")
        assert "Kill Switch" in response.text

    def test_cortex_has_model_integrity(self, client):
        response = client.get("/cortex")
        assert "Model Integrity" in response.text


class TestDebugPage:
    """Tests for /debug page."""

    def test_debug_returns_200(self, client):
        response = client.get("/debug")
        assert response.status_code == 200

    def test_debug_contains_pmacs(self, client):
        response = client.get("/debug")
        assert "PMACS" in response.text

    def test_debug_has_event_stream(self, client):
        response = client.get("/debug")
        assert "Event Stream" in response.text

    def test_debug_has_filter_chips(self, client):
        response = client.get("/debug")
        assert "CYCLE" in response.text
        assert "TRADE" in response.text
        assert "ERROR" in response.text

    def test_debug_has_sse_reference(self, client):
        response = client.get("/debug")
        assert "SSE" in response.text or "pmacs-nervous" in response.text


class TestSettingsPage:
    """Tests for /settings page."""

    def test_settings_returns_200(self, client):
        response = client.get("/settings")
        assert response.status_code == 200

    def test_settings_contains_pmacs(self, client):
        response = client.get("/settings")
        assert "PMACS" in response.text

    def test_settings_has_sections(self, client):
        response = client.get("/settings")
        assert "General" in response.text
        assert "Risk" in response.text
        assert "Crucible" in response.text
        assert "Mutation Engine" in response.text
        assert "Inference" in response.text
        assert "Operator" in response.text

    def test_settings_has_paper_capital(self, client):
        response = client.get("/settings")
        assert "$5,000" in response.text

    def test_settings_has_catastrophe_stop(self, client):
        response = client.get("/settings")
        assert "15%" in response.text


class TestCommonElements:
    """Tests for elements present on all pages."""

    @pytest.fixture(params=["/", "/agents", "/pipeline", "/universe", "/cortex", "/debug", "/settings"])
    def page_response(self, client, request):
        return client.get(request.param)

    def test_all_pages_have_pmacs_wordmark(self, page_response):
        assert "PMACS" in page_response.text

    def test_all_pages_have_kill_switch_button(self, page_response):
        assert "Kill Switch" in page_response.text

    def test_all_pages_have_sidebar_nav(self, page_response):
        assert "Dashboard" in page_response.text
        assert "Agents" in page_response.text
        assert "Pipeline" in page_response.text
        assert "Universe" in page_response.text
        assert "Cortex" in page_response.text
        assert "Debug" in page_response.text
        assert "Settings" in page_response.text

    def test_all_pages_have_cmd_k(self, page_response):
        assert "cmd-k" in page_response.text

    def test_all_pages_have_mode_badge(self, page_response):
        assert "SHADOW + PAPER" in page_response.text

    def test_all_pages_have_tailwind(self, page_response):
        assert "tailwindcss" in page_response.text

    def test_all_pages_have_htmx(self, page_response):
        assert "htmx" in page_response.text.lower()

    def test_all_pages_have_fonts(self, page_response):
        assert "Inter" in page_response.text
        assert "JetBrains" in page_response.text
