"""Memory engine — antipattern detection from lessons / failed assumptions.

Spec ref: Architecture.md §9, Agents.md §4.2 step 5

Phase 3 stub: always returns None (no antipatterns detected).
Will be implemented in later phases with lesson/failed-assumption data from
KuzuDB + Qdrant.
"""

from __future__ import annotations


def check_antipattern(ticker: str, cycle_id: str) -> str | None:
    """Check whether a ticker matches a known antipattern.

    Stub implementation — always returns None.

    Args:
        ticker: Ticker symbol to check.
        cycle_id: Current cycle identifier (for audit logging).

    Returns:
        None (no antipattern detected). Future implementation will return
        a string pattern name when a match is found.
    """
    return None
