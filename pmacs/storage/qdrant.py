"""Qdrant vector adapter — stub for thesis/memo/lesson embedding operations.

Architecture.md §1.8: Both audit and debug logging required.
"""
from __future__ import annotations

import hashlib
from typing import Any

from pmacs.logsys import log_debug


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
        # Audit event — vector upsert (Architecture.md §1.8)
        log_debug(
            "QDRANT_UPSERT",
            payload={"collection": collection, "id": id},
            level="INFO",
            msg=f"Qdrant upsert: {collection}/{id}",
        )

    def search(self, collection: str, vector: list[float], limit: int = 5) -> list[dict]:
        """Search by vector similarity.  Stub."""
        # Debug event — search trace (Architecture.md §1.8)
        log_debug(
            "QDRANT_SEARCH",
            payload={"collection": collection, "limit": limit},
            level="DEBUG",
        )
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
        # Audit event — collections created (Architecture.md §1.8)
        log_debug(
            "QDRANT_COLLECTIONS_CREATED",
            payload={"collections": self.COLLECTIONS},
            level="INFO",
            msg=f"Qdrant collections created: {len(self.COLLECTIONS)} collections",
        )
