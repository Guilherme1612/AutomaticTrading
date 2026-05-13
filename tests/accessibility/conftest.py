"""Shared fixtures for accessibility tests.

Provides a TestClient-based dashboard fixture for testing pages
without requiring Playwright or a running server.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def dashboard_client(tmp_path):
    """Create a FastAPI TestClient with synthetic data for accessibility testing."""
    from fastapi.testclient import TestClient

    # Create a temp SQLite DB with minimal data
    db_path = tmp_path / "pmacs.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS holdings (id TEXT, ticker TEXT, state TEXT, "
        "entry_price_usd REAL, position_size_usd REAL, sector TEXT, "
        "verdict TEXT, conviction_score REAL)"
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
    conn.execute("INSERT INTO universe VALUES ('AAPL', 'Tech', 'Software', 'earnings', 0, 0, '2026-01-01')")
    conn.execute("INSERT INTO universe VALUES ('MSFT', 'Tech', 'Cloud', 'earnings', 0, 0, '2026-01-01')")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    conn.close()

    # Patch config to use temp paths
    from pmacs.web.config import DashboardConfig

    test_config = DashboardConfig(
        sqlite_path=str(db_path),
        duckdb_path=str(tmp_path / "analytics.duckdb"),
        audit_path=str(tmp_path / "audit.log"),
        heartbeat_dir=tmp_path / "heartbeats",
        config_dir=str(tmp_path / "config"),
    )
    (tmp_path / "config").mkdir()

    with patch("pmacs.web.config.get_config", return_value=test_config):
        from pmacs.web.app import app
        client = TestClient(app, raise_server_exceptions=False)
        yield client


@pytest.fixture
def page_urls():
    """All 7 dashboard page URLs."""
    return [
        "/",
        "/agents",
        "/pipeline",
        "/universe",
        "/cortex",
        "/settings",
        "/debug",
    ]
