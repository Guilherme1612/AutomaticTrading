"""KuzuDB graph adapter — Holding-Evidence-Resolution-Lesson lineage (Architecture.md §8.4).

Gracefully degrades when ``kuzu`` package is not installed or the database
is unavailable.  All public methods return empty/default values in stub mode.

Architecture.md §1.8: Both audit and debug logging required.
Architecture.md §1.11 / §16.5: cycle_id required on audit-emitting functions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.logsys import log_debug

# Lazy imports — set by _ensure_connection
_kuzu: Any = None
_kuzu_available: bool | None = None


class KuzuDBAdapter:
    """Adapter for KuzuDB graph operations (Architecture.md §8.4).

    KuzuDB stores sparse projections (key fields + graph edges only).
    Full Holding data lives in SQLite; full evidence content in DuckDB.
    KuzuDB is for lineage traversal, not as a source of truth.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path
        self._db: Any = None
        self._conn: Any = None
        self._schema_initialized = False
        self._ensure_connection()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _ensure_connection(self) -> bool:
        """Try to establish KuzuDB connection. Returns True if connected."""
        global _kuzu, _kuzu_available

        if _kuzu_available is False:
            return False
        if self._conn is not None:
            return True

        try:
            if _kuzu is None:
                import kuzu as _k  # type: ignore[import-untyped]
                _kuzu = _k
            _kuzu_available = True
        except ImportError:
            _kuzu_available = False
            log_debug(
                "KUZU_UNAVAILABLE",
                payload={"reason": "kuzu package not installed"},
                level="INFO",
                msg="KuzuDB adapter running in stub mode (kuzu not installed)",
            )
            return False

        if self.db_path is None:
            return False

        try:
            self._db = _kuzu.Database(str(self.db_path))
            self._conn = _kuzu.Connection(self._db)
            if not self._schema_initialized:
                self._init_schema()
                self._schema_initialized = True
            return True
        except Exception as exc:
            log_debug(
                "KUZU_CONNECTION_FAILED",
                payload={"error": str(exc)},
                level="WARN",
                error_code="KUZU_CONNECTION_FAILED",
                msg=f"KuzuDB connection failed: {exc}",
            )
            self._conn = None
            return False

    def _init_schema(self) -> None:
        """Create all node and edge tables (Architecture.md §8.4)."""
        if self._conn is None:
            return

        # Node tables
        node_tables = [
            """CREATE NODE TABLE IF NOT EXISTS Holding (
                id STRING, ticker STRING, state STRING,
                cycle_id_opened STRING, cycle_id_closed STRING,
                PRIMARY KEY (id)
            )""",
            """CREATE NODE TABLE IF NOT EXISTS Evidence (
                id STRING, source STRING, type STRING,
                fetched_at STRING, content_hash STRING,
                PRIMARY KEY (id)
            )""",
            """CREATE NODE TABLE IF NOT EXISTS Resolution (
                id STRING, holding_id STRING, kind STRING, ts STRING,
                pnl_pct DOUBLE,
                PRIMARY KEY (id)
            )""",
            """CREATE NODE TABLE IF NOT EXISTS Thesis (
                id STRING, hash STRING, version INT64, text STRING,
                embedding_id STRING,
                PRIMARY KEY (id)
            )""",
            """CREATE NODE TABLE IF NOT EXISTS Lesson (
                id STRING, kind STRING, weight DOUBLE, text STRING,
                PRIMARY KEY (id)
            )""",
            """CREATE NODE TABLE IF NOT EXISTS FailedAssumption (
                id STRING, taxonomy STRING, severity DOUBLE, ts STRING,
                summary STRING, holding_id STRING, cycle_id STRING,
                PRIMARY KEY (id)
            )""",
            """CREATE NODE TABLE IF NOT EXISTS MutationOutcome (
                id STRING, dimension STRING, candidate_hash STRING,
                result STRING, effect_size DOUBLE, p_value DOUBLE,
                PRIMARY KEY (id)
            )""",
        ]

        # Edge tables
        edge_tables = [
            "CREATE REL TABLE IF NOT EXISTS BACKED_BY (FROM Holding TO Evidence, weight DOUBLE)",
            "CREATE REL TABLE IF NOT EXISTS RESOLVES_TO (FROM Holding TO Resolution)",
            "CREATE REL TABLE IF NOT EXISTS GROUNDED_IN (FROM Thesis TO Evidence)",
            "CREATE REL TABLE IF NOT EXISTS HAS_THESIS (FROM Holding TO Thesis)",
            "CREATE REL TABLE IF NOT EXISTS PRODUCED_LESSON (FROM Resolution TO Lesson)",
            "CREATE REL TABLE IF NOT EXISTS FAILED_ASSUMPTION (FROM Holding TO FailedAssumption)",
            "CREATE REL TABLE IF NOT EXISTS SIMILAR_TO (FROM Lesson TO Lesson, similarity DOUBLE)",
            "CREATE REL TABLE IF NOT EXISTS INFORMS_MUTATION (FROM FailedAssumption TO MutationOutcome)",
            "CREATE REL TABLE IF NOT EXISTS PROMOTED_FROM (FROM Holding TO MutationOutcome)",
        ]

        for ddl in node_tables + edge_tables:
            try:
                self._conn.execute(ddl)
            except Exception as exc:
                # Table may already exist — that's fine
                log_debug(
                    "KUZU_DDL_SKIP",
                    payload={"ddl": ddl[:80], "error": str(exc)},
                    level="DEBUG",
                    msg=f"KuzuDB DDL skip: {exc}",
                )

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def execute(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Execute a Cypher query and return results as list of dicts."""
        log_debug(
            "KUZU_QUERY",
            payload={"query": query[:200]},
            level="DEBUG",
        )

        if not self._ensure_connection():
            return []

        try:
            result = self._conn.execute(query, params or {})
            cols = result.get_column_names()
            rows = []
            while result.has_next():
                row = result.get_next()
                rows.append(dict(zip(cols, row)))
            return rows
        except Exception as exc:
            log_debug(
                "KUZU_QUERY_FAILED",
                payload={"error": str(exc), "query": query[:100]},
                level="WARN",
                error_code="KUZU_QUERY_FAILED",
                msg=f"KuzuDB query failed: {exc}",
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
        """Write a FailedAssumption node and link to Holding.

        Architecture.md §9.5: Cypher creates the node and links it to the
        parent Holding.
        """
        ts = datetime.now(timezone.utc).isoformat()

        if not self._ensure_connection():
            log_debug(
                "FAILED_ASSUMPTION_WRITTEN",
                payload={
                    "fa_id": fa_id,
                    "taxonomy": taxonomy,
                    "severity": severity,
                    "holding_id": holding_id,
                    "stub": True,
                    "reason": "KuzuDB connection not available",
                },
                level="INFO",
                cycle_id=cycle_id,
                msg=f"Failed assumption stub-logged (no KuzuDB): {fa_id} ({taxonomy})",
            )
            return

        try:
            # Create FailedAssumption node
            self._conn.execute(
                """CREATE (fa:FailedAssumption {
                    id: $id, taxonomy: $tax, severity: $sev,
                    ts: timestamp($ts),
                    holding_id: $hid, cycle_id: $cid, summary: $summary
                })""",
                {
                    "id": fa_id,
                    "tax": taxonomy,
                    "sev": severity,
                    "ts": ts,
                    "hid": holding_id,
                    "cid": cycle_id,
                    "summary": summary,
                },
            )
            # Link to Holding
            try:
                self._conn.execute(
                    "MATCH (h:Holding {id: $hid}), (fa:FailedAssumption {id: $fa_id}) "
                    "CREATE (h)-[:FAILED_ASSUMPTION]->(fa)",
                    {"hid": holding_id, "fa_id": fa_id},
                )
            except Exception:
                # Holding node may not exist yet — that's ok, node is still created
                pass
        except Exception as exc:
            log_debug(
                "KUZU_FAILED_ASSUMPTION_WRITE_FAILED",
                payload={"error": str(exc), "fa_id": fa_id},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"KuzuDB FailedAssumption write failed: {exc}",
            )

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
        if not self._ensure_connection():
            log_debug(
                "KUZU_FAILURES_RETRIEVED",
                payload={"ticker": ticker, "limit": limit, "count": 0},
                level="DEBUG",
            )
            return []

        try:
            results = self.execute(
                "MATCH (fa:FailedAssumption)<-[:FAILED_ASSUMPTION]-(h:Holding {ticker: $t}) "
                "RETURN fa.id, fa.taxonomy, fa.severity, fa.ts, fa.summary "
                "ORDER BY fa.ts DESC LIMIT $lim",
                {"t": ticker, "lim": limit},
            )
            log_debug(
                "KUZU_FAILURES_RETRIEVED",
                payload={"ticker": ticker, "limit": limit, "count": len(results)},
                level="DEBUG",
            )
            return results
        except Exception as exc:
            log_debug(
                "KUZU_FAILURES_QUERY_FAILED",
                payload={"error": str(exc), "ticker": ticker},
                level="WARN",
                error_code="KUZU_QUERY_FAILED",
                msg=f"KuzuDB failures query failed: {exc}",
            )
            return []

    def get_lineage(self, holding_id: str) -> dict:
        """Get full lineage: Holding -> Evidence -> Resolution -> Lesson."""
        if not self._ensure_connection():
            log_debug(
                "KUZU_LINEAGE_RETRIEVED",
                payload={"holding_id": holding_id},
                level="DEBUG",
            )
            return {}

        try:
            result: dict = {"holding_id": holding_id}

            # Get holding node
            h_rows = self.execute(
                "MATCH (h:Holding {id: $hid}) RETURN h.ticker, h.state",
                {"hid": holding_id},
            )
            if h_rows:
                result["ticker"] = h_rows[0].get("h.ticker", "")
                result["state"] = h_rows[0].get("h.state", "")

            # Get evidence
            e_rows = self.execute(
                "MATCH (h:Holding {id: $hid})-[:BACKED_BY]->(e:Evidence) "
                "RETURN e.id, e.source, e.type",
                {"hid": holding_id},
            )
            result["evidence"] = e_rows

            # Get thesis
            t_rows = self.execute(
                "MATCH (h:Holding {id: $hid})-[:HAS_THESIS]->(t:Thesis) "
                "RETURN t.id, t.text, t.version",
                {"hid": holding_id},
            )
            result["theses"] = t_rows

            # Get resolutions + lessons
            r_rows = self.execute(
                "MATCH (h:Holding {id: $hid})-[:RESOLVES_TO]->(r:Resolution) "
                "RETURN r.id, r.kind, r.pnl_pct",
                {"hid": holding_id},
            )
            result["resolutions"] = r_rows

            # Get failed assumptions
            fa_rows = self.execute(
                "MATCH (h:Holding {id: $hid})-[:FAILED_ASSUMPTION]->(fa:FailedAssumption) "
                "RETURN fa.id, fa.taxonomy, fa.severity, fa.summary",
                {"hid": holding_id},
            )
            result["failed_assumptions"] = fa_rows

            log_debug(
                "KUZU_LINEAGE_RETRIEVED",
                payload={"holding_id": holding_id},
                level="DEBUG",
            )
            return result
        except Exception as exc:
            log_debug(
                "KUZU_LINEAGE_QUERY_FAILED",
                payload={"error": str(exc), "holding_id": holding_id},
                level="WARN",
                error_code="KUZU_QUERY_FAILED",
                msg=f"KuzuDB lineage query failed: {exc}",
            )
            return {"holding_id": holding_id}

    def add_holding(
        self,
        holding_id: str,
        ticker: str,
        state: str,
        cycle_id_opened: str,
        cycle_id: str = "",
    ) -> None:
        """Create a Holding node in the graph."""
        if not self._ensure_connection():
            return
        try:
            self._conn.execute(
                "CREATE (h:Holding {id: $id, ticker: $t, state: $s, "
                "cycle_id_opened: $co, cycle_id_closed: ''})",
                {"id": holding_id, "t": ticker, "s": state, "co": cycle_id_opened},
            )
        except Exception as exc:
            log_debug(
                "KUZU_ADD_HOLDING_FAILED",
                payload={"error": str(exc), "holding_id": holding_id},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"KuzuDB add_holding failed: {exc}",
            )

    def link_evidence(self, holding_id: str, evidence_id: str, weight: float = 1.0) -> None:
        """Link a Holding to an Evidence node."""
        if not self._ensure_connection():
            return
        try:
            self._conn.execute(
                "MATCH (h:Holding {id: $hid}), (e:Evidence {id: $eid}) "
                "CREATE (h)-[:BACKED_BY {weight: $w}]->(e)",
                {"hid": holding_id, "eid": evidence_id, "w": weight},
            )
        except Exception as exc:
            log_debug(
                "KUZU_LINK_EVIDENCE_FAILED",
                payload={"error": str(exc), "holding_id": holding_id},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                msg=f"KuzuDB link_evidence failed: {exc}",
            )

    def add_evidence(
        self,
        evidence_id: str,
        source: str,
        etype: str,
        content_hash: str,
        cycle_id: str = "",
    ) -> None:
        """Create an Evidence node in the graph."""
        if not self._ensure_connection():
            return
        ts = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                "CREATE (e:Evidence {id: $id, source: $src, type: $t, "
                "fetched_at: $ts, content_hash: $ch})",
                {"id": evidence_id, "src": source, "t": etype, "ts": ts, "ch": content_hash},
            )
        except Exception as exc:
            log_debug(
                "KUZU_ADD_EVIDENCE_FAILED",
                payload={"error": str(exc), "evidence_id": evidence_id},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"KuzuDB add_evidence failed: {exc}",
            )

    def add_resolution(
        self,
        resolution_id: str,
        holding_id: str,
        kind: str,
        pnl_pct: float,
        cycle_id: str = "",
    ) -> None:
        """Create a Resolution node and link to Holding (RESOLVES_TO)."""
        if not self._ensure_connection():
            return
        ts = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                "CREATE (r:Resolution {id: $id, holding_id: $hid, kind: $k, "
                "ts: $ts, pnl_pct: $pnl})",
                {"id": resolution_id, "hid": holding_id, "k": kind, "ts": ts, "pnl": pnl_pct},
            )
            try:
                self._conn.execute(
                    "MATCH (h:Holding {id: $hid}), (r:Resolution {id: $rid}) "
                    "CREATE (h)-[:RESOLVES_TO]->(r)",
                    {"hid": holding_id, "rid": resolution_id},
                )
            except Exception:
                pass  # Holding may not exist yet
        except Exception as exc:
            log_debug(
                "KUZU_ADD_RESOLUTION_FAILED",
                payload={"error": str(exc), "resolution_id": resolution_id},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"KuzuDB add_resolution failed: {exc}",
            )

    def add_lesson(
        self,
        lesson_id: str,
        kind: str,
        weight: float,
        text: str,
        resolution_id: str | None = None,
        cycle_id: str = "",
    ) -> None:
        """Create a Lesson node and optionally link to Resolution (PRODUCED_LESSON)."""
        if not self._ensure_connection():
            return
        try:
            self._conn.execute(
                "CREATE (l:Lesson {id: $id, kind: $k, weight: $w, text: $t})",
                {"id": lesson_id, "k": kind, "w": weight, "t": text},
            )
            if resolution_id:
                try:
                    self._conn.execute(
                        "MATCH (r:Resolution {id: $rid}), (l:Lesson {id: $lid}) "
                        "CREATE (r)-[:PRODUCED_LESSON]->(l)",
                        {"rid": resolution_id, "lid": lesson_id},
                    )
                except Exception:
                    pass  # Resolution may not exist yet
        except Exception as exc:
            log_debug(
                "KUZU_ADD_LESSON_FAILED",
                payload={"error": str(exc), "lesson_id": lesson_id},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"KuzuDB add_lesson failed: {exc}",
            )

    def add_thesis(
        self,
        thesis_id: str,
        hash_val: str,
        version: int,
        text: str,
        holding_id: str | None = None,
        evidence_id: str | None = None,
        cycle_id: str = "",
    ) -> None:
        """Create a Thesis node, optionally link to Holding and Evidence."""
        if not self._ensure_connection():
            return
        try:
            self._conn.execute(
                "CREATE (t:Thesis {id: $id, hash: $h, version: $v, text: $txt, embedding_id: ''})",
                {"id": thesis_id, "h": hash_val, "v": version, "txt": text},
            )
            if holding_id:
                try:
                    self._conn.execute(
                        "MATCH (h:Holding {id: $hid}), (t:Thesis {id: $tid}) "
                        "CREATE (h)-[:HAS_THESIS]->(t)",
                        {"hid": holding_id, "tid": thesis_id},
                    )
                except Exception:
                    pass
            if evidence_id:
                try:
                    self._conn.execute(
                        "MATCH (t:Thesis {id: $tid}), (e:Evidence {id: $eid}) "
                        "CREATE (t)-[:GROUNDED_IN]->(e)",
                        {"tid": thesis_id, "eid": evidence_id},
                    )
                except Exception:
                    pass
        except Exception as exc:
            log_debug(
                "KUZU_ADD_THESIS_FAILED",
                payload={"error": str(exc), "thesis_id": thesis_id},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                cycle_id=cycle_id,
                msg=f"KuzuDB add_thesis failed: {exc}",
            )

    def link_similar_lessons(
        self, lesson_id_a: str, lesson_id_b: str, similarity: float
    ) -> None:
        """Create SIMILAR_TO edge between two Lesson nodes."""
        if not self._ensure_connection():
            return
        try:
            self._conn.execute(
                "MATCH (a:Lesson {id: $aid}), (b:Lesson {id: $bid}) "
                "CREATE (a)-[:SIMILAR_TO {similarity: $sim}]->(b)",
                {"aid": lesson_id_a, "bid": lesson_id_b, "sim": similarity},
            )
        except Exception as exc:
            log_debug(
                "KUZU_LINK_SIMILAR_FAILED",
                payload={"error": str(exc), "lesson_a": lesson_id_a},
                level="WARN",
                error_code="KUZU_WRITE_FAILED",
                msg=f"KuzuDB link_similar_lessons failed: {exc}",
            )
