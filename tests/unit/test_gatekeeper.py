"""Unit tests for the gatekeeper (Agents.md §4)."""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from pmacs.agents.gatekeeper import GatekeeperResult, gate


# ---------------------------------------------------------------------------
# Config stub
# ---------------------------------------------------------------------------


@dataclass
class _RiskStub:
    max_concurrent_positions: int = 5


@dataclass
class _ConfigStub:
    risk: _RiskStub


def _make_config(max_positions: int = 5) -> _ConfigStub:
    return _ConfigStub(risk=_RiskStub(max_concurrent_positions=max_positions))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _create_db(tmp: Path, universe_rows: list[tuple] | None = None) -> Path:
    """Create a minimal SQLite DB with universe and holdings tables."""
    conn = sqlite3.connect(str(tmp))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS universe ("
            "  ticker TEXT PRIMARY KEY,"
            "  halted INTEGER DEFAULT 0,"
            "  delisted INTEGER DEFAULT 0"
            ")"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS holdings ("
            "  ticker TEXT,"
            "  state TEXT"
            ")"
        )
        if universe_rows:
            conn.executemany(
                "INSERT INTO universe (ticker, halted, delisted) VALUES (?, ?, ?)",
                universe_rows,
            )
        conn.commit()
    finally:
        conn.close()
    return tmp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKillSwitch:
    """Kill switch engaged -> reject everything."""

    def test_rejects(self, tmp_path):
        db = _create_db(tmp_path / "test.db")
        result = gate(
            "AAPL", "c001",
            db_path=db,
            config=_make_config(),
            kill_switch_engaged=True,
        )
        assert result.admitted is False
        assert result.reject_reason == "KILL_SWITCH_ENGAGED"


class TestHaltedDelisted:
    """Halted or delisted tickers are rejected."""

    def test_halted(self, tmp_path):
        db = _create_db(
            tmp_path / "test.db",
            [("HALT", 1, 0)],
        )
        result = gate("HALT", "c002", db_path=db, config=_make_config())
        assert result.admitted is False
        assert result.reject_reason == "HALTED_OR_DELISTED"

    def test_delisted(self, tmp_path):
        db = _create_db(
            tmp_path / "test.db",
            [("DEAD", 0, 1)],
        )
        result = gate("DEAD", "c003", db_path=db, config=_make_config())
        assert result.admitted is False
        assert result.reject_reason == "HALTED_OR_DELISTED"

    def test_normal_ticker_passes(self, tmp_path):
        db = _create_db(
            tmp_path / "test.db",
            [("AAPL", 0, 0)],
        )
        result = gate("AAPL", "c004", db_path=db, config=_make_config())
        assert result.admitted is True


class TestStaleCritical:
    """Stale CRITICAL data -> reject."""

    def test_rejects(self, tmp_path):
        db = _create_db(tmp_path / "test.db")
        result = gate(
            "AAPL", "c005",
            db_path=db,
            config=_make_config(),
            stale_critical=True,
        )
        assert result.admitted is False
        assert result.reject_reason == "STALE_CRITICAL_DATA"


class TestPortfolioLimit:
    """Max concurrent positions -> reject new tickers, allow existing."""

    def test_reject_new_when_full(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE IF NOT EXISTS universe (ticker TEXT PRIMARY KEY, halted INTEGER DEFAULT 0, delisted INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS holdings (ticker TEXT, state TEXT)")
        # 5 open positions
        for i in range(5):
            conn.execute(f"INSERT INTO holdings VALUES ('T{i}', 'ACTIVE')")
        conn.commit()
        conn.close()

        result = gate(
            "NEW_TICKER", "c006",
            db_path=db,
            config=_make_config(max_positions=5),
        )
        assert result.admitted is False
        assert result.reject_reason == "PORTFOLIO_LIMIT_HIT"

    def test_allows_existing_when_full(self, tmp_path):
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE IF NOT EXISTS universe (ticker TEXT PRIMARY KEY, halted INTEGER DEFAULT 0, delisted INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS holdings (ticker TEXT, state TEXT)")
        conn.execute("INSERT INTO holdings VALUES ('AAPL', 'ACTIVE')")
        for i in range(4):
            conn.execute(f"INSERT INTO holdings VALUES ('T{i}', 'ACTIVE')")
        conn.commit()
        conn.close()

        result = gate(
            "AAPL", "c007",
            db_path=db,
            config=_make_config(max_positions=5),
        )
        assert result.admitted is True


class TestAdmitWithNoDb:
    """No DB file -> admit (no data to reject on)."""

    def test_admit(self, tmp_path):
        db = tmp_path / "nonexistent.db"
        result = gate("AAPL", "c008", db_path=db, config=_make_config())
        assert result.admitted is True
        assert result.reject_reason is None


class TestFlags:
    """Flags are set but don't cause rejection."""

    def test_no_flags_default(self, tmp_path):
        db = _create_db(tmp_path / "test.db")
        result = gate("AAPL", "c009", db_path=db, config=_make_config())
        assert result.admitted is True
        # Default stubs return 120 days history and $5M ADV, so no flags
        assert result.flags == []
