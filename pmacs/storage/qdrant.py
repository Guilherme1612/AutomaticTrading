"""Qdrant vector adapter — stub for thesis/memo/lesson embedding operations."""
from __future__ import annotations

import hashlib
from typing import Any


class QdrantAdapter:
    """Adapter for Qdrant vector operations.

    Stub for now — actual Qdrant connection requires the ``qdrant-client`` package
    and a running Qdrant instance.
    """

    COLLECTIONS = ["theses", "memos_persona", "memos_aggregated", "evidence_chunks", "lessons"]

    def __init__(self, url: str = "http://127.0.0.1:6333"):
        self.url = url

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        """Upsert a point.  Stub."""
        pass

    def search(self, collection: str, vector: list[float], limit: int = 5) -> list[dict]:
        """Search by vector similarity.  Stub."""
        return []

    def get_embedding(self, text: str) -> list[float]:
        """Generate embedding for text.

        Stub returns a deterministic hash-based dummy vector of length 8.
        This is **not** semantically meaningful — only useful for testing.
        """
        h = hashlib.sha256(text.encode()).hexdigest()
        return [float(int(h[i : i + 8], 16) % 1000) / 1000.0 for i in range(0, 64, 8)]

    def create_collections(self) -> None:
        """Create all required collections.  Stub."""
        pass
