"""Dashboard configuration — paths to data stores, injected at startup."""
from __future__ import annotations

from pathlib import Path


class DashboardConfig:
    """Configuration for the dashboard web app.

    Set once at startup, used by route handlers via get_config().
    """

    def __init__(
        self,
        sqlite_path: Path = Path("data/pmacs.db"),
        duckdb_path: Path = Path("data/pmacs_analytics.duckdb"),
        heartbeat_dir: Path = Path("/var/db/pmacs/heartbeat"),
        audit_path: Path = Path("logs/audit.log"),
        debug_log_path: Path = Path("logs/debug.jsonl"),
        config_dir: Path = Path("config"),
    ) -> None:
        self.sqlite_path = sqlite_path
        self.duckdb_path = duckdb_path
        self.heartbeat_dir = heartbeat_dir
        self.audit_path = audit_path
        self.debug_log_path = debug_log_path
        self.config_dir = config_dir


_config: DashboardConfig = DashboardConfig()


def configure(config: DashboardConfig) -> None:
    """Set the dashboard configuration."""
    global _config
    _config = config


def get_config() -> DashboardConfig:
    """Get the current dashboard configuration."""
    return _config
