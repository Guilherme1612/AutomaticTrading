"""Unit tests for check_promotion_gates() — Phases.md §3.2."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pmacs.engines.flywheel_health import check_promotion_gates


@pytest.fixture
def tmp_db(tmp_path: Path) -> tuple[Path, Path]:
    """Return (sqlite_path, duckdb_path) with empty files."""
    sqlite_path = tmp_path / "test.db"
    duckdb_path = tmp_path / "test.duckdb"
    sqlite_path.touch()
    return sqlite_path, duckdb_path


def test_all_gates_pass(tmp_db: tuple[Path, Path]) -> None:
    sqlite_path, duckdb_path = tmp_db
    with (
        patch("pmacs.engines.flywheel_health.count_cycles_in_mode", return_value=100),
        patch("pmacs.engines.flywheel_health.count_trades_in_mode", return_value=250),
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.25),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=8.0),
        patch("pmacs.engines.flywheel_health.cycles_since_last_demotion", return_value=15),
    ):
        result = check_promotion_gates(
            "SHADOW_PAPER", "PAPER_VALIDATED",
            sqlite_path, duckdb_path, cycle_id="test-001",
        )
    assert result.all_pass is True
    assert len(result.gates) == 6
    assert all(g.passed for g in result.gates)


def test_brier_gate_fails(tmp_db: tuple[Path, Path]) -> None:
    sqlite_path, duckdb_path = tmp_db
    with (
        patch("pmacs.engines.flywheel_health.count_cycles_in_mode", return_value=100),
        patch("pmacs.engines.flywheel_health.count_trades_in_mode", return_value=250),
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.35),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=8.0),
        patch("pmacs.engines.flywheel_health.cycles_since_last_demotion", return_value=15),
    ):
        result = check_promotion_gates(
            "SHADOW_PAPER", "PAPER_VALIDATED",
            sqlite_path, duckdb_path, cycle_id="test-002",
        )
    assert result.all_pass is False
    brier_gate = next(g for g in result.gates if g.gate_name == "brier")
    assert brier_gate.passed is False


def test_sharpe_gate_fails(tmp_db: tuple[Path, Path]) -> None:
    sqlite_path, duckdb_path = tmp_db
    with (
        patch("pmacs.engines.flywheel_health.count_cycles_in_mode", return_value=100),
        patch("pmacs.engines.flywheel_health.count_trades_in_mode", return_value=250),
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.25),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=-0.1),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=8.0),
        patch("pmacs.engines.flywheel_health.cycles_since_last_demotion", return_value=15),
    ):
        result = check_promotion_gates(
            "PAPER_VALIDATED", "LIVE_EARLY",
            sqlite_path, duckdb_path, cycle_id="test-003",
        )
    assert result.all_pass is False
    sharpe_gate = next(g for g in result.gates if g.gate_name == "sharpe")
    assert sharpe_gate.passed is False


def test_unknown_transition_raises(tmp_db: tuple[Path, Path]) -> None:
    sqlite_path, duckdb_path = tmp_db
    with pytest.raises(KeyError):
        check_promotion_gates(
            "INVALID_MODE", "ANOTHER_INVALID",
            sqlite_path, duckdb_path, cycle_id="test-004",
        )


def test_min_cycles_not_met(tmp_db: tuple[Path, Path]) -> None:
    sqlite_path, duckdb_path = tmp_db
    with (
        patch("pmacs.engines.flywheel_health.count_cycles_in_mode", return_value=10),
        patch("pmacs.engines.flywheel_health.count_trades_in_mode", return_value=250),
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.25),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=8.0),
        patch("pmacs.engines.flywheel_health.cycles_since_last_demotion", return_value=15),
    ):
        result = check_promotion_gates(
            "SHADOW_PAPER", "PAPER_VALIDATED",
            sqlite_path, duckdb_path, cycle_id="test-005",
        )
    assert result.all_pass is False
    cycles_gate = next(g for g in result.gates if g.gate_name == "min_cycles")
    assert cycles_gate.passed is False


def test_drawdown_gate_fails(tmp_db: tuple[Path, Path]) -> None:
    sqlite_path, duckdb_path = tmp_db
    with (
        patch("pmacs.engines.flywheel_health.count_cycles_in_mode", return_value=100),
        patch("pmacs.engines.flywheel_health.count_trades_in_mode", return_value=250),
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.25),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=20.0),
        patch("pmacs.engines.flywheel_health.cycles_since_last_demotion", return_value=15),
    ):
        result = check_promotion_gates(
            "SHADOW_PAPER", "PAPER_VALIDATED",
            sqlite_path, duckdb_path, cycle_id="test-006",
        )
    assert result.all_pass is False
    dd_gate = next(g for g in result.gates if g.gate_name == "drawdown")
    assert dd_gate.passed is False


def test_current_values_populated(tmp_db: tuple[Path, Path]) -> None:
    sqlite_path, duckdb_path = tmp_db
    with (
        patch("pmacs.engines.flywheel_health.count_cycles_in_mode", return_value=95),
        patch("pmacs.engines.flywheel_health.count_trades_in_mode", return_value=210),
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.29),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.1),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=12.0),
        patch("pmacs.engines.flywheel_health.cycles_since_last_demotion", return_value=15),
    ):
        result = check_promotion_gates(
            "SHADOW_PAPER", "PAPER_VALIDATED",
            sqlite_path, duckdb_path, cycle_id="test-007",
        )
    assert result.current_values["cycles"] == 95
    assert result.current_values["trades"] == 210
    assert result.current_values["brier"] == 0.29
