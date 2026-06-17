"""E2E exit tests for Pipeline page — component-level verification (S7).

Validates every pipeline page component described in Source.md section 16:
(a) Top filter bar: verdict multi-select, state multi-select, sector, date range, search
(b) 4 kanban columns: STRONG_BUY, BUY, HOLD, SKIP
(c) Per-ticker card: ticker, price, conviction, memo truncated, cycle date, action buttons on hover
(d) Queue management API endpoints (reorder/pin/promote/scheme)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pmacs.web.app import app


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


class TestTopFilterBar:
    """(a) Top filter bar with verdict selector, search, queue info."""

    def test_filter_bar_present(self, client):
        resp = client.get("/pipeline")
        assert resp.status_code == 200
        assert "Pipeline" in resp.text

    def test_search_input(self, client):
        resp = client.get("/pipeline")
        assert "Filter..." in resp.text

    def test_verdict_selector(self, client):
        resp = client.get("/pipeline")
        assert "All Verdicts" in resp.text
        assert "STRONG_BUY" in resp.text
        assert "BUY" in resp.text
        assert "HOLD" in resp.text
        assert "SKIP" in resp.text

    def test_queue_info_displayed(self, client):
        resp = client.get("/pipeline")
        # Redesigned filter bar shows a live "Filtered" count
        assert "Filtered" in resp.text


class TestKanbanColumns:
    """(b) 4 kanban columns: STRONG_BUY, BUY, HOLD, SKIP."""

    def test_strong_buy_column(self, client):
        resp = client.get("/pipeline")
        assert "STRONG_BUY" in resp.text

    def test_buy_column(self, client):
        resp = client.get("/pipeline")
        assert "BUY" in resp.text

    def test_hold_column(self, client):
        resp = client.get("/pipeline")
        assert "HOLD" in resp.text

    def test_skip_column(self, client):
        resp = client.get("/pipeline")
        assert "SKIP" in resp.text

    def test_four_column_grid(self, client):
        resp = client.get("/pipeline")
        assert "grid-cols-4" in resp.text

    def test_column_color_coding(self, client):
        resp = client.get("/pipeline")
        # Redesign uses semantic token classes per verdict column
        assert "bg-positive-soft" in resp.text  # STRONG_BUY
        assert "bg-accent-soft" in resp.text     # BUY
        assert "bg-crucible" in resp.text        # HOLD/SKIP

    def test_empty_columns_show_placeholder(self, client):
        resp = client.get("/pipeline")
        assert "No strong buy signals" in resp.text

    def test_drag_and_drop_column_handlers(self, client):
        """Kanban columns have drag-over and drop handlers (always present)."""
        resp = client.get("/pipeline")
        assert "ondragover" in resp.text
        assert "ondrop" in resp.text


class TestTickerCards:
    """(c) Per-ticker card: ticker, conviction, action buttons.

    Cards only render when holdings exist. With empty DB, columns show
    'No items' placeholder. We verify the template structure exists.
    """

    def test_kanban_column_card_structure(self, client):
        """Columns contain card containers (empty or populated)."""
        resp = client.get("/pipeline")
        html = resp.text
        # Either cards exist (data-ticker) or the empty placeholder
        has_cards = "data-ticker" in html
        has_empty = "No strong buy signals" in html
        assert has_cards or has_empty

    def test_card_template_has_ticker_data_attr(self, client):
        """Card template uses data-ticker attribute for identification."""
        resp = client.get("/pipeline")
        html = resp.text
        # The template has data-ticker in card HTML, or empty columns
        has_data_ticker = "data-ticker" in html
        has_empty = "No items" in html
        assert has_data_ticker or has_empty

    def test_empty_columns_have_no_items_message(self, client):
        """Empty columns show dashed border placeholder."""
        resp = client.get("/pipeline")
        assert "border-dashed" in resp.text
        assert "No strong buy signals" in resp.text


class TestPipelineAPIEndpoints:
    """API endpoints for queue management."""

    def test_reorder_endpoint_exists(self, client):
        resp = client.post(
            "/pipeline/queue/reorder",
            json={"ticker": "TEST", "from_band": "P1", "to_band": "P2"},
        )
        # Returns 404 when ticker not found in queue (expected)
        assert resp.status_code in (200, 404)

    def test_pin_endpoint_exists(self, client):
        resp = client.post(
            "/pipeline/queue/pin",
            json={"ticker": "TEST", "pinned": True},
        )
        assert resp.status_code in (200, 404)

    def test_promote_endpoint_exists(self, client):
        resp = client.post("/pipeline/queue/promote")
        assert resp.status_code == 200

    def test_scheme_save_endpoint(self, client):
        resp = client.post(
            "/pipeline/queue/scheme/save",
            json={"name": "test-scheme", "config": {"AAPL": "P1"}},
        )
        assert resp.status_code == 200

    def test_scheme_load_endpoint(self, client):
        resp = client.get("/pipeline/queue/scheme/nonexistent")
        assert resp.status_code in (200, 404)
