"""Catalyst resolution detector -- identifies resolved catalysts (Architecture.md §9 step 7).

Stub implementation: returns an empty list. Full implementation deferred to a
future phase that wires catalyst tracking to real data sources.
"""
from __future__ import annotations

from pathlib import Path


class CatalystResolutionDetector:
    """Detect resolved catalysts from pending catalyst records.

    Queries the SQLite database for catalysts with expected dates in the past
    and marks them as resolved based on actual price movement or event outcome.

    Full implementation deferred -- stub returns an empty list.
    """

    def run_all(self, db_path: Path) -> list[dict]:
        """Query pending catalysts and mark resolved ones.

        Args:
            db_path: Path to the SQLite database.

        Returns:
            List of resolved catalyst dicts. Stub returns empty list.
        """
        # Stub -- no catalyst resolution logic yet.
        # Future: query catalysts table, check event outcomes, mark resolved.
        return []
