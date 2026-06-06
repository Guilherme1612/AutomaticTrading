"""DuckDB analytics adapter — rolling metrics, persona performance, affinity tables.

Gracefully degrades when ``duckdb`` is not installed.  All public methods
return empty/default values and log a warning in stub mode.

Architecture.md §1.8: Both audit and debug logging required.
Architecture.md §8.6: DuckDB analytics tables (rolling windows = episodic memory).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.logsys import log_debug

# Lazy import flag — checked per-instance, not globally poisoned
_duckdb_import_available: bool | None = None


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
        self._connection_failed: bool = False  # per-instance failure tracking

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _ensure_conn(self) -> bool:
        """Try to connect to DuckDB. Returns True if connected."""
        global _duckdb_import_available

        # Import availability is global (package either installed or not)
        if _duckdb_import_available is False:
            return False
        # Connection failure is per-instance (different db_path may work)
        if self._connection_failed:
            return False
        if self._conn is not None:
            return True

        try:
            import duckdb  # type: ignore[import-untyped]

            _duckdb_import_available = True
        except ImportError:
            _duckdb_import_available = False
            log_debug(
                "DUCKDB_UNAVAILABLE",
                payload={"reason": "duckdb not installed"},
                level="INFO",
                msg="DuckDB adapter running in stub mode (duckdb not installed)",
            )
            return False

        try:
            self._conn = duckdb.connect(str(self.db_path))
            return True
        except Exception as exc:
            log_debug(
                "DUCKDB_CONNECTION_FAILED",
                payload={"error": str(exc), "db_path": str(self.db_path)},
                level="WARN",
                error_code="DUCKDB_CONNECTION_FAILED",
                msg=f"DuckDB connection failed: {exc}",
            )
            self._conn = None
            self._connection_failed = True  # per-instance, not global
            return False

    def _get_conn(self):
        """Get the connection, initializing if needed. Returns None if unavailable."""
        if self._ensure_conn():
            return self._conn
        return None

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    def init_tables(self) -> None:
        """Create analytics tables if they do not exist."""
        conn = self._get_conn()
        if conn is None:
            log_debug(
                "DUCKDB_TABLES_INITIALIZED",
                payload={
                    "tables": [
                        "rolling_metrics",
                        "persona_performance",
                        "persona_ticker_affinity",
                        "failure_taxonomy_counts",
                        "resolutions_history",
                        "persona_subsector_affinity",
                        "evidence_archive",
                        "scan_records",
                    ],
                    "stub": True,
                },
                level="INFO",
                msg="DuckDB tables stub-logged (duckdb not installed)",
            )
            return

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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_usage (
                call_id VARCHAR PRIMARY KEY,
                cycle_id VARCHAR NOT NULL,
                persona VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                generation_id VARCHAR,
                called_at TIMESTAMP NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                cached_tokens INTEGER DEFAULT 0,
                estimated_cost_usd DOUBLE NOT NULL,
                body_cost_usd DOUBLE NOT NULL,
                actual_cost_usd DOUBLE,
                latency_ms INTEGER NOT NULL,
                succeeded BOOLEAN NOT NULL,
                retry_count INTEGER DEFAULT 0,
                error_code VARCHAR
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_usage_cycle ON api_usage(cycle_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_usage_called_at ON api_usage(called_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_usage_persona ON api_usage(persona)"
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
                "api_usage",
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
        if conn is None:
            return []
        try:
            if params:
                result = conn.execute(query, params)
            else:
                result = conn.execute(query)
            columns = [desc[0] for desc in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]
        except Exception as exc:
            log_debug(
                "DUCKDB_QUERY_FAILED",
                payload={"error": str(exc), "query": query[:200]},
                level="WARN",
                error_code="DUCKDB_QUERY_FAILED",
                msg=f"DuckDB query failed: {exc}",
            )
            return []

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------

    def update_persona_affinity(self, persona: str, ticker: str, brier: float) -> None:
        """Update rolling persona-ticker affinity (upsert)."""
        conn = self._get_conn()
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "persona_ticker_affinity",
                    "persona": persona,
                    "ticker": ticker,
                    "brier": brier,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

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
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "resolutions_history",
                    "resolution_id": resolution_id,
                    "holding_id": holding_id,
                    "ticker": ticker,
                    "cycle_id": cycle_id,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

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
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "persona_subsector_affinity",
                    "persona": persona,
                    "subsector": subsector,
                    "brier": brier,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

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
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "evidence_archive",
                    "evidence_id": evidence_id,
                    "ticker": ticker,
                    "source": source,
                    "cycle_id": cycle_id,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

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
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "scan_records",
                    "cycle_id": cycle_id,
                    "ticker": ticker,
                    "source_count": source_count,
                    "evidence_count": evidence_count,
                    "has_stale_data": has_stale_data,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

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

    # ------------------------------------------------------------------
    # Post-cycle persistence helpers
    # ------------------------------------------------------------------

    def insert_persona_performance(
        self,
        persona: str,
        cycle_id: str,
        ticker: str,
        p_up: float,
        p_flat: float,
        p_down: float,
        brier: float,
        direction_correct: bool,
    ) -> None:
        """Record per-persona Brier score for a cycle (step 19)."""
        conn = self._get_conn()
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "persona_performance",
                    "persona": persona,
                    "ticker": ticker,
                    "brier": brier,
                    "cycle_id": cycle_id,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

        conn.execute(
            """
            INSERT INTO persona_performance
                (persona, cycle_id, ticker, p_up, p_flat, p_down,
                 brier, direction_correct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [persona, cycle_id, ticker, p_up, p_flat, p_down, brier, direction_correct],
        )

        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "persona_performance",
                "persona": persona,
                "ticker": ticker,
                "brier": brier,
                "cycle_id": cycle_id,
            },
            level="DEBUG",
        )

    def insert_rolling_metric(self, cycle_id: str, metric_name: str, metric_value: float) -> None:
        """Insert a rolling metric snapshot (steps 21, 24)."""
        conn = self._get_conn()
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "rolling_metrics",
                    "metric_name": metric_name,
                    "metric_value": metric_value,
                    "cycle_id": cycle_id,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

        conn.execute(
            "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value) VALUES (?, ?, ?)",
            [cycle_id, metric_name, metric_value],
        )

        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "rolling_metrics",
                "metric_name": metric_name,
                "metric_value": metric_value,
                "cycle_id": cycle_id,
            },
            level="DEBUG",
        )

    def insert_failure_taxonomy_count(
        self,
        taxonomy: str,
        cycle_id: str,
        window_start: str,
        window_end: str,
    ) -> None:
        """Record a failure taxonomy classification count (step 25)."""
        conn = self._get_conn()
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "failure_taxonomy_counts",
                    "taxonomy": taxonomy,
                    "cycle_id": cycle_id,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

        conn.execute(
            """
            INSERT INTO failure_taxonomy_counts
                (taxonomy, cycle_id, count, window_start, window_end)
            VALUES (?, ?, 1, ?, ?)
            """,
            [taxonomy, cycle_id, window_start, window_end],
        )

        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "failure_taxonomy_counts",
                "taxonomy": taxonomy,
                "cycle_id": cycle_id,
            },
            level="DEBUG",
        )

    def insert_api_usage(
        self,
        call_id: str,
        cycle_id: str,
        persona: str,
        model_id: str,
        generation_id: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_usd: float,
        body_cost_usd: float,
        latency_ms: int,
        succeeded: bool,
        retry_count: int = 0,
        error_code: str | None = None,
    ) -> None:
        """Record an LLM API call's usage data (Phase 16)."""
        conn = self._get_conn()
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={
                    "table": "api_usage",
                    "call_id": call_id,
                    "persona": persona,
                    "cycle_id": cycle_id,
                    "stub": True,
                },
                level="DEBUG",
            )
            return

        conn.execute(
            """
            INSERT INTO api_usage
                (call_id, cycle_id, persona, model_id, generation_id,
                 called_at, prompt_tokens, completion_tokens,
                 estimated_cost_usd, body_cost_usd, latency_ms,
                 succeeded, retry_count, error_code)
            VALUES (?, ?, ?, ?, ?, current_timestamp, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                call_id, cycle_id, persona, model_id, generation_id,
                prompt_tokens, completion_tokens,
                estimated_cost_usd, body_cost_usd, latency_ms,
                succeeded, retry_count, error_code,
            ],
        )

        log_debug(
            "DUCKDB_WRITE",
            payload={
                "table": "api_usage",
                "call_id": call_id,
                "persona": persona,
                "cycle_id": cycle_id,
                "body_cost_usd": body_cost_usd,
            },
            level="DEBUG",
        )

    def update_actual_cost(self, call_id: str, actual_cost_usd: float) -> None:
        """Update actual_cost_usd after reconciliation (Phase 16)."""
        conn = self._get_conn()
        if conn is None:
            log_debug(
                "DUCKDB_WRITE_STUB",
                payload={"table": "api_usage", "call_id": call_id, "op": "update_actual_cost", "stub": True},
                level="DEBUG",
            )
            return

        conn.execute(
            "UPDATE api_usage SET actual_cost_usd = ? WHERE call_id = ?",
            [actual_cost_usd, call_id],
        )

        log_debug(
            "DUCKDB_WRITE",
            payload={"table": "api_usage", "call_id": call_id, "actual_cost_usd": actual_cost_usd},
            level="DEBUG",
        )

    def close(self) -> None:
        """Close the DuckDB connection if open."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
