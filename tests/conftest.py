"""Shared test fixtures for PMACS web tests.

Provides a reusable TestClient factory used by accessibility, e2e, and
performance test suites to avoid duplicating schema setup and config patching.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


def _create_tables(conn: sqlite3.Connection, extra_data: bool = False) -> None:
    """Create all tables required by dashboard routes.

    Args:
        conn: SQLite connection.
        extra_data: If True, insert test data (holdings, cycles, universe, etc.).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS holdings (id TEXT, ticker TEXT, state TEXT, "
        "entry_price_usd REAL, position_size_usd REAL, sector TEXT, "
        "verdict TEXT, conviction_score REAL, current_price_usd REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cycles (cycle_id TEXT, opened_at TEXT, "
        "closed_at TEXT, state TEXT, trigger TEXT, mode TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS queue (cycle_id TEXT, ticker TEXT, "
        "priority_band TEXT, pinned INTEGER, enqueued_at TEXT, completed_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mutation_candidates (candidate_id TEXT, "
        "dimension TEXT, target TEXT, proposed_at TEXT, sample_size INTEGER, "
        "effect_size REAL, p_value REAL, trending_direction TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mutation_log (candidate_id TEXT, "
        "dimension TEXT, target TEXT, promoted_at TEXT, promoted_by TEXT, "
        "rolled_back_at TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evidence (ticker TEXT, catalyst_imminence REAL, "
        "thesis_strength REAL, source_brier_avg REAL, portfolio_fit REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS universe (ticker TEXT, sector TEXT, "
        "subsector TEXT, catalyst_type TEXT, pinned_priority INTEGER, "
        "halted INTEGER DEFAULT 0, added_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )

    if extra_data:
        conn.execute(
            "INSERT INTO universe VALUES "
            "('AAPL', 'Tech', 'Software', 'earnings', 0, 0, '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO universe VALUES "
            "('MSFT', 'Tech', 'Cloud', 'earnings', 0, 0, '2026-01-01')"
        )

    conn.commit()


def _make_test_config(tmp_path):
    """Create a DashboardConfig pointing at temp paths."""
    from pmacs.web.config import DashboardConfig

    (tmp_path / "config").mkdir()
    return DashboardConfig(
        sqlite_path=str(tmp_path / "pmacs.db"),
        duckdb_path=str(tmp_path / "analytics.duckdb"),
        audit_path=str(tmp_path / "audit.log"),
        heartbeat_dir=tmp_path / "heartbeats",
        config_dir=str(tmp_path / "config"),
    )


@pytest.fixture
def dashboard_client(tmp_path):
    """FastAPI TestClient with tables created but no data."""
    from fastapi.testclient import TestClient

    conn = sqlite3.connect(str(tmp_path / "pmacs.db"))
    _create_tables(conn, extra_data=False)
    conn.close()

    with patch("pmacs.web.config.get_config", return_value=_make_test_config(tmp_path)):
        from pmacs.web.app import app
        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def dashboard_client_with_data(tmp_path):
    """FastAPI TestClient with synthetic data (holdings, universe, etc.)."""
    from fastapi.testclient import TestClient

    conn = sqlite3.connect(str(tmp_path / "pmacs.db"))
    _create_tables(conn, extra_data=True)
    conn.close()

    with patch("pmacs.web.config.get_config", return_value=_make_test_config(tmp_path)):
        from pmacs.web.app import app
        yield TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def page_urls():
    """All 7 dashboard page URLs."""
    return ["/", "/agents", "/pipeline", "/universe", "/cortex", "/settings", "/debug"]
