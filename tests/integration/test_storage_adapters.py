"""Integration tests for Phase 11 storage adapters (DuckDB, Qdrant, KuzuDB)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pmacs.storage.kuzu import KuzuDBAdapter
from pmacs.storage.qdrant import QdrantAdapter
from pmacs.storage.duckdb import DuckDBAdapter


# ======================================================================
# DuckDB adapter
# ======================================================================

class TestDuckDBAdapter:
    @pytest.fixture()
    def duckdb(self, tmp_path: Path):
        adapter = DuckDBAdapter(db_path=tmp_path / "test_analytics.duckdb")
        adapter.init_tables()
        return adapter

    def test_init_tables_creates_tables(self, duckdb: DuckDBAdapter):
        tables = duckdb.execute("SHOW TABLES")
        table_names = [t["name"] for t in tables]
        assert "rolling_metrics" in table_names
        assert "persona_performance" in table_names
        assert "persona_ticker_affinity" in table_names
        assert "failure_taxonomy_counts" in table_names

    def test_insert_and_query_rolling_metrics(self, duckdb: DuckDBAdapter):
        duckdb.execute(
            "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value) VALUES (?, ?, ?)",
            ["c001", "brier", 0.25],
        )
        rows = duckdb.execute("SELECT * FROM rolling_metrics")
        assert len(rows) == 1
        assert rows[0]["cycle_id"] == "c001"
        assert rows[0]["metric_name"] == "brier"
        assert rows[0]["metric_value"] == pytest.approx(0.25)

    def test_update_persona_affinity_insert(self, duckdb: DuckDBAdapter):
        duckdb.update_persona_affinity("macro", "AAPL", 0.3)
        rows = duckdb.execute("SELECT * FROM persona_ticker_affinity")
        assert len(rows) == 1
        assert rows[0]["persona"] == "macro"
        assert rows[0]["ticker"] == "AAPL"
        assert rows[0]["avg_brier"] == pytest.approx(0.3)
        assert rows[0]["cycle_count"] == 1

    def test_update_persona_affinity_upsert(self, duckdb: DuckDBAdapter):
        duckdb.update_persona_affinity("macro", "AAPL", 0.3)
        duckdb.update_persona_affinity("macro", "AAPL", 0.5)
        rows = duckdb.execute("SELECT * FROM persona_ticker_affinity")
        assert len(rows) == 1
        # (0.3 * 1 + 0.5) / 2 = 0.4
        assert rows[0]["avg_brier"] == pytest.approx(0.4)
        assert rows[0]["cycle_count"] == 2

    def test_execute_returns_list_of_dicts(self, duckdb: DuckDBAdapter):
        duckdb.update_persona_affinity("sector", "MSFT", 0.2)
        result = duckdb.execute("SELECT persona, ticker FROM persona_ticker_affinity")
        assert isinstance(result, list)
        assert isinstance(result[0], dict)


# ======================================================================
# Qdrant adapter (stub)
# ======================================================================

class TestQdrantAdapter:
    def test_get_embedding_deterministic(self):
        qa = QdrantAdapter()
        v1 = qa.get_embedding("test text")
        v2 = qa.get_embedding("test text")
        assert v1 == v2

    def test_get_embedding_different_text(self):
        qa = QdrantAdapter()
        v1 = qa.get_embedding("text a")
        v2 = qa.get_embedding("text b")
        assert v1 != v2

    def test_get_embedding_length(self):
        qa = QdrantAdapter()
        vec = qa.get_embedding("hello")
        assert len(vec) == 768  # bge-base-en-v1.5 dimension

    def test_get_embedding_values_in_range(self):
        qa = QdrantAdapter()
        vec = qa.get_embedding("any text")
        assert all(-1.0 <= v <= 1.0 for v in vec)  # cosine-normalized

    def test_search_returns_empty_list(self):
        qa = QdrantAdapter()
        results = qa.search("theses", [0.1] * 8)
        assert results == []

    def test_upsert_no_error(self):
        qa = QdrantAdapter()
        qa.upsert("theses", "id1", [0.1] * 8, {"text": "test"})

    def test_create_collections_no_error(self):
        qa = QdrantAdapter()
        qa.create_collections()

    def test_collections_defined(self):
        assert len(QdrantAdapter.COLLECTIONS) == 5
        assert "theses" in QdrantAdapter.COLLECTIONS
        assert "lessons" in QdrantAdapter.COLLECTIONS


# ======================================================================
# KuzuDB adapter (stub)
# ======================================================================

class TestKuzuDBAdapter:
    def test_execute_returns_empty(self):
        kuzu = KuzuDBAdapter()
        result = kuzu.execute("MATCH (n) RETURN n")
        assert result == []

    def test_write_failed_assumption_no_error(self):
        kuzu = KuzuDBAdapter()
        kuzu.write_failed_assumption(
            fa_id="fa001",
            taxonomy="CATALYST_MISMATCH",
            severity=0.8,
            holding_id="h001",
            cycle_id="c001",
            summary="Earnings miss",
        )

    def test_get_failures_for_ticker_returns_empty(self):
        kuzu = KuzuDBAdapter()
        result = kuzu.get_failures_for_ticker("AAPL")
        assert result == []

    def test_get_lineage_returns_empty(self):
        kuzu = KuzuDBAdapter()
        result = kuzu.get_lineage("h001")
        assert result == {}
