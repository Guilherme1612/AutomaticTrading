"""Flywheel health engine — gate computation and calibration snapshots (Phases.md §3)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pmacs.constants import DEMOTION_THRESHOLDS, DEMOTE_COOLDOWN_CYCLES, PROMOTION_THRESHOLDS
from pmacs.logsys import log_debug
from pmacs.schemas.flywheel import (
    DemotionGateResult,
    GateStatus,
    PromotionGateResult,
)


@dataclass
class FlywheelHealthSnapshot:
    rolling_brier_avg: float
    rolling_sharpe: float
    calibration_gap: float  # difference between predicted and actual
    active_mutations: int
    pending_reviews: int
    lessons_count: int


def snapshot_health(
    rolling_brier_avg: float,
    rolling_sharpe: float,
    calibration_gap: float,
    active_mutations: int = 0,
    pending_reviews: int = 0,
    lessons_count: int = 0,
) -> FlywheelHealthSnapshot:
    """Build a point-in-time flywheel health snapshot."""
    return FlywheelHealthSnapshot(
        rolling_brier_avg=rolling_brier_avg,
        rolling_sharpe=rolling_sharpe,
        calibration_gap=calibration_gap,
        active_mutations=active_mutations,
        pending_reviews=pending_reviews,
        lessons_count=lessons_count,
    )


# ---------------------------------------------------------------------------
# Helper functions for metric retrieval
# ---------------------------------------------------------------------------


def count_cycles_in_mode(mode: str, db_path: Path) -> int:
    """Count closed cycles in the given mode (SQLite)."""
    if not db_path.exists():
        return 0
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM cycles WHERE mode = ? AND state = 'CLOSED'",
            (mode,),
        ).fetchone()
        return row[0] if row else 0


def count_trades_in_mode(mode: str, db_path: Path) -> int:
    """Count trades executed in the given mode (SQLite)."""
    if not db_path.exists():
        return 0
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM holdings WHERE mode = ?",
            (mode,),
        ).fetchone()
        return row[0] if row else 0


def cycles_since_last_demotion(sqlite_db_path: Path) -> int:
    """Count cycles completed since the last demotion event (SQLite).

    A demotion is any mode_history transition from a higher tier to a lower tier.
    Returns 0 if no demotion found (first promotion allowed).

    Args:
        sqlite_db_path: Path to the SQLite database (NOT DuckDB).
    """
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            # Find the most recent demotion (transition from higher to lower mode)
            row = conn.execute(
                "SELECT MAX(changed_at) FROM mode_history "
                "WHERE from_mode IN ('LIVE_EXPANDED','LIVE_STANDARD','LIVE_EARLY','PAPER_VALIDATED') "
                "AND from_mode != to_mode"
            ).fetchone()
            demotion_ts = row[0] if row else None
            if demotion_ts is None:
                return 0
            count_row = conn.execute(
                "SELECT COUNT(*) FROM cycles WHERE state = 'CLOSED' AND closed_at > ?",
                (demotion_ts,),
            ).fetchone()
            return count_row[0] if count_row else 0
    except Exception:
        return 0


def get_rolling_brier(window: int, duckdb_path: Path, cycle_id: str = "") -> float:
    """Get rolling average Brier score over the last N cycles (DuckDB)."""
    if not duckdb_path.exists():
        return 0.0
    try:
        import duckdb

        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            row = conn.execute(
                "SELECT AVG(brier) FROM ("
                "SELECT brier FROM persona_performance "
                "ORDER BY computed_at DESC LIMIT ?"
                ") AS recent",
                (window,),
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
    except Exception as exc:
        log_debug("FLYWHEEL_QUERY_FAILED", payload={"fn": "get_rolling_brier", "error": str(exc)},
                  level="WARN", error_code="FLYWHEEL_QUERY_FAILED",
                  cycle_id=cycle_id or None,
                  msg=f"Rolling Brier query failed: {exc}")
        return 0.0


def get_rolling_sharpe(window: int, duckdb_path: Path, cycle_id: str = "") -> float:
    """Get rolling Sharpe ratio over the last N cycles (DuckDB)."""
    if not duckdb_path.exists():
        return 0.0
    try:
        import duckdb

        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            row = conn.execute(
                "SELECT AVG(metric_value) FROM ("
                "SELECT metric_value FROM rolling_metrics "
                "WHERE metric_name = 'sharpe' "
                "ORDER BY computed_at DESC LIMIT ?"
                ") AS recent",
                (window,),
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
    except Exception as exc:
        log_debug("FLYWHEEL_QUERY_FAILED", payload={"fn": "get_rolling_sharpe", "error": str(exc)},
                  level="WARN", error_code="FLYWHEEL_QUERY_FAILED",
                  cycle_id=cycle_id or None,
                  msg=f"Rolling Sharpe query failed: {exc}")
        return 0.0


def get_max_drawdown(window: int, duckdb_path: Path, cycle_id: str = "") -> float:
    """Get max drawdown percentage over the last N cycles (DuckDB)."""
    if not duckdb_path.exists():
        return 0.0
    try:
        import duckdb

        with duckdb.connect(str(duckdb_path), read_only=True) as conn:
            row = conn.execute(
                "SELECT MAX(metric_value) FROM ("
                "SELECT metric_value FROM rolling_metrics "
                "WHERE metric_name = 'drawdown' "
                "ORDER BY computed_at DESC LIMIT ?"
                ") AS recent",
                (window,),
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
    except Exception as exc:
        log_debug("FLYWHEEL_QUERY_FAILED", payload={"fn": "get_max_drawdown", "error": str(exc)},
                  level="WARN", error_code="FLYWHEEL_QUERY_FAILED",
                  cycle_id=cycle_id or None,
                  msg=f"Max drawdown query failed: {exc}")
        return 0.0


# ---------------------------------------------------------------------------
# Gate computation (Phases.md §3.2, §3.5)
# ---------------------------------------------------------------------------


def check_promotion_gates(
    current_mode: str,
    target_mode: str,
    db_path: Path,
    duckdb_path: Path,
    cycle_id: str,
) -> PromotionGateResult:
    """Check all promotion gates for a mode transition (Phases.md §3.2).

    Returns which gates pass and which fail. UI displays this in the mode badge.
    """
    key = f"{current_mode}_to_{target_mode}"
    # Try both SHADOW_PAPER and PAPER keys (Phases.md §3.7 concurrent mode)
    if key not in PROMOTION_THRESHOLDS:
        alt_key = f"PAPER_to_{target_mode}"
        if alt_key in PROMOTION_THRESHOLDS:
            key = alt_key
    thresholds = PROMOTION_THRESHOLDS[key]

    current_cycles = count_cycles_in_mode(target_mode, db_path)
    current_trades = count_trades_in_mode(target_mode, db_path)
    rolling_brier = get_rolling_brier(window=30, duckdb_path=duckdb_path)
    rolling_sharpe = get_rolling_sharpe(window=20, duckdb_path=duckdb_path)
    rolling_drawdown = get_max_drawdown(window=90, duckdb_path=duckdb_path)
    cooldown_cycles = cycles_since_last_demotion(db_path)

    current_values: dict[str, float | int] = {
        "cycles": current_cycles,
        "trades": current_trades,
        "brier": rolling_brier,
        "sharpe": rolling_sharpe,
        "drawdown": rolling_drawdown,
    }

    gates: list[GateStatus] = [
        GateStatus(
            gate_name="demotion_cooldown",
            passed=cooldown_cycles >= DEMOTE_COOLDOWN_CYCLES,
            current_value=cooldown_cycles,
            threshold=DEMOTE_COOLDOWN_CYCLES,
            comparison=">=",
        ),
        GateStatus(
            gate_name="min_cycles",
            passed=current_cycles >= thresholds["min_cycles"],
            current_value=current_cycles,
            threshold=thresholds["min_cycles"],
            comparison=">=",
        ),
        GateStatus(
            gate_name="min_trades",
            passed=current_trades >= thresholds["min_trades"],
            current_value=current_trades,
            threshold=thresholds["min_trades"],
            comparison=">=",
        ),
        GateStatus(
            gate_name="brier",
            passed=rolling_brier <= thresholds["max_brier"],
            current_value=rolling_brier,
            threshold=thresholds["max_brier"],
            comparison="<=",
        ),
        GateStatus(
            gate_name="sharpe",
            passed=rolling_sharpe >= thresholds["min_sharpe"],
            current_value=rolling_sharpe,
            threshold=thresholds["min_sharpe"],
            comparison=">=",
        ),
        GateStatus(
            gate_name="drawdown",
            passed=rolling_drawdown <= thresholds["max_drawdown_pct"],
            current_value=rolling_drawdown,
            threshold=thresholds["max_drawdown_pct"],
            comparison="<=",
        ),
    ]

    all_pass = all(g.passed for g in gates)

    log_debug(
        "PROMOTION_GATE_CHECK",
        payload={
            "from": current_mode,
            "to": target_mode,
            "all_pass": all_pass,
            "current_values": current_values,
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Promotion gates check: {current_mode} → {target_mode}, pass={all_pass}",
    )

    return PromotionGateResult(
        current_mode=current_mode,
        target_mode=target_mode,
        gates=gates,
        all_pass=all_pass,
        current_values=current_values,
        thresholds=dict(thresholds),
    )


# Mode rank for demotion tier lookup
_MODE_DEMOTION_ORDER: list[str] = [
    "LIVE_EXPANDED",
    "LIVE_STANDARD",
    "LIVE_EARLY",
    "PAPER_VALIDATED",
]


def check_demotion_gates(
    current_mode: str,
    duckdb_path: Path,
    cycle_id: str,
) -> DemotionGateResult:
    """Check demotion gates for current mode (Phases.md §3.5).

    Demotion is one tier at a time. Uses OR logic: any trigger = demote.
    """
    # Find which demotion key applies
    demoted_mode: str | None = None
    key: str | None = None
    for mode in _MODE_DEMOTION_ORDER:
        if current_mode == mode:
            idx = _MODE_DEMOTION_ORDER.index(mode)
            demoted_mode = _MODE_DEMOTION_ORDER[idx + 1] if idx + 1 < len(_MODE_DEMOTION_ORDER) else "PAPER"
            key = f"{mode}_to_{demoted_mode}"
            break

    if key is None or demoted_mode is None:
        return DemotionGateResult(current_mode=current_mode)

    thresholds = DEMOTION_THRESHOLDS[key]
    window = int(thresholds.get("window", 20))

    rolling_sharpe = get_rolling_sharpe(window=window, duckdb_path=duckdb_path)
    rolling_drawdown = get_max_drawdown(window=window, duckdb_path=duckdb_path)
    rolling_brier = get_rolling_brier(window=window, duckdb_path=duckdb_path)

    # Check triggers (OR logic per spec §3.5)
    max_sharpe = thresholds.get("max_sharpe")
    max_drawdown = thresholds.get("max_drawdown_pct")
    max_brier = thresholds.get("max_brier")
    min_sharpe = thresholds.get("min_sharpe")

    # LIVE tiers: Sharpe < max_sharpe OR drawdown > max_drawdown
    if max_sharpe is not None and rolling_sharpe < max_sharpe:
        return DemotionGateResult(
            current_mode=current_mode,
            demoted_mode=demoted_mode,
            triggered=True,
            trigger_reason=f"Rolling {window}-cycle Sharpe ({rolling_sharpe:.2f}) < {max_sharpe}",
            trigger_metric="sharpe",
            current_value=rolling_sharpe,
            threshold=max_sharpe,
        )

    if max_drawdown is not None and rolling_drawdown > max_drawdown:
        return DemotionGateResult(
            current_mode=current_mode,
            demoted_mode=demoted_mode,
            triggered=True,
            trigger_reason=f"Drawdown ({rolling_drawdown:.1f}%) > {max_drawdown}%",
            trigger_metric="drawdown",
            current_value=rolling_drawdown,
            threshold=max_drawdown,
        )

    # PAPER_VALIDATED tier: Brier > max_brier OR Sharpe < min_sharpe
    if max_brier is not None and rolling_brier > max_brier:
        return DemotionGateResult(
            current_mode=current_mode,
            demoted_mode=demoted_mode,
            triggered=True,
            trigger_reason=f"Rolling {window}-cycle Brier ({rolling_brier:.2f}) > {max_brier}",
            trigger_metric="brier",
            current_value=rolling_brier,
            threshold=max_brier,
        )

    if min_sharpe is not None and rolling_sharpe < min_sharpe:
        return DemotionGateResult(
            current_mode=current_mode,
            demoted_mode=demoted_mode,
            triggered=True,
            trigger_reason=f"Rolling {window}-cycle Sharpe ({rolling_sharpe:.2f}) < {min_sharpe}",
            trigger_metric="sharpe",
            current_value=rolling_sharpe,
            threshold=min_sharpe,
        )

    # No trigger
    log_debug(
        "DEMOTION_GATE_CHECK",
        payload={"mode": current_mode, "triggered": False},
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Demotion gate check: {current_mode}, no trigger",
    )

    return DemotionGateResult(current_mode=current_mode)
