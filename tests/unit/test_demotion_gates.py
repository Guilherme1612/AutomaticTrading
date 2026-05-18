"""Unit tests for check_demotion_gates() — Phases.md §3.5."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pmacs.engines.flywheel_health import check_demotion_gates


@pytest.fixture
def tmp_duckdb(tmp_path: Path) -> Path:
    """Return a path to a non-existent duckdb (functions handle missing file)."""
    return tmp_path / "test.duckdb"


def test_live_expanded_sharpe_triggers_demotion(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=-0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=5.0),
    ):
        result = check_demotion_gates("LIVE_EXPANDED", tmp_duckdb, cycle_id="test-001")
    assert result.triggered is True
    assert result.demoted_mode == "LIVE_STANDARD"
    assert result.trigger_metric == "sharpe"


def test_live_expanded_drawdown_triggers_demotion(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=15.0),
    ):
        result = check_demotion_gates("LIVE_EXPANDED", tmp_duckdb, cycle_id="test-002")
    assert result.triggered is True
    assert result.demoted_mode == "LIVE_STANDARD"
    assert result.trigger_metric == "drawdown"


def test_live_standard_drawdown_triggers(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=15.0),
    ):
        result = check_demotion_gates("LIVE_STANDARD", tmp_duckdb, cycle_id="test-003")
    assert result.triggered is True
    assert result.demoted_mode == "LIVE_EARLY"


def test_live_early_drawdown_triggers(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.5),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=18.0),
    ):
        result = check_demotion_gates("LIVE_EARLY", tmp_duckdb, cycle_id="test-004")
    assert result.triggered is True
    assert result.demoted_mode == "PAPER_VALIDATED"


def test_paper_validated_brier_triggers(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.35),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.0),
    ):
        result = check_demotion_gates("PAPER_VALIDATED", tmp_duckdb, cycle_id="test-005")
    assert result.triggered is True
    assert result.demoted_mode == "PAPER"
    assert result.trigger_metric == "brier"


def test_paper_validated_sharpe_triggers(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.25),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=-0.5),
    ):
        result = check_demotion_gates("PAPER_VALIDATED", tmp_duckdb, cycle_id="test-006")
    assert result.triggered is True
    assert result.demoted_mode == "PAPER"
    assert result.trigger_metric == "sharpe"


def test_no_trigger_when_healthy(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=1.0),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=5.0),
    ):
        result = check_demotion_gates("LIVE_EXPANDED", tmp_duckdb, cycle_id="test-007")
    assert result.triggered is False
    assert result.demoted_mode is None


def test_no_trigger_for_unranked_mode(tmp_duckdb: Path) -> None:
    result = check_demotion_gates("SHADOW_PAPER", tmp_duckdb, cycle_id="test-008")
    assert result.triggered is False


def test_one_tier_at_a_time(tmp_duckdb: Path) -> None:
    """LIVE_EXPANDED demotes to LIVE_STANDARD, not directly to PAPER."""
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=-1.0),
        patch("pmacs.engines.flywheel_health.get_max_drawdown", return_value=30.0),
    ):
        result = check_demotion_gates("LIVE_EXPANDED", tmp_duckdb, cycle_id="test-009")
    assert result.demoted_mode == "LIVE_STANDARD"
    # Even with catastrophic metrics, only one tier down


def test_paper_validated_healthy(tmp_duckdb: Path) -> None:
    with (
        patch("pmacs.engines.flywheel_health.get_rolling_brier", return_value=0.25),
        patch("pmacs.engines.flywheel_health.get_rolling_sharpe", return_value=0.1),
    ):
        result = check_demotion_gates("PAPER_VALIDATED", tmp_duckdb, cycle_id="test-010")
    assert result.triggered is False
