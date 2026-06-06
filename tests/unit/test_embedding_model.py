"""Embedding model verification test (Architecture.md §8.7, Phase 8 item 8.12).

Verifies:
1. sentence-transformers library is importable
2. BAAI/bge-base-en-v1.5 produces 768-dim vectors
3. Embeddings are L2-normalized
4. Deterministic output for same input
5. QdrantAdapter.get_embedding() returns correct dimension
"""
from __future__ import annotations

import pytest


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("sentence_transformers"),
    reason="sentence-transformers not installed",
)
class TestEmbeddingModel:
    """Verify BAAI/bge-base-en-v1.5 embedding model."""

    @pytest.fixture(scope="class")
    def model(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer("BAAI/bge-base-en-v1.5")

    def test_embedding_dimension(self, model):
        """Embeddings must be 768-dimensional (Architecture.md §8.7)."""
        embedding = model.encode("test input", normalize_embeddings=True)
        assert len(embedding) == 768

    def test_embedding_normalized(self, model):
        """Embeddings must be L2-normalized (bge-base convention)."""
        embedding = model.encode("test input", normalize_embeddings=True)
        norm = (embedding ** 2).sum() ** 0.5
        assert abs(norm - 1.0) < 0.01

    def test_embedding_deterministic(self, model):
        """Same input must produce same embedding."""
        text = "PMACS deterministic test"
        e1 = model.encode(text, normalize_embeddings=True)
        e2 = model.encode(text, normalize_embeddings=True)
        assert (e1 == e2).all()

    def test_embedding_values_bounded(self, model):
        """Individual values must be in [-1, 1] for normalized embeddings."""
        embedding = model.encode("bounded value test", normalize_embeddings=True)
        assert embedding.min() >= -1.0
        assert embedding.max() <= 1.0

    def test_qdrant_adapter_get_embedding_dimension(self):
        """QdrantAdapter.get_embedding() must return 768-dim vector."""
        from pmacs.storage.qdrant import QdrantAdapter
        qa = QdrantAdapter()
        vec = qa.get_embedding("test embedding dimension")
        assert len(vec) == 768

    def test_qdrant_adapter_embedding_values(self):
        """QdrantAdapter.get_embedding() must return float values."""
        from pmacs.storage.qdrant import QdrantAdapter
        qa = QdrantAdapter()
        vec = qa.get_embedding("test values")
        assert all(isinstance(v, float) for v in vec)
