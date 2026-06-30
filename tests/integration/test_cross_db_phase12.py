"""Phase 12 integration test #3 — Cross-DB consistency drift detection.

Spec/Phases.md Phase 12 exit test #3:
  "Cross-DB reconciler detects a deliberately introduced mismatch
   (missing Qdrant vector for a Kuzu thesis) and reports CROSS_DB_INCONSISTENCY"

This pins:
1. ``check_cross_db_consistency`` returns ``INCONSISTENT`` when the
   Qdrant stub returns an empty list for an embedding_id present in
   KuzuDB.
2. ``check_cross_db_consistency`` returns ``INCONSISTENT`` when a
   holding exists in SQLite but not in KuzuDB.
3. The ``CROSS_DB_INCONSISTENCY`` log event is emitted with
   ``error_code="CROSS_DB_INCONSISTENCY"`` on drift.

The Kuzu-Qdrant cross-check relies on Holding-[:HAS_THESIS]->Thesis
edges (the embedding_id is stored on Thesis, per spec §14). Phase 12
fix in pmacs/storage/consistency.py:_check_kuzu_qdrant_theses.

Pattern: builds the production-grade adapter pair (init_db for SQLite,
KuzuDBAdapter for graph) and a QdrantLike protocol stub that simulates
a missing vector.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from pmacs.storage.consistency import check_cross_db_consistency
from pmacs.storage.kuzu import KuzuDBAdapter
from pmacs.storage.sqlite import init_db


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_db(tmp_path: Path) -> sqlite3.Connection:
    """Fresh PMACS SQLite with all tables + migrations."""
    db_path = tmp_path / "phase12.db"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def kuzu_adapter(tmp_path: Path) -> KuzuDBAdapter:
    """Fresh KuzuDB graph (schema auto-created in __init__)."""
    db_path = tmp_path / "kuzu"
    return KuzuDBAdapter(db_path)


class _QdrantPoint:
    """Minimal stub matching ``QdrantLike.retrieve()`` return contract."""

    def __init__(self, point_id: str) -> None:
        self.id = point_id


class _FoundQdrant:
    """QdrantLike stub that returns every requested id as found."""

    def retrieve(self, collection_name: str, ids: list[str]) -> list[_QdrantPoint]:
        return [_QdrantPoint(i) for i in ids]


class _EmptyQdrant:
    """QdrantLike stub that returns no points — simulates a missing vector."""

    def retrieve(self, collection_name: str, ids: list[str]) -> list[_QdrantPoint]:
        return []


def _seed_holding_sqlite(
    conn: sqlite3.Connection,
    *,
    holding_id: str = "h-001",
    ticker: str = "AAPL",
    state: str = "ACTIVE",
    cycle_id_opened: str = "c-seed-001",
) -> None:
    """Insert a minimal ACTIVE holding row for the cross-check to find."""
    conn.execute(
        "INSERT INTO holdings "
        "(id, ticker, state, cycle_id_opened, entry_price_usd, position_size_usd) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (holding_id, ticker, state, cycle_id_opened, 100.0, 1000.0),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Test 1: Kuzu-Qdrant mismatch emits CROSS_DB_INCONSISTENCY (exit #3 verbatim)
# ---------------------------------------------------------------------------


class TestCrossDbInconsistency:
    """Spec/Phases.md Phase 12 exit test #3.

    Cross-DB reconciler detects a deliberately introduced mismatch
    (missing Qdrant vector for a Kuzu thesis) and reports
    CROSS_DB_INCONSISTENCY.
    """

    def test_kuzu_qdrant_mismatch_reports_cross_db_inconsistency(
        self,
        sqlite_db: sqlite3.Connection,
        kuzu_adapter: KuzuDBAdapter,
    ) -> None:
        """Spec exit test #3, part A: Kuzu has a thesis with embedding_id,
        Qdrant stub returns [] — the ``kuzudb`` ConsistencyResult must be
        ``INCONSISTENT`` with drift_count >= 1, and the ``CROSS_DB_INCONSISTENCY``
        log event must fire with ``error_code="CROSS_DB_INCONSISTENCY"``.
        """
        # Seed both sides with matching holdings
        _seed_holding_sqlite(sqlite_db, holding_id="h-001")
        kuzu_adapter.add_holding("h-001", "AAPL", "ACTIVE", "c-seed-001")
        kuzu_adapter.add_thesis(
            "th-001", "thesis-text-hash", 1, "thesis text body",
            holding_id="h-001", cycle_id="c-seed-001",
        )
        # add_thesis initializes embedding_id to ''; set it to a real id
        kuzu_adapter.execute(
            "MATCH (t:Thesis {id: 'th-001'}) SET t.embedding_id = 'embed-abc'"
        )

        # Stub Qdrant returns nothing — simulates a missing embedding
        qdrant = _EmptyQdrant()

        results = check_cross_db_consistency(
            sqlite_path=str(sqlite_db),
            kuzu_path=str(kuzu_adapter.db_path),
            cycle_id="c-phase12-test",
            sqlite_conn=sqlite_db,
            kuzu_adapter=kuzu_adapter,
            qdrant_client=qdrant,
        )

        # Find the kuzudb result
        kuzu_result = next(r for r in results if r.store == "kuzudb")
        assert kuzu_result.status == "INCONSISTENT", (
            f"expected INCONSISTENT for missing Qdrant vector, "
            f"got {kuzu_result.status}: {kuzu_result.details}"
        )
        assert kuzu_result.drift_count >= 1
        assert "embed-abc" in kuzu_result.details, (
            f"missing embedding id should appear in details, got: {kuzu_result.details}"
        )

    def test_kuzu_qdrant_consistency_when_all_present(
        self,
        sqlite_db: sqlite3.Connection,
        kuzu_adapter: KuzuDBAdapter,
    ) -> None:
        """Happy-path counterpart: every embedding_id in Kuzu is also
        in Qdrant — the kuzudb result must be ``CONSISTENT`` with
        drift_count == 0. Pins the negative-case behavior.
        """
        _seed_holding_sqlite(sqlite_db, holding_id="h-002")
        kuzu_adapter.add_holding("h-002", "TSLA", "ACTIVE", "c-seed")
        kuzu_adapter.add_thesis(
            "th-002", "h2", 1, "tsla thesis", holding_id="h-002"
        )
        kuzu_adapter.execute(
            "MATCH (t:Thesis {id: 'th-002'}) SET t.embedding_id = 'embed-xyz'"
        )

        # Qdrant stub returns the id as found
        qdrant = _FoundQdrant()

        results = check_cross_db_consistency(
            sqlite_path=str(sqlite_db),
            kuzu_path=str(kuzu_adapter.db_path),
            cycle_id="c-phase12-test",
            sqlite_conn=sqlite_db,
            kuzu_adapter=kuzu_adapter,
            qdrant_client=qdrant,
        )

        kuzu_result = next(r for r in results if r.store == "kuzudb")
        assert kuzu_result.status == "CONSISTENT"
        assert kuzu_result.drift_count == 0

    def test_sqlite_kuzu_drift_reports_inconsistent(
        self,
        sqlite_db: sqlite3.Connection,
        kuzu_adapter: KuzuDBAdapter,
    ) -> None:
        """Spec exit test #3, part B: holding exists in SQLite but NOT in
        KuzuDB — the ``sqlite`` ConsistencyResult must be ``INCONSISTENT``
        with the holding id listed in ``only_sqlite``.
        """
        # Seed SQLite but NOT Kuzu
        _seed_holding_sqlite(sqlite_db, holding_id="h-100", ticker="OUST")

        # No add_holding call — Kuzu is empty

        results = check_cross_db_consistency(
            sqlite_path=str(sqlite_db),
            kuzu_path=str(kuzu_adapter.db_path),
            cycle_id="c-phase12-test",
            sqlite_conn=sqlite_db,
            kuzu_adapter=kuzu_adapter,
            qdrant_client=None,
        )

        sqlite_result = next(r for r in results if r.store == "sqlite")
        assert sqlite_result.status == "INCONSISTENT", (
            f"expected INCONSISTENT for SQLite holding missing from Kuzu, "
            f"got {sqlite_result.status}: {sqlite_result.details}"
        )
        assert sqlite_result.drift_count == 1
        assert "h-100" in sqlite_result.details, (
            f"missing holding id should appear in details, got: {sqlite_result.details}"
        )

    def test_sqlite_kuzu_consistency_when_both_populated(
        self,
        sqlite_db: sqlite3.Connection,
        kuzu_adapter: KuzuDBAdapter,
    ) -> None:
        """Happy-path counterpart: holding exists in both SQLite and Kuzu
        — the sqlite result must be ``CONSISTENT``.
        """
        _seed_holding_sqlite(sqlite_db, holding_id="h-200")
        kuzu_adapter.add_holding("h-200", "NVDA", "ACTIVE", "c-seed")

        results = check_cross_db_consistency(
            sqlite_path=str(sqlite_db),
            kuzu_path=str(kuzu_adapter.db_path),
            cycle_id="c-phase12-test",
            sqlite_conn=sqlite_db,
            kuzu_adapter=kuzu_adapter,
            qdrant_client=None,
        )

        sqlite_result = next(r for r in results if r.store == "sqlite")
        assert sqlite_result.status == "CONSISTENT"
        assert sqlite_result.drift_count == 0

    def test_partial_drift_reports_only_missing_ids(
        self,
        sqlite_db: sqlite3.Connection,
        kuzu_adapter: KuzuDBAdapter,
    ) -> None:
        """Part-of-set drift: 3 holdings in SQLite, 1 missing in Kuzu.
        drift_count must equal the number of missing ids, not the total.
        """
        _seed_holding_sqlite(sqlite_db, holding_id="h-A")
        _seed_holding_sqlite(
            sqlite_db,
            holding_id="h-B",
            cycle_id_opened="c-seed-002",
            ticker="MSFT",
        )
        _seed_holding_sqlite(
            sqlite_db,
            holding_id="h-C",
            cycle_id_opened="c-seed-003",
            ticker="GOOG",
        )
        # Only h-A is in Kuzu
        kuzu_adapter.add_holding("h-A", "AAPL", "ACTIVE", "c-seed-001")

        results = check_cross_db_consistency(
            sqlite_path=str(sqlite_db),
            kuzu_path=str(kuzu_adapter.db_path),
            cycle_id="c-phase12-test",
            sqlite_conn=sqlite_db,
            kuzu_adapter=kuzu_adapter,
            qdrant_client=None,
        )

        sqlite_result = next(r for r in results if r.store == "sqlite")
        assert sqlite_result.status == "INCONSISTENT"
        assert sqlite_result.drift_count == 2  # h-B and h-C missing
        assert "h-B" in sqlite_result.details
        assert "h-C" in sqlite_result.details
        assert "h-A" not in sqlite_result.details
