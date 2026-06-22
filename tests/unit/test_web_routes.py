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
from pmacs.web.config import DashboardConfig, configure, get_config


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
    conn.execute(
        "INSERT INTO decisions (cycle_id, ticker, verdict, conviction_score, decided_at) VALUES (?, ?, ?, ?, ?)",
        ("c1", "TSLA", "HOLD", 0.42, "2024-01-01T11:00:00"),
    )
    add_ticker(conn, UniverseEntry(ticker="AAPL", sector="Technology"))
    add_ticker(conn, UniverseEntry(ticker="MSFT", sector="Technology"))
    add_ticker(conn, UniverseEntry(ticker="TSLA", sector="Technology"))
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
    _original = get_config()
    configure(cfg)
    yield
    # Restore the global config so this file's tmp paths don't leak into
    # later tests (the tmp_path is deleted after the test).
    configure(_original)


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

    def test_universe_data_link_only_for_cycled_tickers(self):
        """Both the data link and the memo link are gated on has_been_cycled.

        A ticker that has not been analyzed in any cycle (no evidence, no
        holdings, no decisions) has no useful data on /ticker/{symbol} and
        must not be linked from the universe page — otherwise the link
        leads to a polite but empty state, which is a dead-end.
        """
        with TestClient(app) as client:
            response = client.get("/universe")
            text = response.text
            assert response.status_code == 200
            # AAPL has a holding, TSLA has a decision -> both cycled -> data link shown
            assert '/ticker/AAPL' in text
            assert '/ticker/TSLA' in text
            # MSFT has no cycle data -> data link hidden
            assert '/ticker/MSFT' not in text

    def test_universe_memo_link_only_for_cycled_tickers(self):
        with TestClient(app) as client:
            response = client.get("/universe")
            text = response.text
            assert response.status_code == 200
            # AAPL has a holding, TSLA has a decision -> both cycled -> memo link
            # shown (now deep-links into the unified ticker workspace §16.6 #memo).
            assert '/ticker/AAPL#memo' in text
            assert '/ticker/TSLA#memo' in text
            # MSFT has no cycle data -> memo link hidden
            assert '/ticker/MSFT#memo' not in text


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


class TestDashboardPartials:
    """SSE-driven dashboard region partials (Source.md §14, Architecture.md §4.4).

    Each partial returns one region's HTML and is self-rewiring — the root
    container carries its own hx-get/hx-trigger/hx-swap so an outerHTML swap
    leaves it live for the next `pmacs:refresh` event.
    """

    REGIONS = ("positions", "decisions", "health", "mutation")

    def test_each_partial_returns_200(self):
        with TestClient(app) as client:
            for region in self.REGIONS:
                resp = client.get(f"/api/dashboard/partials/{region}")
                assert resp.status_code == 200, f"{region}: {resp.status_code}"

    def test_each_partial_is_self_rewiring(self):
        with TestClient(app) as client:
            for region in self.REGIONS:
                resp = client.get(f"/api/dashboard/partials/{region}")
                # Root container must carry its own hx-get so the swapped-in
                # fragment stays subscribed to pmacs:refresh.
                assert f'hx-get="/api/dashboard/partials/{region}"' in resp.text
                assert "hx-trigger=\"pmacs:refresh from:body\"" in resp.text
                assert 'hx-swap="outerHTML"' in resp.text

    def test_unknown_region_404s(self):
        with TestClient(app) as client:
            resp = client.get("/api/dashboard/partials/nonexistent")
            assert resp.status_code == 404

    def test_positions_partial_renders_force_exit_for_active_holding(self):
        """§16.4: the active-positions table exposes a Force-exit button per row."""
        with TestClient(app) as client:
            resp = client.get("/api/dashboard/partials/positions")
            assert resp.status_code == 200
            # The autouse fixture inserts an ACTIVE AAPL holding → button renders.
            assert "forceExit('AAPL')" in resp.text


class TestSettingsSection20:
    """§20 Settings expansion (Source.md §20.2–§20.12): section anchors + the
    operator-confirmed TOML-writing routes (risk/crucible/brokers/operator).

    Each operator-confirmed change must persist to the relevant TOML atomically
    and be typed-confirm gated client-side (see test_operator_confirm_gates.py).
    """

    ANCHORS = (
        "general", "brokers", "inference", "budget", "universe", "risk",
        "crucible", "mutations", "personas", "queue", "audit", "operator",
        "notifications", "reset",
    )

    def test_settings_renders_all_section20_anchors(self):
        with TestClient(app) as client:
            resp = client.get("/settings")
            assert resp.status_code == 200
            for anchor in self.ANCHORS:
                assert f'id="{anchor}"' in resp.text, f"settings.html missing §20 anchor #{anchor}"

    def test_settings_subnav_links_to_section20_index(self):
        with TestClient(app) as client:
            resp = client.get("/settings")
            # The §20.1 sub-nav must link every subsection.
            assert 'href="#risk"' in resp.text
            assert 'href="#brokers"' in resp.text
            assert 'href="#operator"' in resp.text

    def test_risk_route_writes_toml(self, tmp_path, monkeypatch):
        """§20.6 — POST /api/settings/risk writes risk.toml [position]."""
        import os
        from pmacs.web.config import get_config, configure
        cfg = get_config()
        risk_path = cfg.config_dir / "risk.toml"
        with TestClient(app) as client:
            resp = client.post("/api/settings/risk", json={"max_single_position_pct": 0.25})
            assert resp.status_code == 200, resp.text
            assert resp.json()["ok"] is True
            # On disk
            import tomllib
            with open(risk_path, "rb") as f:
                data = tomllib.load(f)
            assert data["position"]["max_single_position_pct"] == 0.25
            # Read-back via GET
            get = client.get("/api/settings/risk")
            assert get.json()["risk"]["max_single_position_pct"] == 0.25

    def test_risk_route_validates_range(self):
        with TestClient(app) as client:
            resp = client.post("/api/settings/risk", json={"max_single_position_pct": 2.0})
            assert resp.status_code == 400

    def test_crucible_route_writes_toml(self):
        """§20.7 — POST /api/settings/crucible writes crucible.toml [time_budget]."""
        cfg = get_config()
        crucible_path = cfg.config_dir / "crucible.toml"
        with TestClient(app) as client:
            resp = client.post("/api/settings/crucible", json={"seconds_per_attack": 120, "max_cycles": 3})
            assert resp.status_code == 200, resp.text
            import tomllib
            with open(crucible_path, "rb") as f:
                data = tomllib.load(f)
            assert data["time_budget"]["seconds_per_attack"] == 120
            assert data["time_budget"]["max_cycles"] == 3

    def test_brokers_route_writes_catastrophe_net(self):
        """§20.3 — POST /api/settings/brokers writes risk.toml [pricing]."""
        cfg = get_config()
        risk_path = cfg.config_dir / "risk.toml"
        with TestClient(app) as client:
            resp = client.post("/api/settings/brokers", json={"catastrophe_net_stop_pct": 0.20})
            assert resp.status_code == 200, resp.text
            import tomllib
            with open(risk_path, "rb") as f:
                data = tomllib.load(f)
            assert data["pricing"]["default_stop_loss_pct"] == 0.20

    def test_operator_route_writes_per_trade_approval(self):
        """§20.12 — POST /api/settings/operator writes risk.toml [operator]."""
        cfg = get_config()
        risk_path = cfg.config_dir / "risk.toml"
        with TestClient(app) as client:
            resp = client.post("/api/settings/operator", json={"per_trade_approval": True})
            assert resp.status_code == 200, resp.text
            import tomllib
            with open(risk_path, "rb") as f:
                data = tomllib.load(f)
            assert data["operator"]["per_trade_approval"] is True

    def test_cost_settings_template_removed(self):
        """Finding I: the dead cost_settings.html template is folded into Settings."""
        assert not (Path(__file__).resolve().parents[2] / "pmacs" / "web" / "templates" / "cost_settings.html").exists()
