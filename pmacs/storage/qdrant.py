"""Qdrant vector adapter — thesis/memo/lesson embedding operations (Architecture.md §8.7).

Gracefully degrades when ``qdrant-client`` or ``sentence-transformers`` is not
installed, or when the Qdrant server is unavailable.  All public methods return
empty/default values in stub mode.

Architecture.md §1.8: Both audit and debug logging required.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from pmacs.logsys import log_debug

_EMBEDDING_DIM = 768  # bge-base-en-v1.5 dimension (Architecture.md §8.7)

# Lazy singletons — import availability is global, connection is per-instance
_qdrant_client: Any = None
_sentence_model: Any = None
_qdrant_import_available: bool | None = None
_transformers_available: bool | None = None


class QdrantAdapter:
    """Adapter for Qdrant vector operations (Architecture.md §8.7).

    Collections: theses, memos_persona, memos_aggregated, evidence_chunks, lessons.
    All use 768-dim vectors from BAAI/bge-base-en-v1.5.
    """

    COLLECTIONS = [
        "theses",
        "memos_persona",
        "memos_aggregated",
        "evidence_chunks",
        "lessons",
        "episodic",
    ]

    def __init__(
        self,
        url: str | None = None,
        path: str | None = None,
    ):
        """Initialize Qdrant adapter.

        Args:
            url: Qdrant server HTTP URL (e.g. "http://127.0.0.1:6333").
                 If None and path is None, falls back to in-memory.
            path: Persistent embedded mode path (no server needed).
                  Mutually exclusive with url — if both are set, path wins.
        """
        self.url = url
        self._path = path
        self._client: Any = None
        self._collections_created = False
        self._connection_failed: bool = False  # per-instance failure tracking

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _ensure_client(self) -> bool:
        """Try to connect to Qdrant. Returns True if connected."""
        global _qdrant_client, _qdrant_import_available

        # Import availability is global (package either installed or not)
        if _qdrant_import_available is False:
            return False
        # Connection failure is per-instance (different url/path may work)
        if self._connection_failed:
            return False
        if self._client is not None:
            return True

        try:
            from qdrant_client import QdrantClient  # type: ignore[import-untyped]
            _qdrant_import_available = True
        except ImportError:
            _qdrant_import_available = False
            log_debug(
                "QDRANT_UNAVAILABLE",
                payload={"reason": "qdrant-client not installed"},
                level="INFO",
                msg="Qdrant adapter running in stub mode (qdrant-client not installed)",
            )
            return False

        try:
            # Embedded persistent mode (no server needed)
            if self._path is not None:
                self._client = QdrantClient(path=str(self._path))
            # HTTP/S mode (remote or local Docker server)
            elif self.url is not None:
                self._client = QdrantClient(url=self.url)
            # Fallback: in-memory (data lost on restart)
            else:
                self._client = QdrantClient(":memory:")
            # Quick health check
            self._client.get_collections()
            return True
        except Exception as exc:
            log_debug(
                "QDRANT_CONNECTION_FAILED",
                payload={"error": str(exc)},
                level="WARN",
                error_code="QDRANT_CONNECTION_FAILED",
                msg=f"Qdrant connection failed: {exc}",
            )
            self._client = None
            self._connection_failed = True  # per-instance, not global
            return False

    def _ensure_model(self) -> bool:
        """Try to load sentence-transformers model. Returns True if loaded."""
        global _sentence_model, _transformers_available

        if _transformers_available is False:
            return False
        if _sentence_model is not None:
            return True

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
            _transformers_available = True
            _sentence_model = SentenceTransformer("BAAI/bge-base-en-v1.5")
            return True
        except ImportError:
            _transformers_available = False
            log_debug(
                "QDRANT_NO_EMBEDDINGS",
                payload={"reason": "sentence-transformers not installed"},
                level="INFO",
                msg="Embedding model unavailable (sentence-transformers not installed)",
            )
            return False
        except Exception as exc:
            log_debug(
                "QDRANT_MODEL_LOAD_FAILED",
                payload={"error": str(exc)},
                level="WARN",
                error_code="QDRANT_MODEL_LOAD_FAILED",
                msg=f"Embedding model load failed: {exc}",
            )
            return False

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def create_collections(self) -> None:
        """Create all required collections (Architecture.md §8.7)."""
        if not self._ensure_client():
            log_debug(
                "QDRANT_COLLECTIONS_CREATED",
                payload={"collections": self.COLLECTIONS, "stub": True},
                level="INFO",
                msg=f"Qdrant collections stub-logged (no server): {len(self.COLLECTIONS)} collections",
            )
            return

        from qdrant_client.models import Distance, VectorParams

        for name in self.COLLECTIONS:
            try:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=_EMBEDDING_DIM,
                        distance=Distance.COSINE,
                    ),
                )
            except Exception as exc:
                # Collection may already exist — qdrant raises on duplicate create
                err_str = str(exc).lower()
                if "already exists" not in err_str:
                    log_debug(
                        "QDRANT_CREATE_COLLECTION_FAILED",
                        payload={"collection": name, "error": str(exc)},
                        level="WARN",
                        error_code="QDRANT_CREATE_FAILED",
                        msg=f"Qdrant create_collection failed for {name}: {exc}",
                    )

        self._collections_created = True
        log_debug(
            "QDRANT_COLLECTIONS_CREATED",
            payload={"collections": self.COLLECTIONS},
            level="INFO",
            msg=f"Qdrant collections created: {len(self.COLLECTIONS)} collections",
        )

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        """Upsert a point into a collection."""
        if not self._ensure_client():
            return

        log_debug(
            "QDRANT_UPSERT",
            payload={"collection": collection, "id": id},
            level="INFO",
            msg=f"Qdrant upsert: {collection}/{id}",
        )

        try:
            from qdrant_client.models import PointStruct

            # Ensure collection exists
            if not self._collections_created:
                self.create_collections()

            self._client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, id)),
                        vector=vector,
                        payload=payload,
                    )
                ],
            )
        except Exception as exc:
            log_debug(
                "QDRANT_UPSERT_FAILED",
                payload={"error": str(exc), "collection": collection, "id": id},
                level="WARN",
                error_code="QDRANT_WRITE_FAILED",
                msg=f"Qdrant upsert failed: {exc}",
            )

    def search(self, collection: str, vector: list[float], limit: int = 5) -> list[dict]:
        """Search by vector similarity."""
        log_debug(
            "QDRANT_SEARCH",
            payload={"collection": collection, "limit": limit},
            level="DEBUG",
        )

        if not self._ensure_client():
            return []

        try:
            hits = self._client.query_points(
                collection_name=collection,
                query=vector,
                limit=limit,
            )
            return [
                {"id": str(hit.id), "score": hit.score, "payload": hit.payload or {}}
                for hit in hits.points
            ]
        except Exception as exc:
            log_debug(
                "QDRANT_SEARCH_FAILED",
                payload={"error": str(exc), "collection": collection},
                level="WARN",
                error_code="QDRANT_QUERY_FAILED",
                msg=f"Qdrant search failed: {exc}",
            )
            return []

    def retrieve(self, collection_name: str, ids: list[str]) -> list[Any]:
        """Retrieve points by ID from a collection (for consistency checks)."""
        if not self._ensure_client():
            return []

        try:
            # Convert string IDs to UUID strings (same mapping as upsert)
            uuid_ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, id_)) for id_ in ids]
            return self._client.retrieve(
                collection_name=collection_name,
                ids=uuid_ids,
            )
        except Exception as exc:
            log_debug(
                "QDRANT_RETRIEVE_FAILED",
                payload={"error": str(exc), "collection": collection_name},
                level="WARN",
                error_code="QDRANT_QUERY_FAILED",
                msg=f"Qdrant retrieve failed: {exc}",
            )
            return []

    def count(self, collection: str) -> int:
        """Return the number of points in a collection."""
        if not self._ensure_client():
            return 0

        try:
            info = self._client.get_collection(collection_name=collection)
            return info.points_count if info else 0
        except Exception as exc:
            log_debug(
                "QDRANT_COUNT_FAILED",
                payload={"error": str(exc), "collection": collection},
                level="WARN",
                error_code="QDRANT_QUERY_FAILED",
                msg=f"Qdrant count failed: {exc}",
            )
            return 0

    def delete(self, collection: str, ids: list[str]) -> None:
        """Delete points by ID from a collection."""
        if not self._ensure_client():
            return

        log_debug(
            "QDRANT_DELETE",
            payload={"collection": collection, "count": len(ids)},
            level="INFO",
            msg=f"Qdrant delete: {collection}/{len(ids)} points",
        )

        try:
            from qdrant_client.models import PointIdsList

            uuid_ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, id_)) for id_ in ids]
            self._client.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=uuid_ids),
            )
        except Exception as exc:
            log_debug(
                "QDRANT_DELETE_FAILED",
                payload={"error": str(exc), "collection": collection},
                level="WARN",
                error_code="QDRANT_DELETE_FAILED",
                msg=f"Qdrant delete failed: {exc}",
            )

    def get_embedding(self, text: str) -> list[float]:
        """Generate embedding for text using bge-base-en-v1.5.

        Returns a 768-dim vector when sentence-transformers is available.
        Falls back to a deterministic hash-based 768-dim dummy for testing.
        """
        if self._ensure_model():
            try:
                embedding = _sentence_model.encode(text, normalize_embeddings=True)
                return embedding.tolist()
            except Exception as exc:
                log_debug(
                    "QDRANT_EMBEDDING_FAILED",
                    payload={"error": str(exc), "text_len": len(text)},
                    level="WARN",
                    error_code="QDRANT_EMBEDDING_FAILED",
                    msg=f"Embedding generation failed: {exc}",
                )

        # Fallback: deterministic hash-based dummy vector (NOT semantically meaningful)
        h = hashlib.sha256(text.encode()).hexdigest()
        return [float(int(h[i % 64: i % 64 + 8], 16) % 1000) / 1000.0 for i in range(_EMBEDDING_DIM)]

    def upsert_with_embedding(
        self,
        collection: str,
        id: str,
        text: str,
        payload: dict,
    ) -> None:
        """Convenience: generate embedding from text and upsert."""
        vector = self.get_embedding(text)
        self.upsert(collection, id, vector, payload)

    def search_similar(
        self,
        collection: str,
        text: str,
        limit: int = 5,
    ) -> list[dict]:
        """Convenience: generate embedding from text and search."""
        vector = self.get_embedding(text)
        return self.search(collection, vector, limit=limit)

    def close(self) -> None:
        """Close the Qdrant client connection if open."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
