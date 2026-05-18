"""Flywheel health monitoring (Architecture.md §3 repo tree).

Reads real metrics from engines/flywheel_health.py and evaluates against
thresholds from config/risk.toml.
"""
from __future__ import annotations

from pathlib import Path

from pmacs.engines.flywheel_health import (
    get_max_drawdown,
    get_rolling_brier,
    get_rolling_sharpe,
)
from pmacs.logsys import log_debug
from pmacs.schemas.flywheel import FlywheelHealthSnapshot

# Default thresholds when config not available
_DEFAULT_MAX_BRIER = 0.30
_DEFAULT_MIN_SHARPE = 0.0
_DEFAULT_MAX_DRAWDOWN = 15.0


class FlywheelMonitor:
    """Flywheel component health monitor.

    Reads rolling metrics from DuckDB analytics store and checks against
    thresholds from config/risk.toml.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        duckdb_path: Path | None = None,
        config: object | None = None,
    ):
        self._db_path = db_path
        self._duckdb_path = duckdb_path
        self._config = config

    def get_health(self) -> FlywheelHealthSnapshot:
        """Get the current flywheel health snapshot with real metrics."""
        duckdb = self._duckdb_path or Path("/var/db/pmacs/pmacs_analytics.duckdb")

        rolling_brier = get_rolling_brier(window=30, duckdb_path=duckdb)
        rolling_sharpe = get_rolling_sharpe(window=20, duckdb_path=duckdb)
        max_drawdown = get_max_drawdown(window=90, duckdb_path=duckdb)

        # Count active mutations from SQLite if available
        active_mutations = 0
        if self._db_path and self._db_path.exists():
            try:
                import sqlite3

                with sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM mutation_proposals "
                        "WHERE status IN ('RUNNING_AB', 'READY_FOR_REVIEW')"
                    ).fetchone()
                    active_mutations = row[0] if row else 0
            except Exception:
                pass

        # Calibration gap: difference between predicted probability and actual outcome rate
        calibration_gap = 0.0
        if rolling_brier > 0:
            # Brier score itself measures calibration; gap is Brier minus perfect (0)
            calibration_gap = rolling_brier

        # Cycles since last calibration
        cycles_since = 0
        if self._db_path and self._db_path.exists():
            try:
                import sqlite3

                with sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM cycles "
                        "WHERE state = 'CLOSED' AND calibrated = 1"
                    ).fetchone()
                    total = conn.execute(
                        "SELECT COUNT(*) FROM cycles WHERE state = 'CLOSED'"
                    ).fetchone()
                    if total and total[0] and row:
                        cycles_since = total[0] - row[0]
            except Exception:
                pass

        return FlywheelHealthSnapshot(
            rolling_brier=rolling_brier,
            rolling_sharpe=rolling_sharpe,
            max_drawdown_pct=max_drawdown,
            calibration_gap=calibration_gap,
            cycles_since_calibration=cycles_since,
            active_mutations=active_mutations,
        )

    def is_healthy(self) -> bool:
        """Quick health check against thresholds."""
        health = self.get_health()

        # Unhealthy if any metric is None (no data yet) — treat as healthy
        # until we have data to evaluate
        if health.rolling_brier is None:
            return True

        max_brier = _DEFAULT_MAX_BRIER
        min_sharpe = _DEFAULT_MIN_SHARPE
        max_dd = _DEFAULT_MAX_DRAWDOWN

        if self._config is not None:
            try:
                max_brier = getattr(self._config, "max_brier", _DEFAULT_MAX_BRIER)
                min_sharpe = getattr(self._config, "min_sharpe", _DEFAULT_MIN_SHARPE)
                max_dd = getattr(self._config, "max_drawdown_pct", _DEFAULT_MAX_DRAWDOWN)
            except Exception:
                pass

        brier_ok = health.rolling_brier <= max_brier
        sharpe_ok = health.rolling_sharpe is None or health.rolling_sharpe >= min_sharpe
        dd_ok = health.max_drawdown_pct is None or health.max_drawdown_pct <= max_dd

        healthy = brier_ok and sharpe_ok and dd_ok

        if not healthy:
            log_debug(
                "FLYWHEEL_UNHEALTHY",
                payload={
                    "brier": health.rolling_brier,
                    "sharpe": health.rolling_sharpe,
                    "drawdown": health.max_drawdown_pct,
                    "brier_ok": brier_ok,
                    "sharpe_ok": sharpe_ok,
                    "dd_ok": dd_ok,
                },
                level="WARN",
                msg=f"Flywheel unhealthy: brier={health.rolling_brier}, sharpe={health.rolling_sharpe}, dd={health.max_drawdown_pct}",
            )

        return healthy
