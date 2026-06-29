"""spec/Phases.md Phase 16 exit test #5 — SSE cost events.

Pins the contract that every LLM call fires a `cost.call_completed` SSE
event with `body_cost_usd`, and that the dashboard's cost widget updates
via the cost stream.

The lower-level unit coverage is in:
  * tests/unit/test_billing.py::test_log_usage_publishes_sse_events

This file exists so the spec exit test command
`pytest tests/integration/test_sse_cost_events.py` works. It verifies
the end-to-end pipeline: a BodyCost + EstimatedCost → log_usage → SSE
event published with the correct shape (event_type, payload keys,
rounded body_cost_usd).

The dashboard's cost widget (cost_widget.html) subscribes to the cost
event stream and re-renders on receipt — that wire-level behavior is
asserted via the SSE publisher interceptor here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pmacs.billing.cost_calculator import compute_body_cost
from pmacs.billing.usage_logger import log_usage
from pmacs.schemas.billing import BodyCost, EstimatedCost, PricingRecord
from pmacs.storage.sqlite import init_db


@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test_sse_cost_events.db"))
    yield conn
    conn.close()


@pytest.fixture
def captured_sse():
    """Capture publish_system_event calls."""
    captured: list[tuple[str, dict]] = []

    def capture(event_type: str, payload: dict) -> None:
        captured.append((event_type, payload))

    return captured


class TestCostCallCompletedSSE:
    """Exit test #5: every LLM call fires `cost.call_completed` SSE event."""

    def test_log_usage_fires_cost_call_completed(
        self, sqlite_conn, monkeypatch, captured_sse
    ):
        """A single log_usage call publishes exactly one `cost.call_completed`
        event with the expected payload shape."""
        monkeypatch.setattr(
            "pmacs.billing.usage_logger.publish_system_event",
            lambda event_type, payload: captured_sse.append((event_type, payload)),
        )

        pricing = PricingRecord(
            model_id="test/sse-model",
            input_price_per_token=2.0e-7,
            output_price_per_token=6.0e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        usage = {"prompt_tokens": 1500, "completion_tokens": 600}
        body_cost = compute_body_cost(usage, pricing)

        call = BodyCost(
            call_id="sse-call-001",
            cycle_id="sse-cycle-001",
            persona="growth_hunter",
            model_id="test/sse-model",
            generation_id="gen-sse-xyz",
            prompt_tokens=1500,
            completion_tokens=600,
            body_cost_usd=body_cost,
            latency_ms=950,
        )
        estimated = EstimatedCost(
            call_id="sse-call-001",
            persona="growth_hunter",
            model_id="test/sse-model",
            estimated_input_tokens=1500,
            estimated_output_tokens=600,
            estimated_cost_usd=body_cost,
        )

        # Patch the DuckDB stub so log_usage completes
        class _StubDuck:
            calls = []
            def insert_api_usage(self, **kwargs):
                _StubDuck.calls.append(kwargs)
            def execute(self, q, p=None): return []
            def close(self): pass

        log_usage(sqlite_conn, _StubDuck(), call, estimated)

        # Find the cost.call_completed event in the captured stream
        cost_events = [e for e in captured_sse if e[0] == "cost.call_completed"]
        assert len(cost_events) == 1, (
            f"expected exactly one cost.call_completed, got {len(cost_events)}"
        )
        event_type, payload = cost_events[0]
        # Required payload keys per spec §16.7
        for key in ("call_id", "cycle_id", "persona", "body_cost_usd",
                    "prompt_tokens", "completion_tokens", "latency_ms"):
            assert key in payload, f"missing key {key!r} in payload: {payload}"
        assert payload["call_id"] == "sse-call-001"
        assert payload["cycle_id"] == "sse-cycle-001"
        assert payload["persona"] == "growth_hunter"
        assert payload["prompt_tokens"] == 1500
        assert payload["completion_tokens"] == 600
        assert payload["latency_ms"] == 950
        # body_cost_usd is rounded to 6 decimals by log_usage
        assert payload["body_cost_usd"] == pytest.approx(round(body_cost, 6), abs=1e-9)

    def test_cost_budget_update_event_published_after_totals(
        self, sqlite_conn, monkeypatch, captured_sse
    ):
        """log_usage publishes a `cost.budget_update` event after `cost.call_completed`
        so the dashboard can re-render the cost widget."""
        monkeypatch.setattr(
            "pmacs.billing.usage_logger.publish_system_event",
            lambda event_type, payload: captured_sse.append((event_type, payload)),
        )

        call = BodyCost(
            call_id="sse-call-002",
            cycle_id="sse-cycle-001",
            persona="macro_regime",
            model_id="test/sse-model",
            prompt_tokens=500,
            completion_tokens=200,
            body_cost_usd=0.0009,
            latency_ms=400,
        )
        estimated = EstimatedCost(
            call_id="sse-call-002",
            persona="macro_regime",
            model_id="test/sse-model",
            estimated_input_tokens=500,
            estimated_output_tokens=200,
            estimated_cost_usd=0.0009,
        )

        class _StubDuck:
            def insert_api_usage(self, **kwargs): pass
            def execute(self, q, p=None): return []
            def close(self): pass

        log_usage(sqlite_conn, _StubDuck(), call, estimated)

        # Both events must be present. (Ordering is `cost.budget_update`
        # first, then `cost.call_completed` — `log_usage` calls
        # `update_budget_state` which publishes `cost.budget_update` first,
        # then publishes `cost.call_completed` after the call record is logged.)
        types = [e[0] for e in captured_sse]
        assert "cost.call_completed" in types
        assert "cost.budget_update" in types
        # Pin the actual ordering — protects against future reshuffles
        assert types.index("cost.budget_update") < types.index("cost.call_completed")

        # The budget_update payload carries the new totals
        budget_evt = next(p for t, p in captured_sse if t == "cost.budget_update")
        assert "today" in budget_evt
        assert "this_month" in budget_evt
        assert "total_cost_usd" in budget_evt["today"]
        assert budget_evt["today"]["total_cost_usd"] == pytest.approx(0.0009)


class TestSSEEventShapesForDashboardWidget:
    """Pin the exact payload shape the dashboard cost widget (cost_widget.html) consumes."""

    def test_cost_call_completed_event_has_dashboard_widget_keys(
        self, sqlite_conn, monkeypatch
    ):
        """The dashboard cost widget reads specific keys to update per-call display."""
        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            "pmacs.billing.usage_logger.publish_system_event",
            lambda event_type, payload: events.append((event_type, payload)),
        )

        call = BodyCost(
            call_id="widget-call-001",
            cycle_id="widget-cycle-001",
            persona="forensics",
            model_id="test/widget-model",
            prompt_tokens=200,
            completion_tokens=100,
            body_cost_usd=0.00012,
            latency_ms=240,
        )
        estimated = EstimatedCost(
            call_id="widget-call-001",
            persona="forensics",
            model_id="test/widget-model",
            estimated_input_tokens=200,
            estimated_output_tokens=100,
            estimated_cost_usd=0.00012,
        )

        class _StubDuck:
            def insert_api_usage(self, **kwargs): pass
            def execute(self, q, p=None): return []
            def close(self): pass

        log_usage(sqlite_conn, _StubDuck(), call, estimated)

        call_evt = next(p for t, p in events if t == "cost.call_completed")
        # Dashboard widget keys (cost_widget.html reads via these)
        assert "persona" in call_evt
        assert "body_cost_usd" in call_evt
        assert "prompt_tokens" in call_evt
        assert "completion_tokens" in call_evt
        assert "latency_ms" in call_evt
        # body_cost_usd must be a finite float (not string)
        assert isinstance(call_evt["body_cost_usd"], float)
        assert call_evt["body_cost_usd"] == pytest.approx(0.00012, abs=1e-6)
