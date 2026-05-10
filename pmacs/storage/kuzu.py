"""KuzuDB graph adapter — stub for graph operations (Holding-Evidence-Resolution-Lesson lineage).

Architecture.md §1.8: Both audit and debug logging required.
Architecture.md §1.11 / §16.5: cycle_id required on audit-emitting functions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.logsys import log_debug


class KuzuDBAdapter:
    """Adapter for KuzuDB graph operations.

    Stub for now — actual KuzuDB connection requires the ``kuzu`` Python package.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path
        self._conn: Any = None

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def execute(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Execute a Cypher query.  Stub returns empty list."""
        # Debug event — query trace (Architecture.md §1.8)
        log_debug(
            "KUZU_QUERY",
            payload={"query": query[:200]},
            level="DEBUG",
        )
        return []

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------

    def write_failed_assumption(
        self,
        fa_id: str,
        taxonomy: str,
        severity: float,
        holding_id: str,
        cycle_id: str,
        summary: str,
    ) -> None:
        """Write a FailedAssumption node and link to Holding."""
        # Audit event — failed assumption written (Architecture.md §1.8)
        log_debug(
            "FAILED_ASSUMPTION_WRITTEN",
            payload={
                "fa_id": fa_id,
                "taxonomy": taxonomy,
                "severity": severity,
                "holding_id": holding_id,
                "summary": summary,
            },
            level="INFO",
            cycle_id=cycle_id,
            msg=f"Failed assumption written: {fa_id} ({taxonomy})",
        )

    def get_failures_for_ticker(self, ticker: str, limit: int = 10) -> list[dict]:
        """Get recent FailedAssumption nodes for a ticker."""
        results: list[dict] = []
        # Debug event — failures retrieved (Architecture.md §1.8)
        log_debug(
            "KUZU_FAILURES_RETRIEVED",
            payload={"ticker": ticker, "limit": limit, "count": len(results)},
            level="DEBUG",
        )
        return results

    def get_lineage(self, holding_id: str) -> dict:
        """Get full lineage: Holding -> Evidence -> Resolution -> Lesson."""
        result: dict = {}
        # Debug event — lineage retrieval (Architecture.md §1.8)
        log_debug(
            "KUZU_LINEAGE_RETRIEVED",
            payload={"holding_id": holding_id},
            level="DEBUG",
        )
        return result
