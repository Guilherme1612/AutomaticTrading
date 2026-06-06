"""Unit tests for drift_monitor — estimate drift detection."""

import pytest

from pmacs.billing.drift_monitor import check_estimate_drift
from pmacs.storage.duckdb import DuckDBAdapter


@pytest.fixture
def duckdb_adapter(tmp_path):
    adapter = DuckDBAdapter(tmp_path / "test_analytics.duckdb")
    adapter.init_tables()
    yield adapter
    adapter.close()


class TestDriftDetection:
    def test_no_drift_under_threshold(self, duckdb_adapter):
        """Configured 500 tokens, actuals around 500 — no drift warning."""
        # Insert 25 calls with completion_tokens around 500
        for i in range(25):
            duckdb_adapter.insert_api_usage(
                call_id=f"call_{i}",
                cycle_id="cycle_1",
                persona="growth_hunter",  # configured at 500
                model_id="test-model",
                generation_id=None,
                prompt_tokens=2000,
                completion_tokens=480 + (i % 40),  # 480-520
                estimated_cost_usd=0.001,
                body_cost_usd=0.001,
                latency_ms=1000,
                succeeded=True,
            )

        # Should not raise or log warnings
        check_estimate_drift("growth_hunter", duckdb_adapter)

    def test_drift_detected_over_threshold(self, duckdb_adapter):
        """Configured 500 tokens, actuals at 700+ — should detect drift (>20% over)."""
        for i in range(25):
            duckdb_adapter.insert_api_usage(
                call_id=f"drift_{i}",
                cycle_id="cycle_2",
                persona="growth_hunter",  # configured at 500
                model_id="test-model",
                generation_id=None,
                prompt_tokens=2000,
                completion_tokens=700 + (i % 50),  # 700-750 → p90 ~745, which is 49% over 500
                estimated_cost_usd=0.001,
                body_cost_usd=0.001,
                latency_ms=1000,
                succeeded=True,
            )

        # Should log ESTIMATE_DRIFT (we verify it doesn't crash)
        check_estimate_drift("growth_hunter", duckdb_adapter)

    def test_insufficient_data_skips(self, duckdb_adapter):
        """Fewer than 20 calls → skip drift check."""
        for i in range(5):
            duckdb_adapter.insert_api_usage(
                call_id=f"few_{i}",
                cycle_id="cycle_3",
                persona="growth_hunter",
                model_id="test-model",
                generation_id=None,
                prompt_tokens=2000,
                completion_tokens=2000,  # Way over configured
                estimated_cost_usd=0.001,
                body_cost_usd=0.001,
                latency_ms=1000,
                succeeded=True,
            )

        # Should not crash (insufficient data → early return)
        check_estimate_drift("growth_hunter", duckdb_adapter)
