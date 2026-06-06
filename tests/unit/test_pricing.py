"""Unit tests for pricing module — cache, fetch, stale handling.

All network calls are mocked via httpx mocks — no real HTTP in CI.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from pmacs.billing.pricing import (
    fetch_pricing_from_openrouter,
    get_pricing,
    refresh_pricing_table,
    _upsert_pricing,
)
from pmacs.schemas.billing import PricingRecord
from pmacs.storage.sqlite import init_db


# -- Fixture --

@pytest.fixture
def sqlite_conn(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    yield conn
    conn.close()


# -- Mock helpers --

def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    """Build a fake httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("GET", "https://openrouter.ai/api/v1/models"),
    )


DEEPSEEK_RESPONSE = {
    "data": [
        {
            "id": "deepseek/deepseek-v4-flash",
            "pricing": {
                "prompt": "0.10",
                "completion": "0.40",
                "cache_read": "0.025",
            },
        },
        {
            "id": "other/model",
            "pricing": {"prompt": "0.50", "completion": "1.50"},
        },
    ]
}


# -- Tests --

class TestFetchPricing:
    @patch("pmacs.billing.pricing.httpx.Client")
    def test_fetch_success(self, mock_client_cls):
        """Successful fetch returns PricingRecord with correct per-token prices."""
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = lambda s, *a: None
        mock_client_cls.return_value.get.return_value = _mock_response(
            json_data=DEEPSEEK_RESPONSE,
        )

        result = fetch_pricing_from_openrouter("deepseek/deepseek-v4-flash")
        assert result is not None
        assert result.model_id == "deepseek/deepseek-v4-flash"
        assert result.input_price_per_token == pytest.approx(0.10 / 1_000_000)
        assert result.output_price_per_token == pytest.approx(0.40 / 1_000_000)
        assert result.cached_input_price_per_token == pytest.approx(0.025 / 1_000_000)

    @patch("pmacs.billing.pricing.httpx.Client")
    def test_fetch_model_not_found(self, mock_client_cls):
        """Model not in response returns None."""
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = lambda s, *a: None
        mock_client_cls.return_value.get.return_value = _mock_response(
            json_data=DEEPSEEK_RESPONSE,
        )

        result = fetch_pricing_from_openrouter("nonexistent-model")
        assert result is None

    @patch("pmacs.billing.pricing.httpx.Client")
    def test_fetch_network_error(self, mock_client_cls):
        """Network error returns None without raising."""
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = lambda s, *a: None
        mock_client_cls.return_value.get.side_effect = httpx.ConnectError("timeout")

        result = fetch_pricing_from_openrouter("any/model")
        assert result is None

    @patch("pmacs.billing.pricing.httpx.Client")
    def test_fetch_http_error(self, mock_client_cls):
        """HTTP 500 returns None."""
        mock_client_cls.return_value.__enter__ = lambda s: s
        mock_client_cls.return_value.__exit__ = lambda s, *a: None
        mock_client_cls.return_value.get.return_value = _mock_response(status_code=500)

        result = fetch_pricing_from_openrouter("any/model")
        assert result is None


class TestPricingCache:
    def test_cache_hit_returns_cached(self, sqlite_conn):
        """Cached pricing returned without network fetch."""
        pricing = PricingRecord(
            model_id="test/model",
            input_price_per_token=1e-7,
            output_price_per_token=2e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        _upsert_pricing(sqlite_conn, pricing)

        with patch("pmacs.billing.pricing.fetch_pricing_from_openrouter") as mock_fetch:
            result = get_pricing(sqlite_conn, "test/model")
            mock_fetch.assert_not_called()

        assert result is not None
        assert result.model_id == "test/model"

    @patch("pmacs.billing.pricing.fetch_pricing_from_openrouter")
    def test_cache_miss_fetches(self, mock_fetch, sqlite_conn):
        """Cache miss triggers fetch and caches result."""
        mock_fetch.return_value = PricingRecord(
            model_id="deepseek/deepseek-v4-flash",
            input_price_per_token=1e-7,
            output_price_per_token=4e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        result = get_pricing(sqlite_conn, "deepseek/deepseek-v4-flash")
        mock_fetch.assert_called_once_with("deepseek/deepseek-v4-flash")
        assert result is not None
        assert result.input_price_per_token == 1e-7

        # Second call should hit cache
        mock_fetch.reset_mock()
        result2 = get_pricing(sqlite_conn, "deepseek/deepseek-v4-flash")
        mock_fetch.assert_not_called()
        assert result2.model_id == "deepseek/deepseek-v4-flash"

    @patch("pmacs.billing.pricing.fetch_pricing_from_openrouter")
    def test_cache_stale_refetches(self, mock_fetch, sqlite_conn):
        """Stale cache (>24h) triggers re-fetch."""
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        pricing = PricingRecord(
            model_id="test/model",
            input_price_per_token=1e-7,
            output_price_per_token=2e-7,
            fetched_at=stale_time,
        )
        _upsert_pricing(sqlite_conn, pricing)

        mock_fetch.return_value = PricingRecord(
            model_id="test/model",
            input_price_per_token=3e-7,
            output_price_per_token=4e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        result = get_pricing(sqlite_conn, "test/model")
        mock_fetch.assert_called_once()
        assert result.input_price_per_token == 3e-7

    @patch("pmacs.billing.pricing.fetch_pricing_from_openrouter")
    def test_fetch_failure_uses_stale_cache(self, mock_fetch, sqlite_conn):
        """Fetch failure falls back to stale cache."""
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        pricing = PricingRecord(
            model_id="test/model",
            input_price_per_token=1e-7,
            output_price_per_token=2e-7,
            fetched_at=stale_time,
        )
        _upsert_pricing(sqlite_conn, pricing)
        mock_fetch.return_value = None

        result = get_pricing(sqlite_conn, "test/model")
        assert result is not None
        assert result.input_price_per_token == 1e-7  # stale but usable

    @patch("pmacs.billing.pricing.fetch_pricing_from_openrouter")
    def test_fetch_failure_no_cache_returns_none(self, mock_fetch, sqlite_conn):
        """No cache + fetch failure returns None."""
        mock_fetch.return_value = None
        result = get_pricing(sqlite_conn, "nonexistent-model")
        assert result is None


class TestUpsertPricing:
    def test_upsert_and_retrieve(self, sqlite_conn):
        pricing = PricingRecord(
            model_id="test/model",
            input_price_per_token=1e-7,
            output_price_per_token=2e-7,
            fetched_at="2026-05-24T00:00:00Z",
        )
        _upsert_pricing(sqlite_conn, pricing)

        row = sqlite_conn.execute(
            "SELECT model_id FROM pricing_table WHERE model_id = ?",
            ["test/model"],
        ).fetchone()
        assert row is not None
        assert row[0] == "test/model"

    def test_upsert_updates_existing(self, sqlite_conn):
        p1 = PricingRecord(
            model_id="test/model",
            input_price_per_token=1e-7,
            output_price_per_token=2e-7,
            fetched_at="2026-01-01T00:00:00Z",
        )
        _upsert_pricing(sqlite_conn, p1)

        p2 = PricingRecord(
            model_id="test/model",
            input_price_per_token=3e-7,
            output_price_per_token=4e-7,
            fetched_at="2026-06-01T00:00:00Z",
        )
        _upsert_pricing(sqlite_conn, p2)

        row = sqlite_conn.execute(
            "SELECT input_price_per_token FROM pricing_table WHERE model_id = ?",
            ["test/model"],
        ).fetchone()
        assert row[0] == 3e-7


class TestRefreshPricingTable:
    @patch("pmacs.billing.pricing.fetch_pricing_from_openrouter")
    def test_refresh_specific_model(self, mock_fetch, sqlite_conn):
        mock_fetch.return_value = PricingRecord(
            model_id="test/model",
            input_price_per_token=5e-7,
            output_price_per_token=6e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        refresh_pricing_table(sqlite_conn, model_id="test/model")
        mock_fetch.assert_called_once_with("test/model")

    @patch("pmacs.billing.pricing.fetch_pricing_from_openrouter")
    def test_refresh_all_cached(self, mock_fetch, sqlite_conn):
        """Refreshes all models in pricing_table."""
        for mid in ["a/model", "b/model"]:
            _upsert_pricing(sqlite_conn, PricingRecord(
                model_id=mid,
                input_price_per_token=1e-7,
                output_price_per_token=2e-7,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            ))

        mock_fetch.return_value = PricingRecord(
            model_id="x",
            input_price_per_token=1e-7,
            output_price_per_token=2e-7,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

        refresh_pricing_table(sqlite_conn)
        assert mock_fetch.call_count == 2


class TestBudgetStateSeed:
    def test_budget_state_seeded(self, sqlite_conn):
        rows = sqlite_conn.execute("SELECT period FROM budget_state").fetchall()
        periods = {r[0] for r in rows}
        assert "today" in periods
        assert "this_month" in periods

    def test_budget_state_defaults(self, sqlite_conn):
        today = sqlite_conn.execute(
            "SELECT cap_usd FROM budget_state WHERE period = 'today'"
        ).fetchone()
        assert today[0] == 2.00

        month = sqlite_conn.execute(
            "SELECT cap_usd FROM budget_state WHERE period = 'this_month'"
        ).fetchone()
        assert month[0] == 30.00
