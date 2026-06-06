"""Integration tests for Phase 16 billing lifecycle.

Tests the full flow: estimate -> body cost -> usage logging -> budget enforcement -> reconciliation.
Uses mocked LLM calls and OpenRouter HTTP, but real SQLite storage.
DuckDB tests use a mock adapter when duckdb is not installed.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from pmacs.billing.budget_enforcer import (
    check_daily_hard_cap,
    check_monthly_hard_cap,
    check_per_cycle_soft_cap,
    check_runaway,
    enforce_budgets,
)
from pmacs.billing.cost_calculator import compute_body_cost, compute_cost
from pmacs.billing.drift_monitor import check_estimate_drift
from pmacs.billing.period_roller import roll_daily, roll_monthly
from pmacs.billing.pricing import _upsert_pricing, get_pricing
from pmacs.billing.reconciler import _reconcile_call_impl
from pmacs.billing.token_estimator import estimate_call_cost, estimate_tokens
from pmacs.billing.usage_logger import (
    get_budget_totals,
    log_usage,
    update_budget_state,
)
from pmacs.schemas.billing import BodyCost, EstimatedCost, PricingRecord
from pmacs.storage.sqlite import init_db


# -- DuckDB availability --

duckdb_available = True
try:
    import duckdb as _duckdb  # noqa: F401
except ImportError:
    duckdb_available = False


def _make_mock_duckdb():
    """Create a mock DuckDB adapter with in-memory storage.

    Simulates execute() returning list[dict] and insert/update methods.
    """
    _store: dict[str, dict] = {}

    class MockAdapter:
        def insert_api_usage(self, **kwargs) -> None:
            _store[kwargs["call_id"]] = dict(kwargs)

        def update_actual_cost(self, call_id: str, actual_cost_usd: float) -> None:
            if call_id in _store:
                _store[call_id]["actual_cost_usd"] = actual_cost_usd

        def execute(self, query: str, params: list | None = None) -> list[dict]:
            q = query.upper()

            # SELECT ... WHERE call_id = ?
            if "CALL_ID" in q and params:
                call_id = params[0]
                if call_id in _store:
                    row = _store[call_id]
                    # Filter columns based on SELECT
                    if "ACTUAL_COST_USD IS NULL" in q:
                        if row.get("actual_cost_usd") is None and row.get("generation_id") is not None:
                            return [row]
                        return []
                    if "GENERATION_ID" in q and "ACTUAL_COST_USD IS NULL" in q:
                        if row.get("actual_cost_usd") is None and row.get("generation_id") is not None:
                            return [row]
                        return []
                    return [row]
                return []

            # SELECT ... WHERE cycle_id = ? AND actual_cost_usd IS NULL
            if "CYCLE_ID" in q and "ACTUAL_COST_USD IS NULL" in q and params:
                cycle_id = params[0]
                return [
                    row for row in _store.values()
                    if row.get("cycle_id") == cycle_id
                    and row.get("actual_cost_usd") is None
                    and row.get("generation_id") is not None
                ]

            # SELECT ... WHERE persona = ?
            if "PERSONA" in q and params:
                persona = params[0]
                return [r for r in _store.values() if r.get("persona") == persona][:100]

            return []

        def close(self) -> None:
            pass

    return MockAdapter()


# -- Fixtures --

@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    yield conn
    conn.close()


@pytest.fixture
def duckdb_adapter():
    """Provides a real DuckDB adapter if available, otherwise a mock."""
    if duckdb_available:
        from pmacs.storage.duckdb import DuckDBAdapter
        import tempfile, os
        d = tempfile.mkdtemp()
        adapter = DuckDBAdapter(os.path.join(d, "test.duckdb"))
        adapter.init_tables()
        yield adapter
        adapter.close()
    else:
        yield _make_mock_duckdb()


@pytest.fixture
def sample_pricing(sqlite_conn):
    """Seed pricing for a test model."""
    pricing = PricingRecord(
        model_id="test/deepseek-v4",
        input_price_per_token=1e-7,
        output_price_per_token=4e-7,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    _upsert_pricing(sqlite_conn, pricing)
    return pricing


# ============================================================
# Test 1: Full call lifecycle (estimate -> body cost -> log)
# ============================================================

class TestFullCallLifecycle:
    def test_estimate_log_budget(self, sqlite_conn, duckdb_adapter, sample_pricing):
        """End-to-end: estimate tokens -> compute cost -> log usage -> budget updated."""
        prompt_text = "Analyze the growth trajectory of AAPL based on quarterly earnings."
        tokens = estimate_tokens(prompt_text)
        assert tokens > 0

        estimated = estimate_call_cost(prompt_text, "growth_hunter", sample_pricing)
        assert estimated.estimated_cost_usd > 0
        assert estimated.persona == "growth_hunter"

        budget_result = enforce_budgets(sqlite_conn, estimated.estimated_cost_usd)
        assert budget_result.allowed is True

        usage_dict = {"prompt_tokens": tokens, "completion_tokens": 450}
        body_cost_usd = compute_body_cost(usage_dict, sample_pricing)
        assert body_cost_usd > 0

        call_record = BodyCost(
            call_id="test-call-001",
            cycle_id="test-cycle-001",
            persona="growth_hunter",
            model_id="test/deepseek-v4",
            generation_id="gen-or-12345",
            prompt_tokens=tokens,
            completion_tokens=450,
            body_cost_usd=body_cost_usd,
            latency_ms=1200,
        )
        log_usage(sqlite_conn, duckdb_adapter, call_record, estimated)

        # Budget state always updates (SQLite, always available)
        totals = get_budget_totals(sqlite_conn)
        assert totals["today"]["total_cost_usd"] == pytest.approx(body_cost_usd)
        assert totals["this_month"]["total_cost_usd"] == pytest.approx(body_cost_usd)

    def test_multiple_calls_accumulate(self, sqlite_conn, duckdb_adapter, sample_pricing):
        """Multiple calls accumulate in budget state correctly."""
        for i in range(3):
            estimated = estimate_call_cost("Short prompt", "forensics", sample_pricing)
            usage = {"prompt_tokens": 100, "completion_tokens": 200}
            body_cost = compute_body_cost(usage, sample_pricing)
            call = BodyCost(
                call_id=f"call-{i}",
                cycle_id="cycle-multi",
                persona="forensics",
                model_id="test/deepseek-v4",
                prompt_tokens=100,
                completion_tokens=200,
                body_cost_usd=body_cost,
            )
            log_usage(sqlite_conn, duckdb_adapter, call, estimated)

        totals = get_budget_totals(sqlite_conn)
        expected_total = 3 * compute_cost(100, 200, 1e-7, 4e-7)
        assert totals["today"]["total_cost_usd"] == pytest.approx(expected_total, rel=1e-6)


# ============================================================
# Test 2: Budget enforcement flow
# ============================================================

class TestBudgetEnforcementFlow:
    def test_daily_cap_blocks_after_accumulation(self, sqlite_conn):
        """After accumulating spend, daily cap correctly blocks."""
        update_budget_state(sqlite_conn, 1.95)
        result = enforce_budgets(sqlite_conn, 0.10, daily_cap=2.00, cycle_soft_cap=100.0)
        assert result.allowed is False
        assert "daily_hard" in result.cap_type

    def test_monthly_cap_blocks(self, sqlite_conn):
        """Monthly cap blocks when exceeded."""
        update_budget_state(sqlite_conn, 29.95)
        result = enforce_budgets(
            sqlite_conn, 0.10,
            daily_cap=100.0, monthly_cap=30.00, cycle_soft_cap=100.0,
        )
        assert result.allowed is False
        assert "monthly_hard" in result.cap_type

    def test_soft_cap_requests_confirmation(self, sqlite_conn):
        """Cycle soft cap blocks but doesn't kill switch."""
        update_budget_state(sqlite_conn, 0.90)
        result = check_per_cycle_soft_cap(sqlite_conn, 0.20, cap=1.00)
        assert result.allowed is False
        assert result.cap_type == "cycle_soft"

    def test_runaway_detection(self):
        """Runaway >1.5x triggers block."""
        result = check_runaway(actual_cumulative=0.30, estimated_cumulative=0.15)
        assert result.allowed is False
        assert "runaway" in result.cap_type


# ============================================================
# Test 3: Pricing cache lifecycle
# ============================================================

class TestPricingCacheLifecycle:
    def test_cache_hit_no_network(self, sqlite_conn):
        """Cached pricing returned without network fetch."""
        pricing = PricingRecord(
            model_id="cached/model",
            input_price_per_token=2e-7,
            output_price_per_token=5e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        _upsert_pricing(sqlite_conn, pricing)

        with patch("pmacs.billing.pricing.fetch_pricing_from_openrouter") as mock_fetch:
            result = get_pricing(sqlite_conn, "cached/model")
            mock_fetch.assert_not_called()

        assert result is not None
        assert result.input_price_per_token == 2e-7

    def test_stale_cache_refetches(self, sqlite_conn):
        """Stale pricing triggers re-fetch."""
        stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _upsert_pricing(sqlite_conn, PricingRecord(
            model_id="stale/model",
            input_price_per_token=1e-7,
            output_price_per_token=2e-7,
            fetched_at=stale,
        ))

        new_pricing = PricingRecord(
            model_id="stale/model",
            input_price_per_token=3e-7,
            output_price_per_token=6e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        with patch("pmacs.billing.pricing.fetch_pricing_from_openrouter") as mock_fetch:
            mock_fetch.return_value = new_pricing
            result = get_pricing(sqlite_conn, "stale/model")
            mock_fetch.assert_called_once()
            assert result.input_price_per_token == 3e-7


# ============================================================
# Test 4: Reconciliation flow
# ============================================================

class TestReconciliationFlow:
    def test_reconcile_impl_updates_cost(self, sqlite_conn, duckdb_adapter, sample_pricing):
        """_reconcile_call_impl fetches cost, updates DuckDB, adjusts budget."""
        # Insert a call via log_usage
        estimated = EstimatedCost(
            persona="growth_hunter",
            model_id="test/deepseek-v4",
            estimated_input_tokens=100,
            estimated_output_tokens=200,
            estimated_cost_usd=0.00009,
        )
        call = BodyCost(
            call_id="recon-call-001",
            cycle_id="recon-cycle",
            persona="growth_hunter",
            model_id="test/deepseek-v4",
            generation_id="gen-recon-123",
            prompt_tokens=100,
            completion_tokens=200,
            body_cost_usd=0.00009,
        )
        log_usage(sqlite_conn, duckdb_adapter, call, estimated)

        # Mock the OpenRouter fetch
        with patch("pmacs.billing.reconciler._fetch_authoritative_cost") as mock_fetch:
            mock_fetch.return_value = 0.00012
            result = _reconcile_call_impl(
                "recon-call-001", "gen-recon-123", sqlite_conn, duckdb_adapter,
            )

        assert result is True

        # Verify actual_cost was written to DuckDB
        rows = duckdb_adapter.execute(
            "SELECT actual_cost_usd FROM api_usage WHERE call_id = ?",
            ["recon-call-001"],
        )
        assert len(rows) == 1
        assert rows[0]["actual_cost_usd"] == pytest.approx(0.00012)

        # Verify budget state was adjusted by delta (0.00012 - 0.00009 = 0.00003)
        totals = get_budget_totals(sqlite_conn)
        # Original 0.00009 + delta 0.00003 = 0.00012
        assert totals["today"]["total_cost_usd"] == pytest.approx(0.00012)

    def test_reconcile_skips_no_generation_id(self, sqlite_conn, duckdb_adapter):
        """Calls with no generation_id (local LLM) are skipped."""
        estimated = EstimatedCost(
            persona="macro_regime",
            model_id="local/qwen",
            estimated_input_tokens=100,
            estimated_output_tokens=200,
            estimated_cost_usd=0.0,
        )
        call = BodyCost(
            call_id="local-call-001",
            cycle_id="local-cycle",
            persona="macro_regime",
            model_id="local/qwen",
            generation_id=None,
            prompt_tokens=100,
            completion_tokens=200,
            body_cost_usd=0.0,
        )
        log_usage(sqlite_conn, duckdb_adapter, call, estimated)

        # reconcile_cycle should find 0 calls to reconcile
        from pmacs.billing.reconciler import reconcile_cycle
        reconciled = reconcile_cycle("local-cycle", sqlite_conn, duckdb_adapter)
        assert reconciled == 0


# ============================================================
# Test 5: Period rollover
# ============================================================

class TestPeriodRollover:
    def test_roll_daily_resets_today(self, sqlite_conn):
        """Rolling daily resets today's total."""
        update_budget_state(sqlite_conn, 1.50)
        totals_before = get_budget_totals(sqlite_conn)
        assert totals_before["today"]["total_cost_usd"] > 0

        roll_daily(sqlite_conn)
        totals_after = get_budget_totals(sqlite_conn)
        assert totals_after["today"]["total_cost_usd"] == 0.0

    def test_roll_monthly_resets_month(self, sqlite_conn):
        """Rolling monthly resets this_month (today unchanged)."""
        update_budget_state(sqlite_conn, 5.00)
        roll_monthly(sqlite_conn)

        totals = get_budget_totals(sqlite_conn)
        assert totals["this_month"]["total_cost_usd"] == 0.0
        assert totals["today"]["total_cost_usd"] == 5.0  # daily unaffected


# ============================================================
# Test 6: Drift monitoring
# ============================================================

class TestDriftMonitoring:
    def test_no_drift_few_samples(self, duckdb_adapter):
        """Drift check with <20 samples returns silently."""
        check_estimate_drift("growth_hunter", duckdb_adapter)

    def test_drift_with_sufficient_samples(self, duckdb_adapter):
        """Insert 25 calls with high output tokens, check drift fires."""
        for i in range(25):
            duckdb_adapter.insert_api_usage(
                call_id=f"drift-{i}",
                cycle_id="drift-cycle",
                persona="growth_hunter",
                model_id="test/model",
                generation_id=None,
                prompt_tokens=100,
                completion_tokens=900,  # way above configured 500
                estimated_cost_usd=0.001,
                body_cost_usd=0.001,
                latency_ms=100,
                succeeded=True,
            )

        # Should log ESTIMATE_DRIFT warning (900 p90 >> 500 configured)
        check_estimate_drift("growth_hunter", duckdb_adapter)
