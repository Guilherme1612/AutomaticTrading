"""DuckDB analytics adapter — rolling metrics, persona performance, affinity tables.

Architecture.md §1.8: Both audit and debug logging required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.logsys import log_debug


class DuckDBAdapter:
    """Adapter for DuckDB analytics tables.

    Tables created by :meth:`init_tables`:

    * ``rolling_metrics``     — per-cycle metric snapshots
    * ``persona_performance`` — per-persona per-cycle Brier scores
    * ``persona_ticker_affinity`` — rolling persona-ticker affinity
    * ``failure_taxonomy_counts`` — FDE taxonomy histogram
    * ``resolutions_history``     — per-resolution outcome tracking
    * ``persona_subsector_affinity`` — rolling persona-subsector affinity
    * ``evidence_archive``        — historical evidence snapshots
    * ``scan_records``            — per-cycle scan metadata
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or Path("pmacs_analytics.duckdb")
        self._conn: Any = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_conn(self):
        if self._conn is None:
            import duckdb

            self._conn = duckdb.connect(str(self.db_path))
        return self._conn

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    def init_tables(self) -> None:
        """Create analytics tables if they do not exist."""
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rolling_metrics (
                cycle_id    VARCHAR,
                metric_name VARCHAR,
                metric_value DOUBLE,
                computed_at TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_performance (
                persona  VARCHAR,
                cycle_id VARCHAR,
                ticker   VARCHAR,
                p_up     DOUBLE,
                p_flat   DOUBLE,
                p_down   DOUBLE,
                brier    DOUBLE,
                direction_correct BOOLEAN,
                computed_at TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_ticker_affinity (
                persona     VARCHAR,
                ticker      VARCHAR,
                avg_brier   DOUBLE,
                cycle_count INTEGER,
                updated_at  TIMESTAMP DEFAULT current_timestamp,
                PRIMARY KEY (persona, ticker)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS failure_taxonomy_counts (
                taxonomy    VARCHAR,
                cycle_id    VARCHAR,
                count       INTEGER DEFAULT 1,
                window_start TIMESTAMP,
                window_end   TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resolutions_history (
                resolution_id      VARCHAR,
                holding_id         VARCHAR,
                ticker             VARCHAR,
                catalyst_type      VARCHAR,
                direction          VARCHAR,
                expected_move_pct  DOUBLE,
                actual_move_pct    DOUBLE,
                resolution_quality VARCHAR,
                resolved_at        TIMESTAMP DEFAULT current_timestamp
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS persona_subsector_affinity (
                persona     VARCHAR,
                subsector   VARCHAR,
                avg_brier   DOUBLE,
                cycle_count INTEGER,
                updated_at  TIMESTAMP DEFAULT current_timestamp,
                PRIMARY KEY (persona, subsector)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence_archive (
                evidence_id  VARCHAR,
                ticker       VARCHAR,
                source       VARCHAR,
                fetched_at   TIMESTAMP DEFAULT current_timestamp,
                content_hash VARCHAR,
                data_json    VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_records (
                cycle_id       VARCHAR,
                ticker         VARCHAR,
                scan_timestamp TIMESTAMP DEFAULT current_timestamp,
                source_count   INTEGER,
                evidence_count INTEGER,
                has_stale_data BOOLEAN
            )
            """
        )

        # Audit event — tables initialized (Architecture.md §1.8)
        log_debug(
            "DUCKDB_TABLES_INITIALIZED",
            payload={"tables": [
                "rolling_metrics",
                "persona_performance",
                "persona_ticker_affinity",
                "failure_taxonomy_counts",
                "resolutions_history",
                "persona_subsector_affinity",
                "evidence_archive",
                "scan_records",
            ]},
            level="INFO",
            msg="DuckDB analytics tables initialized",
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def execute(self, query: str, params: list | None = None) -> list[dict]:
        """Execute a query and return results as list of dicts."""
        conn = self._get_conn()
        if params:
            result = conn.execute(query, params)
        else:
            result = conn.execute(query)
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------

    def update_persona_affinity(self, persona: str, ticker: str, brier: float) -> None:
        """Update rolling persona-ticker affinity (upsert)."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO persona_ticker_affinity (persona, ticker, avg_brier, cycle_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT (persona, ticker) DO UPDATE SET
                avg_brier = (avg_brier * cycle_count + ?) / (cycle_count + 1),
                cycle_count = cycle_count + 1,
                updated_at = now()
            """,
            [persona, ticker, brier, brier],
        )

        # Debug event — affinity write (Architecture.md §1.8)
        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "persona_ticker_affinity",
                "persona": persona,
                "ticker": ticker,
                "brier": brier,
            },
            level="DEBUG",
        )

    def insert_resolution(
        self,
        resolution_id: str,
        holding_id: str,
        ticker: str,
        catalyst_type: str,
        direction: str,
        expected_move_pct: float,
        actual_move_pct: float,
        resolution_quality: str,
        cycle_id: str = "",
    ) -> None:
        """Record a resolution outcome for tracking accuracy over time."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO resolutions_history
                (resolution_id, holding_id, ticker, catalyst_type,
                 direction, expected_move_pct, actual_move_pct,
                 resolution_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                resolution_id, holding_id, ticker, catalyst_type,
                direction, expected_move_pct, actual_move_pct,
                resolution_quality,
            ],
        )

        # Debug event — resolution write (Architecture.md §1.8)
        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "resolutions_history",
                "resolution_id": resolution_id,
                "holding_id": holding_id,
                "ticker": ticker,
                "cycle_id": cycle_id,
            },
            level="DEBUG",
        )

    def update_subsector_affinity(
        self, persona: str, subsector: str, brier: float,
    ) -> None:
        """Update rolling persona-subsector affinity (upsert)."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO persona_subsector_affinity (persona, subsector, avg_brier, cycle_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT (persona, subsector) DO UPDATE SET
                avg_brier = (avg_brier * cycle_count + ?) / (cycle_count + 1),
                cycle_count = cycle_count + 1,
                updated_at = now()
            """,
            [persona, subsector, brier, brier],
        )

        # Debug event — subsector affinity write (Architecture.md §1.8)
        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "persona_subsector_affinity",
                "persona": persona,
                "subsector": subsector,
                "brier": brier,
            },
            level="DEBUG",
        )

    def archive_evidence(
        self,
        evidence_id: str,
        ticker: str,
        source: str,
        content_hash: str,
        data_json: str,
        cycle_id: str = "",
    ) -> None:
        """Archive an evidence snapshot for historical analysis."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO evidence_archive
                (evidence_id, ticker, source, content_hash, data_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [evidence_id, ticker, source, content_hash, data_json],
        )

        # Debug event — evidence archive write (Architecture.md §1.8)
        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "evidence_archive",
                "evidence_id": evidence_id,
                "ticker": ticker,
                "source": source,
                "cycle_id": cycle_id,
            },
            level="DEBUG",
        )

    def insert_scan_record(
        self,
        cycle_id: str,
        ticker: str,
        source_count: int,
        evidence_count: int,
        has_stale_data: bool,
    ) -> None:
        """Record per-cycle scan metadata for data quality tracking."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO scan_records
                (cycle_id, ticker, source_count, evidence_count, has_stale_data)
            VALUES (?, ?, ?, ?, ?)
            """,
            [cycle_id, ticker, source_count, evidence_count, has_stale_data],
        )

        # Debug event — scan record write (Architecture.md §1.8)
        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "scan_records",
                "cycle_id": cycle_id,
                "ticker": ticker,
                "source_count": source_count,
                "evidence_count": evidence_count,
                "has_stale_data": has_stale_data,
            },
            level="DEBUG",
        )
