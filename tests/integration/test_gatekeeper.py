"""Integration tests for Gatekeeper — deterministic admittance filter.

Tests use synthetic SQLite databases to exercise the gate() function
without requiring a running inference server or real data pipeline.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pmacs.agents.gatekeeper import (
    ADV_MINIMUM_USD,
    LIMITED_HISTORY_DAYS,
    GatekeeperResult,
    gate,
)
from pmacs.storage.sqlite import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RiskConfig:
    """Minimal risk config stub for gate()."""

    max_concurrent_positions: int = 5


class _Config:
    """Minimal config stub satisfying ConfigLike protocol."""

    risk: _RiskConfig

    def __init__(self, max_concurrent: int = 5) -> None:
        self.risk = _RiskConfig()
        self.risk.max_concurrent_positions = max_concurrent


def _make_db_with_universe(tmp_path: Path, rows: list[tuple]) -> Path:
    """Create a temp DB with init_db schema plus a 'universe' table.

    Args:
        tmp_path: Directory for the temp database.
        rows: List of (ticker, halted, delisted) tuples to insert.

    Returns:
        Path to the created database file.
    """
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS universe ("
        "  ticker TEXT PRIMARY KEY,"
        "  halted INTEGER NOT NULL DEFAULT 0,"
        "  delisted INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    for ticker, halted, delisted in rows:
        conn.execute(
            "INSERT INTO universe (ticker, halted, delisted) VALUES (?, ?, ?)",
            (ticker, halted, delisted),
        )
    conn.commit()
    conn.close()
    return db_path


def _make_db_with_holdings(tmp_path: Path, holdings: list[tuple]) -> Path:
    """Create a temp DB with holdings rows.

    Args:
        tmp_path: Directory for the temp database.
        holdings: List of (id, ticker, state, cycle_id_opened) tuples.

    Returns:
        Path to the created database file.
    """
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    for hid, ticker, state, cycle_id in holdings:
        conn.execute(
            "INSERT INTO holdings (id, ticker, state, cycle_id_opened) "
            "VALUES (?, ?, ?, ?)",
            (hid, ticker, state, cycle_id),
        )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGatekeeperIntegration:
    """Integration tests for the gate() admittance filter."""

    def test_halted_ticker_rejected(self, tmp_path: Path) -> None:
        """A halted ticker is rejected."""
        db_path = _make_db_with_universe(
            tmp_path, [("HALT", 1, 0), ("OK", 0, 0)]
        )
        result = gate(
            "HALT",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
        )
        assert result.admitted is False
        assert result.reject_reason == "HALTED_OR_DELISTED"

    def test_delisted_ticker_rejected(self, tmp_path: Path) -> None:
        """A delisted ticker is rejected."""
        db_path = _make_db_with_universe(
            tmp_path, [("DELIST", 0, 1)]
        )
        result = gate(
            "DELIST",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
        )
        assert result.admitted is False
        assert result.reject_reason == "HALTED_OR_DELISTED"

    def test_kill_switch_engaged_rejects_all(self, tmp_path: Path) -> None:
        """When kill switch is engaged, all tickers rejected."""
        db_path = _make_db_with_universe(
            tmp_path, [("AAPL", 0, 0)]
        )
        result = gate(
            "AAPL",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
            kill_switch_engaged=True,
        )
        assert result.admitted is False
        assert result.reject_reason == "KILL_SWITCH_ENGAGED"

    def test_stale_critical_data_rejects(self, tmp_path: Path) -> None:
        """Stale CRITICAL data causes rejection."""
        db_path = _make_db_with_universe(
            tmp_path, [("AAPL", 0, 0)]
        )
        result = gate(
            "AAPL",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
            stale_critical=True,
        )
        assert result.admitted is False
        assert result.reject_reason == "STALE_CRITICAL_DATA"

    def test_valid_ticker_admitted(self, tmp_path: Path) -> None:
        """A valid ticker with fresh data is admitted."""
        db_path = _make_db_with_universe(
            tmp_path, [("AAPL", 0, 0)]
        )
        result = gate(
            "AAPL",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
        )
        assert result.admitted is True
        assert result.reject_reason is None

    def test_unknown_ticker_admitted(self, tmp_path: Path) -> None:
        """A ticker not in universe table passes halted/delisted check.

        The gatekeeper only checks halted/delisted status for known tickers.
        Unknown tickers are assumed not halted.
        """
        db_path = _make_db_with_universe(
            tmp_path, [("OTHER", 0, 0)]
        )
        result = gate(
            "UNKNOWN_TICKER",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
        )
        assert result.admitted is True

    def test_portfolio_limit_rejects_new(self, tmp_path: Path) -> None:
        """When max concurrent positions reached, new tickers rejected."""
        db_path = _make_db_with_holdings(
            tmp_path,
            [
                ("h1", "AAPL", "ACTIVE", "c1"),
                ("h2", "MSFT", "ACTIVE", "c1"),
                ("h3", "GOOG", "ACTIVE", "c1"),
            ],
        )
        result = gate(
            "TSLA",
            "cycle-001",
            db_path=db_path,
            config=_Config(max_concurrent=3),
        )
        assert result.admitted is False
        assert result.reject_reason == "PORTFOLIO_LIMIT_HIT"

    def test_portfolio_limit_allows_existing(self, tmp_path: Path) -> None:
        """When at portfolio limit, existing position tickers are still admitted."""
        db_path = _make_db_with_holdings(
            tmp_path,
            [
                ("h1", "AAPL", "ACTIVE", "c1"),
                ("h2", "MSFT", "ACTIVE", "c1"),
                ("h3", "GOOG", "ACTIVE", "c1"),
            ],
        )
        # AAPL already has an open position, so it should pass
        result = gate(
            "AAPL",
            "cycle-001",
            db_path=db_path,
            config=_Config(max_concurrent=3),
        )
        assert result.admitted is True

    def test_closed_positions_dont_count(self, tmp_path: Path) -> None:
        """Closed positions don't count toward the limit."""
        db_path = _make_db_with_holdings(
            tmp_path,
            [
                ("h1", "AAPL", "ACTIVE", "c1"),
                ("h2", "MSFT", "RESOLVED_FLAT", "c1"),
            ],
        )
        result = gate(
            "TSLA",
            "cycle-001",
            db_path=db_path,
            config=_Config(max_concurrent=2),
        )
        # Only 1 OPEN position, limit is 2, so new ticker admitted
        assert result.admitted is True

    def test_no_db_file_admits(self, tmp_path: Path) -> None:
        """When DB file doesn't exist, ticker is admitted (graceful fallback)."""
        db_path = tmp_path / "nonexistent.db"
        result = gate(
            "AAPL",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
        )
        assert result.admitted is True

    def test_kill_switch_priority(self, tmp_path: Path) -> None:
        """Kill switch is checked before halted/delisted check."""
        db_path = _make_db_with_universe(
            tmp_path, [("AAPL", 0, 0)]
        )
        result = gate(
            "AAPL",
            "cycle-001",
            db_path=db_path,
            config=_Config(),
            kill_switch_engaged=True,
        )
        assert result.admitted is False
        assert result.reject_reason == "KILL_SWITCH_ENGAGED"
        # Not HALTED_OR_DELISTED — kill switch fires first

    def test_gatekeeper_result_is_frozen(self, tmp_path: Path) -> None:
        """GatekeeperResult is immutable (frozen Pydantic model)."""
        result = GatekeeperResult(ticker="AAPL", admitted=True)
        with pytest.raises(Exception):
            result.admitted = False  # type: ignore[misc]
