"""DuckDB analytics adapter — rolling metrics, persona performance, affinity tables."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class DuckDBAdapter:
    """Adapter for DuckDB analytics tables.

    Tables created by :meth:`init_tables`:

    * ``rolling_metrics``     — per-cycle metric snapshots
    * ``persona_performance`` — per-persona per-cycle Brier scores
    * ``persona_ticker_affinity`` — rolling persona-ticker affinity
    * ``failure_taxonomy_counts`` — FDE taxonomy histogram
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
