"""Comprehensive gatekeeper tests — all 7 admittance checks (Agents.md §4).

Extends existing test_gatekeeper.py with tests for:
- Check 5: Antipattern (MemoryEngine)
- Check 6: Limited-history flagging
- Check 7: ADV check flagging
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from pmacs.agents.gatekeeper import (
    ADV_MINIMUM_USD,
    LIMITED_HISTORY_DAYS,
    GatekeeperResult,
    _adv_90d,
    _days_of_history,
    gate,
)


@dataclass
class _RiskCfg:
    max_concurrent_positions: int = 5


@dataclass
class _Cfg:
    risk: _RiskCfg

    def __init__(self, max_pos: int = 5):
        self.risk = _RiskCfg(max_concurrent_positions=max_pos)


def _make_db(tmp_path: Path, tickers: dict | None = None, holdings: list[dict] | None = None) -> Path:
    """Create a test SQLite DB with universe and holdings tables."""
    db_path = tmp_path / "test.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS universe "
            "(ticker TEXT PRIMARY KEY, halted INTEGER DEFAULT 0, delisted INTEGER DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS holdings "
            "(ticker TEXT, state TEXT, mode TEXT)"
        )
        if tickers:
            for t, status in tickers.items():
                conn.execute(
                    "INSERT INTO universe (ticker, halted, delisted) VALUES (?, ?, ?)",
                    (t, int(status.get("halted", False)), int(status.get("delisted", False))),
                )
        if holdings:
            for h in holdings:
                conn.execute(
                    "INSERT INTO holdings (ticker, state, mode) VALUES (?, ?, ?)",
                    (h["ticker"], h["state"], h.get("mode", "PAPER")),
                )
        conn.commit()
    return db_path


# ---------------------------------------------------------------------------
# Check 5: Antipattern
# ---------------------------------------------------------------------------


def test_antipattern_rejects(tmp_path: Path) -> None:
    """Antipattern check triggers rejection with reason."""
    db_path = _make_db(tmp_path)
    with patch("pmacs.agents.gatekeeper.check_antipattern", return_value="REPEAT_LOSER"):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg(), kill_switch_engaged=False)
    assert result.admitted is False
    assert "ANTIPATTERN" in (result.reject_reason or "")


def test_antipattern_passes_when_none(tmp_path: Path) -> None:
    """No antipattern returns None, ticker admitted."""
    db_path = _make_db(tmp_path)
    with patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert result.admitted is True


# ---------------------------------------------------------------------------
# Check 6: Limited-history flagging
# ---------------------------------------------------------------------------


def test_limited_history_flagged(tmp_path: Path) -> None:
    """Ticker with < 90 days history gets LIMITED_HISTORY flag."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._days_of_history", return_value=45),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert result.admitted is True
    assert "LIMITED_HISTORY" in result.flags


def test_limited_history_not_flagged_above_threshold(tmp_path: Path) -> None:
    """Ticker with >= 90 days history does NOT get flag."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._days_of_history", return_value=120),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert "LIMITED_HISTORY" not in result.flags


def test_limited_history_exactly_at_threshold(tmp_path: Path) -> None:
    """Exactly 90 days = NOT flagged (boundary test)."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._days_of_history", return_value=90),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert "LIMITED_HISTORY" not in result.flags


# ---------------------------------------------------------------------------
# Check 7: ADV check flagging
# ---------------------------------------------------------------------------


def test_adv_below_threshold_flagged(tmp_path: Path) -> None:
    """Low ADV gets ADV_BELOW_THRESHOLD flag."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._adv_90d", return_value=500_000.0),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert result.admitted is True
    assert "ADV_BELOW_THRESHOLD" in result.flags


def test_adv_above_threshold_not_flagged(tmp_path: Path) -> None:
    """High ADV does NOT get flag."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._adv_90d", return_value=5_000_000.0),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert "ADV_BELOW_THRESHOLD" not in result.flags


def test_adv_exactly_at_threshold(tmp_path: Path) -> None:
    """Exactly $1M ADV = NOT flagged (boundary test)."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._adv_90d", return_value=ADV_MINIMUM_USD),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert "ADV_BELOW_THRESHOLD" not in result.flags


# ---------------------------------------------------------------------------
# Multiple flags + combined checks
# ---------------------------------------------------------------------------


def test_both_flags_simultaneously(tmp_path: Path) -> None:
    """Both LIMITED_HISTORY and ADV_BELOW_THRESHOLD can be set at once."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._days_of_history", return_value=30),
        patch("pmacs.agents.gatekeeper._adv_90d", return_value=100_000.0),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert result.admitted is True
    assert "LIMITED_HISTORY" in result.flags
    assert "ADV_BELOW_THRESHOLD" in result.flags


def test_flags_set_even_when_admitted(tmp_path: Path) -> None:
    """Flags are set even when admission passes other checks."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._days_of_history", return_value=10),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert result.admitted is True
    assert len(result.flags) > 0


def test_no_flags_when_all_healthy(tmp_path: Path) -> None:
    """No flags when history and ADV are above thresholds."""
    db_path = _make_db(tmp_path)
    with (
        patch("pmacs.agents.gatekeeper.check_antipattern", return_value=None),
        patch("pmacs.agents.gatekeeper._days_of_history", return_value=200),
        patch("pmacs.agents.gatekeeper._adv_90d", return_value=10_000_000.0),
    ):
        result = gate("XYZ", "cycle-001", db_path=db_path, config=_Cfg())
    assert result.admitted is True
    assert result.flags == []


def test_result_is_frozen(tmp_path: Path) -> None:
    """GatekeeperResult is immutable."""
    result = GatekeeperResult(ticker="AAPL", admitted=True, flags=[])
    with pytest.raises(Exception):
        result.admitted = False  # type: ignore


def test_constants_match_spec() -> None:
    """Thresholds match spec values (Agents.md §4.2)."""
    assert LIMITED_HISTORY_DAYS == 90
    assert ADV_MINIMUM_USD == 1_000_000.0
