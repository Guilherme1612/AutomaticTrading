"""Integration tests for Phase 11 storage adapters (DuckDB, Qdrant, KuzuDB)."""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pytest

from pmacs.storage.kuzu import KuzuDBAdapter
from pmacs.storage.qdrant import QdrantAdapter

_HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None
_HAS_SENTENCE_TRANSFORMERS = importlib.util.find_spec("sentence_transformers") is not None


@pytest.mark.skipif(not _HAS_DUCKDB, reason="duckdb not installed")
class TestDuckDBAdapter:
    @pytest.fixture()
    def duckdb(self, tmp_path: Path):
        from pmacs.storage.duckdb import DuckDBAdapter

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
# DuckDB adapter (stub mode — graceful degradation without duckdb)
# ======================================================================

class TestDuckDBAdapterStub:
    """Tests that DuckDB adapter gracefully degrades when duckdb is not installed.

    These always run (not skipped) — they verify the stub path works.
    When duckdb IS installed, the adapter connects for real so these tests
    validate the real path instead.
    """

    def test_init_tables_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.init_tables()  # must not raise

    def test_execute_returns_empty_or_results(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        result = adapter.execute("SELECT 1")
        # Either empty list (stub) or list with one row (real)
        assert isinstance(result, list)

    def test_update_persona_affinity_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.update_persona_affinity("macro", "AAPL", 0.3)  # must not raise

    def test_insert_resolution_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.insert_resolution(
            resolution_id="r001",
            holding_id="h001",
            ticker="AAPL",
            catalyst_type="earnings",
            direction="UP",
            expected_move_pct=5.0,
            actual_move_pct=3.0,
            resolution_quality="PARTIAL",
            cycle_id="c001",
        )

    def test_insert_rolling_metric_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.insert_rolling_metric("c001", "brier", 0.25)

    def test_insert_persona_performance_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.insert_persona_performance(
            persona="macro",
            cycle_id="c001",
            ticker="AAPL",
            p_up=0.6,
            p_flat=0.2,
            p_down=0.2,
            brier=0.15,
            direction_correct=True,
        )

    def test_insert_failure_taxonomy_count_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.insert_failure_taxonomy_count(
            taxonomy="CATALYST_MISMATCH",
            cycle_id="c001",
            window_start="2025-01-01",
            window_end="2025-01-31",
        )

    def test_archive_evidence_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.archive_evidence(
            evidence_id="ev001",
            ticker="AAPL",
            source="sec",
            content_hash="abc123",
            data_json='{"key": "value"}',
        )

    def test_insert_scan_record_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.insert_scan_record(
            cycle_id="c001",
            ticker="AAPL",
            source_count=5,
            evidence_count=12,
            has_stale_data=False,
        )

    def test_update_subsector_affinity_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.update_subsector_affinity("sector", "semiconductor", 0.3)

    def test_close_no_error(self):
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter()
        adapter.close()  # must not raise


# ======================================================================
# Qdrant adapter (stub)
# ======================================================================

class TestQdrantAdapter:
    @pytest.mark.skipif(not _HAS_SENTENCE_TRANSFORMERS, reason="sentence-transformers not installed")
    def test_get_embedding_deterministic(self):
        qa = QdrantAdapter()
        v1 = qa.get_embedding("test text")
        v2 = qa.get_embedding("test text")
        assert v1 == v2

    @pytest.mark.skipif(not _HAS_SENTENCE_TRANSFORMERS, reason="sentence-transformers not installed")
    def test_get_embedding_different_text(self):
        qa = QdrantAdapter()
        v1 = qa.get_embedding("text a")
        v2 = qa.get_embedding("text b")
        assert v1 != v2

    @pytest.mark.skipif(not _HAS_SENTENCE_TRANSFORMERS, reason="sentence-transformers not installed")
    def test_get_embedding_length(self):
        qa = QdrantAdapter()
        vec = qa.get_embedding("hello")
        assert len(vec) == 768  # bge-base-en-v1.5 dimension

    @pytest.mark.skipif(not _HAS_SENTENCE_TRANSFORMERS, reason="sentence-transformers not installed")
    def test_get_embedding_values_in_range(self):
        qa = QdrantAdapter()
        vec = qa.get_embedding("any text")
        assert all(-1.0 <= v <= 1.0 for v in vec)  # cosine-normalized

    def test_search_returns_empty_list(self):
        qa = QdrantAdapter()
        results = qa.search("theses", [0.1] * 768)
        assert results == []

    def test_upsert_no_error(self):
        qa = QdrantAdapter()
        qa.upsert("theses", "id1", [0.1] * 768, {"text": "test"})

    def test_create_collections_no_error(self):
        qa = QdrantAdapter()
        qa.create_collections()

    def test_collections_defined(self):
        assert len(QdrantAdapter.COLLECTIONS) == 6
        assert "theses" in QdrantAdapter.COLLECTIONS
        assert "lessons" in QdrantAdapter.COLLECTIONS
        assert "episodic" in QdrantAdapter.COLLECTIONS


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
