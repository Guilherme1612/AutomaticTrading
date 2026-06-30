"""Phase 12 integration test #5 — FailedAssumption KuzuDB graph traversal.

Spec/Phases.md Phase 12 exit test #5:
  "FailedAssumption nodes written to KuzuDB are traversable:
   MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa) RETURN fa"

This test pins the contract from Architecture.md §9.5 + Agents.md §15.2:
``KuzuDBAdapter.write_failed_assumption`` creates a ``:FailedAssumption``
node and a ``:FAILED_ASSUMPTION`` edge from the parent ``:Holding``,
and the resulting graph is traversable via the spec's exact Cypher query.

Two tests:
1. ``test_write_failed_assumption_creates_traversable_edge`` — direct
   write + spec traversal query.
2. ``test_step_fde_writes_failed_assumption_to_kuzu`` — full orchestrator
   step 25 path: classify a STOP_HUNTED holding then write to Kuzu.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pmacs.engines.failure_diagnostic import HoldingContext, classify
from pmacs.schemas.failure import FailureTaxonomy
from pmacs.storage.kuzu import KuzuDBAdapter


@pytest.fixture
def kuzu_adapter(tmp_path: Path) -> KuzuDBAdapter:
    """Fresh KuzuDB instance in a temp dir (schema + edge tables auto-created)."""
    db_path = tmp_path / "kuzu"
    return KuzuDBAdapter(db_path)


# ---------------------------------------------------------------------------
# Test 1: direct write + spec traversal query (the spec exit test verbatim)
# ---------------------------------------------------------------------------


class TestFailedAssumptionGraphTraversal:
    """Pin Agents.md §15.2 / Architecture.md §9.5: FailedAssumption nodes
    written to KuzuDB are traversable via MATCH (h)-[:FAILED_ASSUMPTION]->(fa)."""

    def test_write_failed_assumption_creates_traversable_edge(
        self, kuzu_adapter: KuzuDBAdapter
    ) -> None:
        """Spec exit test #5, verbatim.

        1. Insert a Holding node.
        2. Call ``KuzuDBAdapter.write_failed_assumption``.
        3. Run the spec's traversal query and assert the node + edge
           properties match what was written.
        """
        # Seed a Holding
        kuzu_adapter.add_holding("h-001", "AAPL", "ACTIVE", "c-seed")

        # Write the FailedAssumption
        kuzu_adapter.write_failed_assumption(
            fa_id="fa-001",
            taxonomy="STOP_HUNTED",
            severity=0.7,
            holding_id="h-001",
            cycle_id="c-001",
            summary="Stopped at 95, recovered to 103 within 48h",
        )

        # Run the spec's exact traversal query
        rows = kuzu_adapter.execute(
            "MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa:FailedAssumption) "
            "WHERE h.id = 'h-001' "
            "RETURN fa.id AS fa_id, fa.taxonomy AS taxonomy, "
            "fa.severity AS severity, fa.summary AS summary"
        )

        assert len(rows) == 1, f"Expected 1 FailedAssumption traversal, got {len(rows)}"
        row = rows[0]
        assert row["fa_id"] == "fa-001"
        assert row["taxonomy"] == "STOP_HUNTED"
        assert row["severity"] == pytest.approx(0.7)
        assert "recovered" in row["summary"]

    def test_step_fde_writes_failed_assumption_to_kuzu(
        self, kuzu_adapter: KuzuDBAdapter
    ) -> None:
        """Full end-to-end: classify a STOP_HUNTED holding via the FDE
        ``classify()`` (the contract that ``_step_fde`` invokes), then write
        the resulting classification to Kuzu, then traverse.

        This pins that the orchestrator's step 25 path (classify → Kuzu write)
        produces a graph the spec traversal query can read.
        """
        # Seed Holding
        kuzu_adapter.add_holding("h-002", "TSLA", "ACTIVE", "c-seed")

        # Build the context the orchestrator would build for a STOPPED_OUT
        # holding whose price recovered 48h after exit (the STOP_HUNTED case).
        ctx = HoldingContext(
            state="STOPPED_OUT",
            ticker="TSLA",
            entry_price=200.0,
            exit_price=180.0,
            price_48h_after_exit=205.0,  # > 1.02 * entry → STOP_HUNTED
        )

        # FDE classify (mirrors orchestrator._step_fde)
        result = classify(
            ctx, holding_id="h-002", cycle_id="c-002"
        )

        # Pin the FDE contract first
        assert result.primary == FailureTaxonomy.STOP_HUNTED
        assert result.severity == pytest.approx(0.7)
        assert result.holding_id == "h-002"
        assert result.cycle_id == "c-002"

        # Now write to Kuzu (mirrors orchestrator._step_fde body)
        from uuid import uuid4 as _uuid4

        kuzu_adapter.write_failed_assumption(
            fa_id=str(_uuid4()),
            taxonomy=result.primary.value,
            severity=result.severity,
            holding_id="h-002",
            cycle_id="c-002",
            summary=result.summary,
        )

        # Traverse
        rows = kuzu_adapter.execute(
            "MATCH (h:Holding)-[:FAILED_ASSUMPTION]->(fa:FailedAssumption) "
            "WHERE h.id = 'h-002' "
            "RETURN fa.taxonomy AS taxonomy, fa.severity AS severity"
        )

        assert len(rows) == 1
        assert rows[0]["taxonomy"] == "STOP_HUNTED"
        assert rows[0]["severity"] == pytest.approx(0.7)

    def test_write_failed_assumption_for_holding_without_thesis(
        self, kuzu_adapter: KuzuDBAdapter
    ) -> None:
        """Edge case: FailedAssumption written for a holding that has no
        Thesis or Evidence nodes — only the FAILED_ASSUMPTION edge should
        exist from the Holding. Guards against the KuzuDB write path
        requiring other node types to be present.
        """
        kuzu_adapter.add_holding("h-003", "ONDS", "ACTIVE", "c-seed")
        kuzu_adapter.write_failed_assumption(
            fa_id="fa-003",
            taxonomy="CATALYST_FALSE_POSITIVE",
            severity=0.4,
            holding_id="h-003",
            cycle_id="c-003",
            summary="Catalyst resolved but market disagreed",
        )

        # Traverse — should find the edge even without Thesis/Evidence
        rows = kuzu_adapter.execute(
            "MATCH (h:Holding {id: 'h-003'})-[:FAILED_ASSUMPTION]->(fa:FailedAssumption) "
            "RETURN fa.taxonomy AS taxonomy"
        )
        assert len(rows) == 1
        assert rows[0]["taxonomy"] == "CATALYST_FALSE_POSITIVE"

    def test_holding_with_no_failed_assumption_returns_empty(
        self, kuzu_adapter: KuzuDBAdapter
    ) -> None:
        """Negative case: a Holding with no FailedAssumption edge returns
        an empty traversal — guards against false-positive matches from
        a missing WHERE clause or wrong relationship direction.
        """
        kuzu_adapter.add_holding("h-004", "PLTR", "ACTIVE", "c-seed")
        rows = kuzu_adapter.execute(
            "MATCH (h:Holding {id: 'h-004'})-[:FAILED_ASSUMPTION]->(fa:FailedAssumption) "
            "RETURN fa.id AS fa_id"
        )
        assert rows == []
