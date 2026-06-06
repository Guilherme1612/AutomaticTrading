"""Dashboard configuration — paths to data stores, injected at startup."""
from __future__ import annotations

from pathlib import Path

from pmacs.config import data_dir as _data_dir, CONFIG_DIR


class DashboardConfig:
    """Configuration for the dashboard web app.

    Set once at startup, used by route handlers via get_config().
    All paths default to the centralized data_dir() resolution.
    """

    def __init__(
        self,
        sqlite_path: Path | None = None,
        duckdb_path: Path | None = None,
        heartbeat_dir: Path | None = None,
        audit_path: Path | None = None,
        debug_log_path: Path | None = None,
        config_dir: Path | None = None,
    ) -> None:
        d = _data_dir()
        self.sqlite_path = sqlite_path or d / "pmacs.db"
        self.duckdb_path = duckdb_path or d / "pmacs_analytics.duckdb"
        self.heartbeat_dir = heartbeat_dir or d / "heartbeats"
        self.audit_path = audit_path or d / "audit.log"
        self.debug_log_path = debug_log_path or d / "debug.jsonl"
        self.config_dir = config_dir or CONFIG_DIR


_config: DashboardConfig = DashboardConfig()


def configure(config: DashboardConfig) -> None:
    """Set the dashboard configuration."""
    global _config
    _config = config


def get_config() -> DashboardConfig:
    """Get the current dashboard configuration."""
    return _config
