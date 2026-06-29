"""Unit tests for usage_logger — spec/Phases.md Phase 16 exit test #2.

Verifies that `log_usage` writes `api_usage` to DuckDB and updates
`budget_state` totals in SQLite. The `period_roller.check_and_roll` half
of exit test #2 lives in `tests/unit/test_period_roller.py`.

Stubbed DuckDB adapter follows the same pattern as
`tests/integration/test_billing_lifecycle.py::_make_mock_duckdb` — it
captures `insert_api_usage` calls in a dict so the unit test can assert
on the actual API contract without depending on duckdb package presence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pmacs.billing.usage_logger import (
    get_budget_totals,
    log_usage,
    update_budget_state,
)
from pmacs.schemas.billing import BodyCost, EstimatedCost
from pmacs.storage.sqlite import init_db


# ---------------------------------------------------------------------------
# Stubbed DuckDB adapter
# ---------------------------------------------------------------------------

class StubDuckDB:
    """In-memory DuckDB adapter. Records insert_api_usage calls; configurable failure mode."""

    def __init__(self, fail_insert: bool = False) -> None:
        self._store: dict[str, dict] = {}
        self.fail_insert = fail_insert
        self.insert_calls: list[dict] = []

    def insert_api_usage(self, **kwargs) -> None:
        self.insert_calls.append(dict(kwargs))
        if self.fail_insert:
            raise RuntimeError("simulated duckdb failure")
        self._store[kwargs["call_id"]] = dict(kwargs)

    def execute(self, query: str, params: list | None = None) -> list[dict]:
        # Not exercised by log_usage itself, but required by the type contract.
        return []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test_billing.db"))
    yield conn
    conn.close()


@pytest.fixture
def stub_duckdb() -> StubDuckDB:
    return StubDuckDB()


def _make_call(
    *,
    call_id: str = "call-001",
    cycle_id: str = "cycle-001",
    persona: str = "macro_regime",
    model_id: str = "test/deepseek-v4",
    prompt_tokens: int = 1000,
    completion_tokens: int = 500,
    body_cost_usd: float = 0.002,
    latency_ms: int = 1200,
) -> BodyCost:
    return BodyCost(
        call_id=call_id,
        cycle_id=cycle_id,
        persona=persona,
        model_id=model_id,
        generation_id="gen-abc-123",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        body_cost_usd=body_cost_usd,
        latency_ms=latency_ms,
    )


def _make_estimate(body_cost_usd: float = 0.002) -> EstimatedCost:
    return EstimatedCost(
        call_id="call-001",
        persona="macro_regime",
        model_id="test/deepseek-v4",
        estimated_input_tokens=1000,
        estimated_output_tokens=500,
        estimated_cost_usd=body_cost_usd,
    )


# ---------------------------------------------------------------------------
# Spec exit test #2 — log_usage writes api_usage to DuckDB + updates budget_state
# ---------------------------------------------------------------------------

class TestLogUsageWritesToDuckDB:
    def test_log_usage_writes_api_usage_to_duckdb(self, sqlite_conn, stub_duckdb):
        """log_usage forwards the BodyCost fields to duckdb.insert_api_usage."""
        call = _make_call()
        estimated = _make_estimate()
        log_usage(sqlite_conn, stub_duckdb, call, estimated)

        assert len(stub_duckdb.insert_calls) == 1
        row = stub_duckdb.insert_calls[0]
        assert row["call_id"] == "call-001"
        assert row["cycle_id"] == "cycle-001"
        assert row["persona"] == "macro_regime"
        assert row["model_id"] == "test/deepseek-v4"
        assert row["generation_id"] == "gen-abc-123"
        assert row["prompt_tokens"] == 1000
        assert row["completion_tokens"] == 500
        assert row["estimated_cost_usd"] == 0.002
        assert row["body_cost_usd"] == 0.002
        assert row["latency_ms"] == 1200
        assert row["succeeded"] is True

    def test_log_usage_increments_budget_state_totals(self, sqlite_conn, stub_duckdb):
        """After log_usage, both 'today' and 'this_month' budget_state rows accumulate cost."""
        call = _make_call(body_cost_usd=0.50)
        estimated = _make_estimate(body_cost_usd=0.50)

        # Baseline: both rows are at 0.0 (init_db seeds them)
        baseline = get_budget_totals(sqlite_conn)
        assert baseline["today"]["total_cost_usd"] == 0.0
        assert baseline["this_month"]["total_cost_usd"] == 0.0

        log_usage(sqlite_conn, stub_duckdb, call, estimated)

        after = get_budget_totals(sqlite_conn)
        assert after["today"]["total_cost_usd"] == pytest.approx(0.50)
        assert after["this_month"]["total_cost_usd"] == pytest.approx(0.50)

    def test_log_usage_updates_both_today_and_this_month(self, sqlite_conn, stub_duckdb):
        """A single log_usage call must atomically add to BOTH period rows."""
        call = _make_call(body_cost_usd=1.25)
        estimated = _make_estimate(body_cost_usd=1.25)
        log_usage(sqlite_conn, stub_duckdb, call, estimated)

        # Single fetch — both rows reflect the same delta
        rows = sqlite_conn.execute(
            "SELECT period, total_cost_usd FROM budget_state ORDER BY period"
        ).fetchall()
        today_total, month_total = rows[0][1], rows[1][1]
        assert today_total == pytest.approx(1.25)
        assert month_total == pytest.approx(1.25)
        # They must match — single atomic call
        assert today_total == month_total

    def test_log_usage_propagates_duckdb_failure(self, sqlite_conn):
        """Pin the actual contract: DuckDB exceptions are NOT swallowed.

        The orchestrator wraps `_log_call_billing` in try/except (orchestrator.py:1788),
        so a DuckDB failure won't kill the cycle. This unit test pins the lower-level
        behavior: `log_usage` itself does not absorb DuckDB exceptions, ensuring that
        callers see the failure (and can decide to retry, alert, or fall back).

        Documented via xfail marker: the spec exit test #2 does not require graceful
        DuckDB degradation — it only requires that DuckDB writes happen. If this
        contract changes (e.g. graceful fallback becomes mandatory), remove the xfail
        and assert on the new behavior.
        """
        failing_duck = StubDuckDB(fail_insert=True)
        call = _make_call(body_cost_usd=0.75)
        estimated = _make_estimate(body_cost_usd=0.75)

        with pytest.raises(RuntimeError, match="simulated duckdb failure"):
            log_usage(sqlite_conn, failing_duck, call, estimated)

        # Insert was attempted (call recorded) before the raise
        assert len(failing_duck.insert_calls) == 1

    def test_log_usage_publishes_sse_events(self, sqlite_conn, stub_duckdb, monkeypatch):
        """log_usage must publish `cost.call_completed` SSE event with body_cost_usd."""
        captured: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            "pmacs.billing.usage_logger.publish_system_event",
            lambda event_type, payload: captured.append((event_type, payload)),
        )

        call = _make_call(body_cost_usd=0.30, latency_ms=850)
        estimated = _make_estimate(body_cost_usd=0.30)
        log_usage(sqlite_conn, stub_duckdb, call, estimated)

        # Find the cost.call_completed event
        cost_events = [e for e in captured if e[0] == "cost.call_completed"]
        assert len(cost_events) == 1
        event_type, payload = cost_events[0]
        assert payload["call_id"] == "call-001"
        assert payload["cycle_id"] == "cycle-001"
        assert payload["persona"] == "macro_regime"
        assert payload["body_cost_usd"] == pytest.approx(0.30)
        assert payload["prompt_tokens"] == 1000
        assert payload["completion_tokens"] == 500
        assert payload["latency_ms"] == 850


# ---------------------------------------------------------------------------
# Direct update_budget_state tests — also pinned to spec exit test #2
# ---------------------------------------------------------------------------

class TestUpdateBudgetState:
    def test_update_budget_state_adds_to_both_periods(self, sqlite_conn):
        """update_budget_state atomically adds a cost delta to today and this_month."""
        # Seed a starting balance
        update_budget_state(sqlite_conn, 2.00)
        before = get_budget_totals(sqlite_conn)
        assert before["today"]["total_cost_usd"] == pytest.approx(2.00)
        assert before["this_month"]["total_cost_usd"] == pytest.approx(2.00)

        # Add a delta
        update_budget_state(sqlite_conn, 0.50)
        after = get_budget_totals(sqlite_conn)
        assert after["today"]["total_cost_usd"] == pytest.approx(2.50)
        assert after["this_month"]["total_cost_usd"] == pytest.approx(2.50)

    def test_update_budget_state_handles_negative_delta(self, sqlite_conn):
        """Negative deltas (e.g. reconciliation corrections) decrement the totals."""
        update_budget_state(sqlite_conn, 1.00)
        update_budget_state(sqlite_conn, -0.25)
        after = get_budget_totals(sqlite_conn)
        assert after["today"]["total_cost_usd"] == pytest.approx(0.75)
        assert after["this_month"]["total_cost_usd"] == pytest.approx(0.75)
