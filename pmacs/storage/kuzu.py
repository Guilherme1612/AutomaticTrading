"""KuzuDB graph adapter — stub for graph operations (Holding-Evidence-Resolution-Lesson lineage)."""
from __future__ import annotations

from pathlib import Path
from typing import Any


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
        pass

    def get_failures_for_ticker(self, ticker: str, limit: int = 10) -> list[dict]:
        """Get recent FailedAssumption nodes for a ticker."""
        return []

    def get_lineage(self, holding_id: str) -> dict:
        """Get full lineage: Holding -> Evidence -> Resolution -> Lesson."""
        return {}
