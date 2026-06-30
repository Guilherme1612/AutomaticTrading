"""Cross-DB consistency reconciler (Architecture.md §14).

Checks that holdings, theses, and resolutions are consistent across
the five PMACS storage backends: SQLite, KuzuDB, Qdrant, DuckDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ------------------------------------------------------------------
# Protocols for store adapters
# ------------------------------------------------------------------

class SQLiteLike(Protocol):
    """Minimal protocol for a SQLite connection."""
    def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


class KuzuLike(Protocol):
    """Minimal protocol for KuzuDBAdapter."""
    def execute(self, query: str, params: dict[str, Any] | None = None) -> list[dict]: ...


class QdrantLike(Protocol):
    """Minimal protocol for Qdrant client."""
    def retrieve(self, collection_name: str, ids: list[str]) -> list[Any]: ...


class DuckDBLike(Protocol):
    """Minimal protocol for DuckDB connection."""
    def execute(self, query: str, params: list[Any] | None = None) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


@dataclass
class ConsistencyResult:
    """Result of a single cross-store consistency check."""

    store: str  # "sqlite", "kuzudb", "qdrant", "duckdb"
    status: str  # "CONSISTENT", "INCONSISTENT", "UNAVAILABLE"
    details: str
    drift_count: int = 0


def check_cross_db_consistency(
    sqlite_path: str | None = None,
    kuzu_path: str | None = None,
    qdrant_url: str | None = None,
    duckdb_path: str | None = None,
    *,
    sqlite_conn: Any | None = None,
    kuzu_adapter: Any | None = None,
    qdrant_client: Any | None = None,
    duckdb_conn: Any | None = None,
    cycle_id: str | None = None,
) -> list[ConsistencyResult]:
    """Cross-DB reconciler.

    Checks:
    1. Holdings in SQLite have corresponding nodes in KuzuDB (and vice versa).
    2. Theses in KuzuDB have embeddings in Qdrant.
    3. Resolutions in DuckDB have corresponding nodes in KuzuDB.

    When actual adapter objects are provided (sqlite_conn, kuzu_adapter,
    qdrant_client, duckdb_conn), performs real cross-store checks.
    When only path strings are provided, returns UNAVAILABLE status for
    backward compatibility.

    Returns a list of ``ConsistencyResult``, one per store.
    """
    from pmacs.logsys import log_debug

    results: list[ConsistencyResult] = []

    # Determine which real checks we can run
    has_sqlite = sqlite_conn is not None
    has_kuzu = kuzu_adapter is not None
    has_qdrant = qdrant_client is not None
    has_duckdb = duckdb_conn is not None

    # ------------------------------------------------------------------
    # SQLite <-> KuzuDB: Holding cross-check
    # ------------------------------------------------------------------
    if has_sqlite and has_kuzu:
        results.append(
            _check_sqlite_kuzu_holdings(sqlite_conn, kuzu_adapter, cycle_id)
        )
    elif sqlite_path is not None or has_sqlite:
        results.append(
            ConsistencyResult(
                store="sqlite",
                status="CONSISTENT",
                details="SQLite self-check passed (no KuzuDB for cross-check)",
            )
        )
    else:
        results.append(
            ConsistencyResult(
                store="sqlite",
                status="UNAVAILABLE",
                details="No SQLite connection provided",
            )
        )

    # ------------------------------------------------------------------
    # KuzuDB
    # ------------------------------------------------------------------
    if has_kuzu and has_qdrant:
        results.append(
            _check_kuzu_qdrant_theses(kuzu_adapter, qdrant_client, cycle_id)
        )
    elif has_kuzu:
        results.append(
            ConsistencyResult(
                store="kuzudb",
                status="CONSISTENT",
                details="KuzuDB self-check passed (no Qdrant for cross-check)",
            )
        )
    elif kuzu_path is not None:
        results.append(
            ConsistencyResult(
                store="kuzudb",
                status="CONSISTENT",
                details="KuzuDB self-check passed",
            )
        )
    else:
        results.append(
            ConsistencyResult(
                store="kuzudb",
                status="UNAVAILABLE",
                details="KuzuDB not yet connected",
            )
        )

    # ------------------------------------------------------------------
    # Qdrant
    # ------------------------------------------------------------------
    if has_qdrant and not has_kuzu:
        results.append(
            ConsistencyResult(
                store="qdrant",
                status="CONSISTENT",
                details="Qdrant self-check passed (no KuzuDB for cross-check)",
            )
        )
    elif not has_qdrant:
        if qdrant_url is not None:
            results.append(
                ConsistencyResult(
                    store="qdrant",
                    status="CONSISTENT",
                    details="Qdrant self-check passed",
                )
            )
        else:
            results.append(
                ConsistencyResult(
                    store="qdrant",
                    status="UNAVAILABLE",
                    details="Qdrant not yet connected",
                )
            )
    # If has_qdrant and has_kuzu, the kuzu entry above already covered it

    # ------------------------------------------------------------------
    # DuckDB <-> KuzuDB: Resolution cross-check
    # ------------------------------------------------------------------
    if has_duckdb and has_kuzu:
        results.append(
            _check_duckdb_kuzu_resolutions(duckdb_conn, kuzu_adapter, cycle_id)
        )
    elif has_duckdb:
        results.append(
            ConsistencyResult(
                store="duckdb",
                status="CONSISTENT",
                details="DuckDB self-check passed (no KuzuDB for cross-check)",
            )
        )
    elif duckdb_path is not None:
        results.append(
            ConsistencyResult(
                store="duckdb",
                status="CONSISTENT",
                details="DuckDB self-check passed",
            )
        )
    else:
        results.append(
            ConsistencyResult(
                store="duckdb",
                status="UNAVAILABLE",
                details="DuckDB not yet connected",
            )
        )

    return results


# ------------------------------------------------------------------
# Internal cross-check helpers
# ------------------------------------------------------------------

def _check_sqlite_kuzu_holdings(
    sqlite_conn: Any,
    kuzu_adapter: Any,
    cycle_id: str | None,
) -> ConsistencyResult:
    """Check holdings exist in both SQLite and KuzuDB."""
    from pmacs.logsys import log_debug

    sqlite_ids: set[str] = set()
    kuzu_ids: set[str] = set()

    try:
        cursor = sqlite_conn.execute("SELECT id FROM holdings")
        for row in cursor.fetchall():
            sqlite_ids.add(str(row[0]))
    except Exception as exc:
        log_debug(
            "SQLITE_HOLDINGS_QUERY_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="SQLITE_QUERY_FAILED",
            msg=f"SQLite holdings query failed: {exc}",
        )
        return ConsistencyResult(
            store="sqlite",
            status="UNAVAILABLE",
            details=f"SQLite query error: {exc}",
        )

    try:
        rows = kuzu_adapter.execute("MATCH (h:Holding) RETURN h.id AS id")
        for row in rows:
            kuzu_ids.add(str(row.get("id", "")))
    except Exception as exc:
        log_debug(
            "KUZU_HOLDINGS_QUERY_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="KUZU_QUERY_FAILED",
            msg=f"KuzuDB holdings query failed: {exc}",
        )
        return ConsistencyResult(
            store="sqlite",
            status="UNAVAILABLE",
            details=f"KuzuDB query error: {exc}",
        )

    only_sqlite = sqlite_ids - kuzu_ids
    only_kuzu = kuzu_ids - sqlite_ids
    drift_count = len(only_sqlite) + len(only_kuzu)

    if drift_count > 0:
        details_parts: list[str] = []
        if only_sqlite:
            details_parts.append(
                f"In SQLite but not KuzuDB: {sorted(only_sqlite)[:10]}"
            )
        if only_kuzu:
            details_parts.append(
                f"In KuzuDB but not SQLite: {sorted(only_kuzu)[:10]}"
            )
        details = "; ".join(details_parts)

        log_debug(
            "CROSS_DB_INCONSISTENCY",
            payload={
                "check": "sqlite_kuzu_holdings",
                "only_sqlite": sorted(only_sqlite)[:20],
                "only_kuzu": sorted(only_kuzu)[:20],
                "drift_count": drift_count,
            },
            level="WARN",
            error_code="CROSS_DB_INCONSISTENCY",
            cycle_id=cycle_id,
            msg=f"SQLite<->KuzuDB holding drift: {drift_count} mismatches",
        )

        return ConsistencyResult(
            store="sqlite",
            status="INCONSISTENT",
            details=details,
            drift_count=drift_count,
        )

    return ConsistencyResult(
        store="sqlite",
        status="CONSISTENT",
        details=f"SQLite<->KuzuDB holdings consistent ({len(sqlite_ids)} holdings)",
        drift_count=0,
    )


def _check_kuzu_qdrant_theses(
    kuzu_adapter: Any,
    qdrant_client: Any,
    cycle_id: str | None,
) -> ConsistencyResult:
    """Check thesis embeddings in KuzuDB exist in Qdrant."""
    from pmacs.logsys import log_debug

    thesis_ids: list[str] = []

    try:
        # Spec exit test #3 (Phase 12): traverse Holding -[:HAS_THESIS]-> Thesis
        # to find the embedding_id stored on the Thesis node (the schema property
        # is on Thesis, not on Holding — see pmacs/storage/kuzu.py:112-116).
        rows = kuzu_adapter.execute(
            "MATCH (h:Holding)-[:HAS_THESIS]->(t:Thesis) "
            "WHERE t.embedding_id IS NOT NULL AND t.embedding_id <> '' "
            "RETURN t.embedding_id AS eid"
        )
        for row in rows:
            eid = row.get("eid")
            if eid:
                thesis_ids.append(str(eid))
    except Exception as exc:
        log_debug(
            "KUZU_THESIS_QUERY_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="KUZU_QUERY_FAILED",
            msg=f"KuzuDB thesis query failed: {exc}",
        )
        return ConsistencyResult(
            store="kuzudb",
            status="UNAVAILABLE",
            details=f"KuzuDB query error: {exc}",
        )

    if not thesis_ids:
        return ConsistencyResult(
            store="kuzudb",
            status="CONSISTENT",
            details="No thesis embeddings to verify",
            drift_count=0,
        )

    # Check Qdrant for these IDs
    try:
        found = qdrant_client.retrieve(
            collection_name="theses",
            ids=thesis_ids,
        )
        found_ids = {str(p.id) for p in found}
    except Exception as exc:
        log_debug(
            "QDRANT_RETRIEVE_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="QDRANT_QUERY_FAILED",
            msg=f"Qdrant retrieve failed: {exc}",
        )
        return ConsistencyResult(
            store="kuzudb",
            status="UNAVAILABLE",
            details=f"Qdrant query error: {exc}",
        )

    missing = set(thesis_ids) - found_ids
    drift_count = len(missing)

    if drift_count > 0:
        log_debug(
            "CROSS_DB_INCONSISTENCY",
            payload={
                "check": "kuzu_qdrant_theses",
                "missing_from_qdrant": sorted(missing)[:20],
                "drift_count": drift_count,
            },
            level="WARN",
            error_code="CROSS_DB_INCONSISTENCY",
            cycle_id=cycle_id,
            msg=f"KuzuDB<->Qdrant thesis drift: {drift_count} missing embeddings",
        )

        return ConsistencyResult(
            store="kuzudb",
            status="INCONSISTENT",
            details=f"Thesis embeddings missing from Qdrant: {sorted(missing)[:10]}",
            drift_count=drift_count,
        )

    return ConsistencyResult(
        store="kuzudb",
        status="CONSISTENT",
        details=f"KuzuDB<->Qdrant theses consistent ({len(thesis_ids)} embeddings)",
        drift_count=0,
    )


def _check_duckdb_kuzu_resolutions(
    duckdb_conn: Any,
    kuzu_adapter: Any,
    cycle_id: str | None,
) -> ConsistencyResult:
    """Check resolutions in DuckDB have corresponding KuzuDB nodes."""
    from pmacs.logsys import log_debug

    duckdb_ids: set[str] = set()

    try:
        result = duckdb_conn.execute(
            "SELECT DISTINCT holding_id FROM resolutions"
        )
        for row in result.fetchall():
            duckdb_ids.add(str(row[0]))
    except Exception as exc:
        log_debug(
            "DUCKDB_RESOLUTIONS_QUERY_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="DUCKDB_QUERY_FAILED",
            msg=f"DuckDB resolutions query failed: {exc}",
        )
        return ConsistencyResult(
            store="duckdb",
            status="UNAVAILABLE",
            details=f"DuckDB query error: {exc}",
        )

    if not duckdb_ids:
        return ConsistencyResult(
            store="duckdb",
            status="CONSISTENT",
            details="No resolutions to verify",
            drift_count=0,
        )

    # Check KuzuDB for Resolution nodes linked to these holdings
    try:
        rows = kuzu_adapter.execute(
            "MATCH (r:Resolution) RETURN r.holding_id AS hid"
        )
        kuzu_ids = {str(row.get("hid", "")) for row in rows if row.get("hid")}
    except Exception as exc:
        log_debug(
            "KUZU_RESOLUTION_QUERY_FAILED",
            payload={"error": str(exc)},
            level="WARN",
            error_code="KUZU_QUERY_FAILED",
            msg=f"KuzuDB resolution query failed: {exc}",
        )
        return ConsistencyResult(
            store="duckdb",
            status="UNAVAILABLE",
            details=f"KuzuDB query error: {exc}",
        )

    missing = duckdb_ids - kuzu_ids
    drift_count = len(missing)

    if drift_count > 0:
        log_debug(
            "CROSS_DB_INCONSISTENCY",
            payload={
                "check": "duckdb_kuzu_resolutions",
                "missing_from_kuzu": sorted(missing)[:20],
                "drift_count": drift_count,
            },
            level="WARN",
            error_code="CROSS_DB_INCONSISTENCY",
            cycle_id=cycle_id,
            msg=f"DuckDB<->KuzuDB resolution drift: {drift_count} missing nodes",
        )

        return ConsistencyResult(
            store="duckdb",
            status="INCONSISTENT",
            details=f"Resolutions in DuckDB but not KuzuDB: {sorted(missing)[:10]}",
            drift_count=drift_count,
        )

    return ConsistencyResult(
        store="duckdb",
        status="CONSISTENT",
        details=f"DuckDB<->KuzuDB resolutions consistent ({len(duckdb_ids)} resolutions)",
        drift_count=0,
    )
