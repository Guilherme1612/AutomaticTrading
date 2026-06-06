"""Performance profiling test (Architecture.md §20).

Runs component-level profiling and verifies all operations are within budget.
"""
from __future__ import annotations

import pytest

from ops.profile_system import (
    profile_engines,
    profile_sqlite,
    profile_storage_adapters,
    ProfileResult,
)


class TestSystemProfile:
    """Verify all PMACS subsystems meet performance budgets."""

    @pytest.fixture(scope="class")
    def sqlite_results(self):
        return profile_sqlite()

    @pytest.fixture(scope="class")
    def storage_results(self):
        return profile_storage_adapters()

    @pytest.fixture(scope="class")
    def engine_results(self):
        return profile_engines()

    def test_sqlite_write_throughput(self, sqlite_results):
        """SQLite must write 1K rows in under 5s."""
        r = _find(sqlite_results, "write_1k_rows")
        assert r is not None, "SQLite write benchmark missing"
        assert r.pass_, f"SQLite write too slow: {r.total_ms:.0f}ms > {r.budget_ms:.0f}ms"

    def test_sqlite_read_throughput(self, sqlite_results):
        """SQLite must read 1K queries in under 3s."""
        r = _find(sqlite_results, "read_1k_queries")
        assert r is not None, "SQLite read benchmark missing"
        assert r.pass_, f"SQLite read too slow: {r.total_ms:.0f}ms > {r.budget_ms:.0f}ms"

    def test_sqlite_audit_throughput(self, sqlite_results):
        """SQLite audit writes must complete 100 in under 1s."""
        r = _find(sqlite_results, "audit_100_writes")
        assert r is not None, "SQLite audit benchmark missing"
        assert r.pass_, f"Audit write too slow: {r.total_ms:.0f}ms > {r.budget_ms:.0f}ms"

    def test_qdrant_embedding_generation(self, storage_results):
        """Qdrant embedding generation must be within budget."""
        r = _find(storage_results, "embedding_generation")
        assert r is not None, "Qdrant embedding benchmark missing"
        assert r.pass_, f"Embedding generation too slow: {r.total_ms:.0f}ms > {r.budget_ms:.0f}ms ({r.notes})"

    def test_kuzu_stub_throughput(self, storage_results):
        """KuzuDB stub writes must be fast."""
        r = _find(storage_results, "failed_assumption_write_stub")
        assert r is not None, "KuzuDB benchmark missing"
        assert r.pass_, f"KuzuDB stub too slow: {r.total_ms:.0f}ms"

    def test_duckdb_init(self, storage_results):
        """DuckDB table creation must be under 5s."""
        r = _find(storage_results, "init_tables")
        assert r is not None, "DuckDB benchmark missing"
        assert r.pass_, f"DuckDB init too slow: {r.total_ms:.0f}ms"

    def test_conviction_engine(self, engine_results):
        """Conviction computation must be fast."""
        r = _find(engine_results, "compute_conviction_1k")
        assert r is not None, "Conviction benchmark missing"
        assert r.pass_, f"Conviction too slow: {r.total_ms:.0f}ms"

    def test_pricing_engine(self, engine_results):
        """EV computation must be fast."""
        r = _find(engine_results, "compute_ev_500")
        assert r is not None, "Pricing benchmark missing"
        assert r.pass_, f"Pricing too slow: {r.total_ms:.0f}ms"


def _find(results: list[ProfileResult], operation: str) -> ProfileResult | None:
    for r in results:
        if r.operation == operation:
            return r
    return None
