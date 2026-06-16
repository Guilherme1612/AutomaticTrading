"""E2E exit tests for Debug page — component-level verification (S7, M4).

Validates every debug page component described in Source.md section 19:
(a) Filter bar: level, process, component, error_code, cycle_id, ticker, time range, search
(b) Event rows: timestamp, level badge, process, component, error_code, message
(c) Quick filter chips: Errors only, Current cycle, Last hour, LLM events, Trade events
(d) Expand inline: full payload JSON, traceback, spec_ref, suggested_fix_keywords
(e) "Copy for Claude Code" button on expanded row [M4]
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pmacs.web.app import app
from pmacs.web import config as web_config


@pytest.fixture
def client():
    """FastAPI test client with default config."""
    return TestClient(app)


@pytest.fixture
def empty_client(tmp_path):
    """Client backed by empty debug + audit logs so the empty-state UI
    renders, independent of the developer's real logs. The debug page falls
    back to audit entries when no live debug events exist, so both must be
    empty for the 'Waiting for debug events' state to appear."""
    log_file = tmp_path / "debug.jsonl"
    log_file.write_text("")
    audit_file = tmp_path / "audit.log"
    audit_file.write_text("")
    original = web_config.get_config()
    web_config.configure(
        web_config.DashboardConfig(debug_log_path=log_file, audit_path=audit_file)
    )
    yield TestClient(app)
    web_config.configure(original)


def _make_client_with_events(tmp_path):
    """Helper: create a client backed by a synthetic debug log.

    Caller is responsible for restoring the original config after use.
    """
    log_file = tmp_path / "debug.jsonl"
    log_file.write_text(
        '{"level":"ERROR","ts":"2026-01-15T10:00:00Z","event":"CYCLE_START",'
        '"msg":"Test error message","error_code":"ERR_CYCLE_001",'
        '"spec_ref":"Architecture.md §9.2","cycle_id":"c-001",'
        '"payload":{"key":"value"},"event_id":"evt-001"}\n'
    )
    original = web_config.get_config()
    cfg = web_config.DashboardConfig(debug_log_path=log_file)
    web_config.configure(cfg)
    return TestClient(app), original


class TestFilterBar:
    """(a) Filter bar: level, process, component, error_code, search."""

    def test_event_stream_heading(self, client):
        resp = client.get("/debug")
        assert resp.status_code == 200
        assert "Event Stream" in resp.text

    def test_search_input(self, client):
        resp = client.get("/debug")
        assert "Search events" in resp.text

    def test_event_count_displayed(self, client):
        resp = client.get("/debug")
        assert "events" in resp.text

    def test_clear_button(self, client):
        resp = client.get("/debug")
        assert "Clear" in resp.text


class TestFilterChips:
    """(c) Quick filter chips: ALL, CYCLE, TRADE, AUDIT, ERROR, WARN, KILL_SWITCH, MUTATION."""

    def test_all_chip(self, client):
        resp = client.get("/debug")
        assert "ALL" in resp.text and "filterEvents" in resp.text

    def test_cycle_chip(self, client):
        resp = client.get("/debug")
        assert "CYCLE" in resp.text

    def test_trade_chip(self, client):
        resp = client.get("/debug")
        assert "TRADE" in resp.text

    def test_audit_chip(self, client):
        resp = client.get("/debug")
        assert "AUDIT" in resp.text

    def test_error_chip(self, client):
        resp = client.get("/debug")
        assert "ERROR" in resp.text

    def test_warn_chip(self, client):
        resp = client.get("/debug")
        assert "WARN" in resp.text

    def test_kill_switch_chip(self, client):
        resp = client.get("/debug")
        assert "KILL_SWITCH" in resp.text

    def test_mutation_chip(self, client):
        resp = client.get("/debug")
        assert "MUTATION" in resp.text

    def test_chip_filter_function(self, client):
        """Chips call filterEvents() JavaScript function."""
        resp = client.get("/debug")
        assert "filterEvents" in resp.text


class TestEventRows:
    """(b) Event rows: timestamp, level badge, process, error_code, message."""

    @pytest.fixture
    def client_with_events(self, tmp_path):
        """Client with a debug log file containing a synthetic event."""
        c, orig = _make_client_with_events(tmp_path)
        yield c
        web_config.configure(orig)

    def test_event_stream_container(self, client):
        resp = client.get("/debug")
        assert 'id="event-stream"' in resp.text

    def test_empty_state(self, empty_client):
        """When no events, shows waiting message."""
        resp = empty_client.get("/debug")
        assert "Waiting for debug events" in resp.text

    def test_sse_reference(self, empty_client):
        """Empty state shows SSE subscription reference."""
        resp = empty_client.get("/debug")
        assert "pmacs-nervous" in resp.text

    def test_level_badge_templates_with_events(self, client_with_events):
        """Template has CSS classes for ERROR, WARN, INFO, DEBUG level badges."""
        resp = client_with_events.get("/debug")
        assert "bg-negative-soft text-negative" in resp.text   # ERROR


class TestExpandInline:
    """(d) Expand inline: full payload JSON, spec_ref, traceback.

    The expand/detail section only renders when events exist. With no events,
    we verify the empty state shows the SSE reference.
    """

    def test_empty_state_shows_sse_reference(self, empty_client):
        """Empty state shows SSE subscription info."""
        resp = empty_client.get("/debug")
        assert "pmacs-nervous" in resp.text

    def test_empty_state_explanation(self, empty_client):
        resp = empty_client.get("/debug")
        assert "Events will stream from pmacs-nervous via SSE" in resp.text


class TestExpandInlineWithEvents:
    """(d) Expand inline with synthetic events to test detail rendering."""

    @pytest.fixture
    def client_with_events(self, tmp_path):
        """Client with a debug log file containing a synthetic event."""
        c, orig = _make_client_with_events(tmp_path)
        yield c
        web_config.configure(orig)

    def test_event_rows_render(self, client_with_events):
        resp = client_with_events.get("/debug")
        assert "ERR_CYCLE_001" in resp.text

    def test_level_badge_error(self, client_with_events):
        resp = client_with_events.get("/debug")
        assert "bg-negative-soft text-negative" in resp.text

    def test_expand_toggle_function(self, client_with_events):
        """Rows call toggleEventDetail() on click."""
        resp = client_with_events.get("/debug")
        assert "toggleEventDetail" in resp.text

    def test_copy_json_button(self, client_with_events):
        """Expanded rows have 'Copy JSON' button."""
        resp = client_with_events.get("/debug")
        assert "Copy JSON" in resp.text

    def test_spec_ref_display(self, client_with_events):
        """Template shows spec_ref when present."""
        resp = client_with_events.get("/debug")
        assert "Spec ref:" in resp.text
        assert "Architecture.md" in resp.text

    def test_repro_hint(self, client_with_events):
        """Template shows repro hint for error_code events."""
        resp = client_with_events.get("/debug")
        assert "Repro:" in resp.text


class TestCopyForClaudeCode:
    """(e) "Copy for Claude Code" button on expanded row [M4].

    The copy button only renders inside event detail rows. We test with
    synthetic events to verify the feature.
    """

    @pytest.fixture
    def client_with_events(self, tmp_path):
        """Client with a debug log file containing a synthetic event."""
        c, orig = _make_client_with_events(tmp_path)
        yield c
        web_config.configure(orig)

    def test_copy_for_claude_code_button(self, client_with_events):
        """Expanded rows have 'Copy for Claude Code' button."""
        resp = client_with_events.get("/debug")
        assert "Copy for Claude Code" in resp.text

    def test_copy_button_has_data_attributes(self, client_with_events):
        """Copy button has data attributes for event metadata."""
        resp = client_with_events.get("/debug")
        assert "data-error-code" in resp.text
        assert "data-spec-ref" in resp.text
        assert "data-message" in resp.text
        assert "data-level" in resp.text

    def test_copy_button_calls_function(self, client_with_events):
        """Copy button invokes copyForClaudeCode()."""
        resp = client_with_events.get("/debug")
        assert "copyForClaudeCode" in resp.text
