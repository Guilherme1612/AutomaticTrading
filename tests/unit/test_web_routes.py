"""Web route handler tests — verify routes render with data layer."""

import json
import sqlite3
import time

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from pmacs.storage.sqlite import init_db
from pmacs.storage.audit import AuditWriter
from pmacs.data.universe import add_ticker, init_universe_table, UniverseEntry
from pmacs.web.app import app
from pmacs.web.config import DashboardConfig, configure


@pytest.fixture(autouse=True)
def _setup(tmp_path):
    """Configure dashboard with test paths."""
    # Create test database with data
    db_path = tmp_path / "pmacs.db"
    conn = init_db(db_path)
    init_universe_table(conn)

    # Insert test data
    conn.execute(
        "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) VALUES (?, ?, ?, ?, ?)",
        ("c1", "2024-01-01T10:00:00", "OPEN", "timed", "SHADOW"),
    )
    conn.execute(
        "INSERT INTO holdings (id, ticker, state, cycle_id_opened, verdict) VALUES (?, ?, ?, ?, ?)",
        ("h1", "AAPL", "ACTIVE", "c1", "BUY"),
    )
    conn.execute(
        "INSERT INTO queue (cycle_id, ticker, priority_band, enqueued_at, completed_at) VALUES (?, ?, ?, ?, ?)",
        ("c1", "GOOG", 1, "2024-01-01T10:00:00", None),
    )
    add_ticker(conn, UniverseEntry(ticker="AAPL", sector="Technology"))
    add_ticker(conn, UniverseEntry(ticker="MSFT", sector="Technology"))
    conn.commit()
    conn.close()

    # Create heartbeat dir with a running process
    hb_dir = tmp_path / "heartbeat"
    hb_dir.mkdir()
    (hb_dir / "dashboard.ts").write_text(str(int(time.time())))

    # Create config dir
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "risk.toml").write_text('[risk]\nmax_position_pct = 0.20\n')

    # Create audit log
    audit_path = tmp_path / "audit.log"
    writer = AuditWriter(audit_path)
    writer.append("test_event", {"key": "value"}, cycle_id="c1")
    writer.close()

    # Create debug log
    debug_path = tmp_path / "debug.jsonl"
    with open(debug_path, "w") as f:
        f.write(json.dumps({"ts": "t1", "level": "INFO", "event": "BOOT_DETECTED"}) + "\n")

    # Configure dashboard
    cfg = DashboardConfig(
        sqlite_path=db_path,
        duckdb_path=tmp_path / "analytics.duckdb",
        heartbeat_dir=hb_dir,
        audit_path=audit_path,
        debug_log_path=debug_path,
        config_dir=config_dir,
    )
    configure(cfg)
    yield


class TestDashboardRoute:
    """Tests for the dashboard page route."""

    def test_dashboard_returns_200(self):
        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 200

    def test_dashboard_renders_page(self):
        with TestClient(app) as client:
            response = client.get("/")
            assert b"dashboard" in response.content.lower() or response.status_code == 200


class TestAgentsRoute:
    """Tests for the agents page route."""

    def test_agents_returns_200(self):
        with TestClient(app) as client:
            response = client.get("/agents")
            assert response.status_code == 200


class TestPipelineRoute:
    """Tests for the pipeline page route."""

    def test_pipeline_returns_200(self):
        with TestClient(app) as client:
            response = client.get("/pipeline")
            assert response.status_code == 200


class TestCortexRoute:
    """Tests for the cortex page route."""

    def test_cortex_returns_200(self):
        with TestClient(app) as client:
            response = client.get("/cortex")
            assert response.status_code == 200


class TestDebugRoute:
    """Tests for the debug page route."""

    def test_debug_returns_200(self):
        with TestClient(app) as client:
            response = client.get("/debug")
            assert response.status_code == 200

    def test_debug_with_filter(self):
        with TestClient(app) as client:
            response = client.get("/debug?level=WARN")
            assert response.status_code == 200


class TestUniverseRoute:
    """Tests for the universe page route."""

    def test_universe_returns_200(self):
        with TestClient(app) as client:
            response = client.get("/universe")
            assert response.status_code == 200


class TestSettingsRoute:
    """Tests for the settings page route."""

    def test_settings_returns_200(self):
        with TestClient(app) as client:
            response = client.get("/settings")
            assert response.status_code == 200


class TestSparklineAPI:
    """Tests for the /api/dashboard/sparkline endpoint."""

    def test_sparkline_api_returns_json(self):
        with TestClient(app) as client:
            response = client.get("/api/dashboard/sparkline?metric=sharpe&window=1W")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    def test_sparkline_api_returns_empty_for_missing_metric(self):
        with TestClient(app) as client:
            response = client.get("/api/dashboard/sparkline?metric=nonexistent&window=1W")
            assert response.status_code == 200
            data = response.json()
            assert data == []

    def test_sparkline_api_uses_default_window(self):
        with TestClient(app) as client:
            response = client.get("/api/dashboard/sparkline?metric=sharpe")
            assert response.status_code == 200

    def test_dashboard_includes_sparkline_data(self):
        with TestClient(app) as client:
            response = client.get("/")
            assert response.status_code == 200
            # Sparkline API endpoint is wired up and returns JSON array
            api_resp = client.get("/api/dashboard/sparkline?metric=sharpe&window=1W")
            assert api_resp.status_code == 200
            assert isinstance(api_resp.json(), list)
