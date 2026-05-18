"""Integration tests for Qdrant vector adapter (stub mode).

Validates instantiation, method signatures, embedding consistency,
collection creation, and logging emission.
"""
from __future__ import annotations

import inspect
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pmacs.storage.qdrant import QdrantAdapter


def _reset_log_fd():
    """Close and reset the module-level log file descriptor so a new path takes effect."""
    import pmacs.logsys.debug_log as _mod
    if _mod._log_fd is not None:
        _mod._log_fd.close()
        _mod._log_fd = None


# ======================================================================
# Instantiation
# ======================================================================

class TestQdrantInstantiation:
    def test_default_instantiation(self) -> None:
        """QdrantAdapter can be created with no arguments."""
        qa = QdrantAdapter()
        assert qa.url == "http://127.0.0.1:6333"

    def test_custom_url(self) -> None:
        """QdrantAdapter accepts custom URL."""
        qa = QdrantAdapter(url="http://custom:9999")
        assert qa.url == "http://custom:9999"


# ======================================================================
# Method existence and parameter signatures
# ======================================================================

class TestQdrantMethodSignatures:
    def test_upsert_signature(self) -> None:
        """upsert() accepts collection, id, vector, payload."""
        sig = inspect.signature(QdrantAdapter.upsert)
        params = list(sig.parameters.keys())
        assert "collection" in params
        assert "id" in params
        assert "vector" in params
        assert "payload" in params

    def test_search_signature(self) -> None:
        """search() accepts collection, vector, limit."""
        sig = inspect.signature(QdrantAdapter.search)
        params = list(sig.parameters.keys())
        assert "collection" in params
        assert "vector" in params
        assert "limit" in params

    def test_create_collections_exists(self) -> None:
        """create_collections() method exists."""
        assert hasattr(QdrantAdapter, "create_collections")
        assert callable(getattr(QdrantAdapter, "create_collections"))

    def test_upsert_executes_without_error(self) -> None:
        """upsert() works in stub mode without raising."""
        qa = QdrantAdapter()
        qa.upsert("theses", "id-001", [0.1] * 8, {"text": "test thesis"})

    def test_search_returns_list(self) -> None:
        """search() returns a list in stub mode."""
        qa = QdrantAdapter()
        results = qa.search("theses", [0.1] * 8, limit=5)
        assert isinstance(results, list)
        assert results == []  # stub mode returns empty

    def test_create_collections_no_error(self) -> None:
        """create_collections() runs in stub mode."""
        qa = QdrantAdapter()
        qa.create_collections()


# ======================================================================
# Embedding consistency
# ======================================================================

class TestQdrantEmbedding:
    def test_get_embedding_consistent_length(self) -> None:
        """get_embedding() returns vectors of consistent length."""
        qa = QdrantAdapter()
        lengths = {len(qa.get_embedding(t)) for t in ["a", "bb", "ccc", "longer text here"]}
        assert len(lengths) == 1, f"Varying lengths: {lengths}"

    def test_get_embedding_deterministic(self) -> None:
        """Same text always produces same embedding."""
        qa = QdrantAdapter()
        v1 = qa.get_embedding("deterministic test")
        v2 = qa.get_embedding("deterministic test")
        assert v1 == v2

    def test_get_embedding_different_for_different_text(self) -> None:
        """Different text produces different embeddings."""
        qa = QdrantAdapter()
        v1 = qa.get_embedding("text one")
        v2 = qa.get_embedding("text two")
        assert v1 != v2

    def test_get_embedding_values_normalized(self) -> None:
        """Embedding values are in [-1, 1] range (cosine-normalized)."""
        qa = QdrantAdapter()
        vec = qa.get_embedding("any input text")
        assert all(-1.0 <= v <= 1.0 for v in vec)


# ======================================================================
# Logging emission
# ======================================================================

class TestQdrantLogging:
    def test_upsert_emits_log(self, tmp_path: Path) -> None:
        """upsert() emits a QDRANT_UPSERT log event (or QDRANT_CONNECTION_FAILED if server is down)."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "test_upsert.log"
        set_log_path(str(log_file))

        qa = QdrantAdapter()
        qa.upsert("theses", "id-log-test", [0.1] * 8, {"text": "logging test"})

        _reset_log_fd()  # close FD so we can read
        content = log_file.read_text()
        assert "QDRANT_UPSERT" in content or "QDRANT_CONNECTION_FAILED" in content
        # Verify structured JSON
        for line in content.strip().split("\n"):
            entry = json.loads(line)
            assert "event" in entry
            assert entry["event"] in ("QDRANT_UPSERT", "QDRANT_CONNECTION_FAILED")

    def test_search_emits_log(self, tmp_path: Path) -> None:
        """search() emits a QDRANT_SEARCH log event (or QDRANT_CONNECTION_FAILED if server is down)."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "test_search.log"
        set_log_path(str(log_file))

        qa = QdrantAdapter()
        qa.search("theses", [0.1] * 8, limit=3)

        _reset_log_fd()
        content = log_file.read_text()
        assert "QDRANT_SEARCH" in content or "QDRANT_CONNECTION_FAILED" in content

    def test_create_collections_emits_log(self, tmp_path: Path) -> None:
        """create_collections() emits a QDRANT_COLLECTIONS_CREATED log event (or QDRANT_CONNECTION_FAILED if server is down)."""
        from pmacs.logsys.debug_log import set_log_path
        _reset_log_fd()
        log_file = tmp_path / "test_collections.log"
        set_log_path(str(log_file))

        qa = QdrantAdapter()
        qa.create_collections()

        _reset_log_fd()
        content = log_file.read_text()
        assert "QDRANT_COLLECTIONS_CREATED" in content or "QDRANT_CONNECTION_FAILED" in content

    def test_collections_list_correct(self) -> None:
        """COLLECTIONS class attribute has 5 expected collections."""
        expected = ["theses", "memos_persona", "memos_aggregated", "evidence_chunks", "lessons"]
        assert QdrantAdapter.COLLECTIONS == expected
