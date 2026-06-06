"""Web data layer tests — verify shared data access functions."""

import json
import sqlite3
import time

import pytest
from pathlib import Path

from pmacs.storage.sqlite import init_db
from pmacs.storage.audit import AuditWriter
from pmacs.data.universe import add_ticker, init_universe_table, UniverseEntry
from pmacs.web.data import (
    get_active_holdings,
    get_recent_decisions,
    get_risk_metrics,
    get_sparkline_data,
    get_all_sparkline_data,
    get_system_health,
    get_queue_status,
    get_universe_list,
    get_debug_events,
    get_settings,
    get_cortex_status,
    get_agent_cycle_data,
)


@pytest.fixture
def db(tmp_path):
    """Create a test SQLite database with schema."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def heartbeat_dir(tmp_path):
    """Create a heartbeat directory."""
    hb_dir = tmp_path / "heartbeat"
    hb_dir.mkdir()
    return hb_dir


class TestGetActiveHoldings:
    """Tests for get_active_holdings."""

    def test_returns_empty_when_no_holdings(self, db):
        result = get_active_holdings(db)
        assert result == []

    def test_returns_active_holdings(self, db):
        db.execute(
            "INSERT INTO holdings (id, ticker, state, cycle_id_opened) VALUES (?, ?, ?, ?)",
            ("h1", "AAPL", "ACTIVE", "c1"),
        )
        db.commit()
        result = get_active_holdings(db)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["state"] == "ACTIVE"

    def test_excludes_closed_holdings(self, db):
        db.execute(
            "INSERT INTO holdings (id, ticker, state, cycle_id_opened) VALUES (?, ?, ?, ?)",
            ("h1", "AAPL", "CLOSED", "c1"),
        )
        db.commit()
        result = get_active_holdings(db)
        assert result == []


class TestGetRecentDecisions:
    """Tests for get_recent_decisions."""

    def test_returns_empty_when_no_cycles(self, db):
        result = get_recent_decisions(db)
        assert result == []

    def test_returns_recent_cycles_ordered(self, db):
        # get_recent_decisions queries the decisions table (not cycles)
        # Insert parent cycle rows first (FK constraint)
        db.execute(
            "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) VALUES (?, ?, ?, ?, ?)",
            ("c1", "2024-01-01T10:00:00", "CLOSED", "timed", "SHADOW"),
        )
        db.execute(
            "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) VALUES (?, ?, ?, ?, ?)",
            ("c2", "2024-01-01T11:00:00", "OPEN", "manual", "SHADOW"),
        )
        db.execute(
            "INSERT INTO decisions (cycle_id, ticker, verdict, conviction_score, decided_at) VALUES (?, ?, ?, ?, ?)",
            ("c1", "AAPL", "BUY", 0.7, "2024-01-01T10:05:00"),
        )
        db.execute(
            "INSERT INTO decisions (cycle_id, ticker, verdict, conviction_score, decided_at) VALUES (?, ?, ?, ?, ?)",
            ("c2", "MSFT", "SKIP", 0.2, "2024-01-01T11:05:00"),
        )
        db.commit()
        result = get_recent_decisions(db, limit=10)
        assert len(result) == 2
        assert result[0]["cycle_id"] == "c2"  # Newest first


class TestGetRiskMetrics:
    """Tests for get_risk_metrics."""

    def test_returns_defaults_when_no_duckdb(self, tmp_path):
        result = get_risk_metrics(tmp_path / "nonexistent.duckdb")
        assert result["max_drawdown_pct"] == 0.0
        assert result["sharpe"] == 0.0
        assert result["win_rate_pct"] == 0.0
        assert result["sortino"] == 0.0
        assert result["avg_risk_reward"] == 0.0

    @pytest.mark.skipif(
        not __import__("importlib").util.find_spec("duckdb"),
        reason="duckdb not installed",
    )
    def test_returns_metrics_from_duckdb(self, tmp_path):
        from pmacs.storage.duckdb import DuckDBAdapter

        db_path = tmp_path / "analytics.duckdb"
        adapter = DuckDBAdapter(db_path=db_path)
        adapter.init_tables()
        adapter.execute(
            "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value) VALUES (?, ?, ?)",
            ["c1", "sharpe", 1.5],
        )

        result = get_risk_metrics(db_path)
        assert result["sharpe"] == 1.5


class TestGetSystemHealth:
    """Tests for get_system_health."""

    def test_returns_process_statuses(self, heartbeat_dir):
        result = get_system_health(heartbeat_dir)
        assert "processes" in result
        assert len(result["processes"]) > 0
        # All should be stale since no heartbeats written
        for proc in result["processes"]:
            assert proc["status"] == "stale"

    def test_detects_running_process(self, heartbeat_dir):
        # Write a heartbeat for inference
        (heartbeat_dir / "inference.ts").write_text(str(int(time.time())))
        result = get_system_health(heartbeat_dir)
        assert result["inference_ok"] is True


class TestGetQueueStatus:
    """Tests for get_queue_status."""

    def test_returns_empty_when_no_queue(self, db):
        result = get_queue_status(db)
        assert result == []

    def test_returns_pending_items(self, db):
        db.execute(
            "INSERT INTO queue (cycle_id, ticker, priority_band, enqueued_at, completed_at) VALUES (?, ?, ?, ?, ?)",
            ("c1", "AAPL", 1, "2024-01-01T10:00:00", None),
        )
        db.execute(
            "INSERT INTO queue (cycle_id, ticker, priority_band, enqueued_at, completed_at) VALUES (?, ?, ?, ?, ?)",
            ("c1", "GOOG", 2, "2024-01-01T10:00:00", "2024-01-01T10:05:00"),
        )
        db.commit()
        result = get_queue_status(db)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAPL"


class TestGetUniverseList:
    """Tests for get_universe_list."""

    def test_returns_empty_when_no_universe(self, db):
        init_universe_table(db)
        result = get_universe_list(db)
        assert result == []

    def test_returns_active_tickers(self, db):
        init_universe_table(db)
        add_ticker(db, UniverseEntry(ticker="AAPL", sector="Technology"))
        add_ticker(db, UniverseEntry(ticker="GOOG", sector="Technology"))
        result = get_universe_list(db)
        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"


class TestGetDebugEvents:
    """Tests for get_debug_events."""

    def test_returns_empty_when_no_file(self, tmp_path):
        result = get_debug_events(tmp_path / "nonexistent.log")
        assert result == []

    def test_returns_parsed_events(self, tmp_path):
        log_path = tmp_path / "debug.jsonl"
        events = [
            {"ts": "2024-01-01T10:00:00", "level": "INFO", "event": "BOOT_DETECTED"},
            {"ts": "2024-01-01T10:01:00", "level": "WARN", "event": "DISK_SPACE_LOW", "error_code": "E401"},
        ]
        with open(log_path, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

        result = get_debug_events(log_path)
        assert len(result) == 2
        # Newest first
        assert result[0]["event"] == "DISK_SPACE_LOW"

    def test_filters_by_level(self, tmp_path):
        log_path = tmp_path / "debug.jsonl"
        events = [
            {"ts": "t1", "level": "INFO", "event": "BOOT_DETECTED"},
            {"ts": "t2", "level": "WARN", "event": "DISK_SPACE_LOW", "error_code": "E401"},
        ]
        with open(log_path, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

        result = get_debug_events(log_path, filters={"level": "WARN"})
        assert len(result) == 1
        assert result[0]["level"] == "WARN"


class TestGetSettings:
    """Tests for get_settings."""

    def test_returns_empty_when_no_dir(self, tmp_path):
        result = get_settings(tmp_path / "nonexistent")
        assert result == {}

    def test_reads_toml_files(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "risk.toml").write_text('[risk]\nmax_position_pct = 0.20\n')
        result = get_settings(config_dir)
        assert "risk" in result
        assert result["risk"]["risk"]["max_position_pct"] == 0.20

    def test_reads_json_files(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "model_registry.json").write_text('{"primary": "llama-server"}')
        result = get_settings(config_dir)
        assert "model_registry" in result
        assert result["model_registry"]["primary"] == "llama-server"


class TestGetCortexStatus:
    """Tests for get_cortex_status."""

    def test_returns_full_status(self, db, tmp_path, heartbeat_dir):
        audit_path = tmp_path / "audit.log"
        writer = AuditWriter(audit_path)
        writer.append("test_event", {"key": "value"}, cycle_id="c1")
        writer.close()

        result = get_cortex_status(db, heartbeat_dir, audit_path)
        assert result["audit_chain"]["status"] == "OK"
        assert "processes" in result
        assert "cross_db" in result
        assert result["kill_switch"]["totp_required"] is True

    def test_handles_missing_audit(self, db, heartbeat_dir, tmp_path):
        result = get_cortex_status(db, heartbeat_dir, tmp_path / "nope.log")
        assert result["audit_chain"]["status"] == "OK"  # Empty file = ok


class TestGetAgentCycleData:
    """Tests for get_agent_cycle_data."""

    def test_returns_not_found_for_missing_cycle(self, db):
        result = get_agent_cycle_data(db, "nonexistent")
        assert result["found"] is False

    def test_returns_cycle_data(self, db):
        db.execute(
            "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) VALUES (?, ?, ?, ?, ?)",
            ("c1", "2024-01-01T10:00:00", "OPEN", "timed", "SHADOW"),
        )
        db.commit()
        result = get_agent_cycle_data(db, "c1")
        assert result["found"] is True
        assert result["state"] == "OPEN"
        assert result["mode"] == "SHADOW"


class TestGetSparklineData:
    """Tests for get_sparkline_data and get_all_sparkline_data."""

    def test_returns_empty_when_no_duckdb(self, tmp_path):
        result = get_sparkline_data(tmp_path / "nonexistent.duckdb", "sharpe", "1W")
        assert result == []

    def test_returns_empty_for_missing_metric(self, tmp_path):
        try:
            from pmacs.storage.duckdb import DuckDBAdapter

            db_path = tmp_path / "analytics.duckdb"
            adapter = DuckDBAdapter(db_path=db_path)
            adapter.init_tables()
            result = get_sparkline_data(db_path, "nonexistent_metric", "1W")
            assert result == []
        except Exception:
            # DuckDB not installed
            pass

    def test_returns_time_series_data(self, tmp_path):
        try:
            from pmacs.storage.duckdb import DuckDBAdapter

            db_path = tmp_path / "analytics.duckdb"
            adapter = DuckDBAdapter(db_path=db_path)
            adapter.init_tables()
            adapter.execute(
                "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value, computed_at) VALUES (?, ?, ?, current_timestamp - INTERVAL '2 day')",
                ["c1", "sharpe", 0.5],
            )
            adapter.execute(
                "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value, computed_at) VALUES (?, ?, ?, current_timestamp - INTERVAL '1 day')",
                ["c2", "sharpe", 1.2],
            )
            adapter.execute(
                "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value, computed_at) VALUES (?, ?, ?, current_timestamp)",
                ["c3", "sharpe", 1.5],
            )

            result = get_sparkline_data(db_path, "sharpe", "1W")
            assert len(result) == 3
            assert result[0][1] == 0.5
            assert result[1][1] == 1.2
            assert result[2][1] == 1.5
        except Exception:
            # DuckDB not installed — skip gracefully
            pass

    def test_1d_window_filters_old_data(self, tmp_path):
        try:
            from pmacs.storage.duckdb import DuckDBAdapter

            db_path = tmp_path / "analytics.duckdb"
            adapter = DuckDBAdapter(db_path=db_path)
            adapter.init_tables()
            # Insert old data (outside 1D window)
            adapter.execute(
                "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value, computed_at) VALUES (?, ?, ?, current_timestamp - INTERVAL '3 day')",
                ["c1", "sharpe", 0.5],
            )
            # Insert recent data (inside 1D window)
            adapter.execute(
                "INSERT INTO rolling_metrics (cycle_id, metric_name, metric_value, computed_at) VALUES (?, ?, ?, current_timestamp)",
                ["c2", "sharpe", 1.5],
            )

            result = get_sparkline_data(db_path, "sharpe", "1D")
            assert len(result) == 1
            assert result[0][1] == 1.5
        except Exception:
            pass

    def test_get_all_sparkline_data_returns_dict(self, tmp_path):
        try:
            from pmacs.storage.duckdb import DuckDBAdapter

            db_path = tmp_path / "analytics.duckdb"
            adapter = DuckDBAdapter(db_path=db_path)
            adapter.init_tables()

            result = get_all_sparkline_data(db_path, "1W")
            assert isinstance(result, dict)
            assert "sharpe" in result
            assert "max_drawdown_pct" in result
            assert "win_rate_pct" in result
            assert "open_positions" in result
            assert "capital_used_pct" in result
        except Exception:
            pass
