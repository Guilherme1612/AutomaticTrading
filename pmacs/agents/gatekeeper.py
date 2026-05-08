"""Gatekeeper — deterministic admittance filter (Agents.md §4).

This is NOT an LLM persona. Pure Python. Runs before slot dispatch.

Admittance checks (ordered; fail-fast):
  1. Kill switch check
  2. Halted / delisted check
  3. Stale CRITICAL data check
  4. Max concurrent positions check
  5. Antipattern check (MemoryEngine — stub, always passes)
  6. Limited-history flagging (does NOT reject; just flags)
  7. ADV check (flags if below threshold)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from pmacs.engines.memory import check_antipattern


# ---------------------------------------------------------------------------
# Output schema (Agents.md §4.3)
# ---------------------------------------------------------------------------


class GatekeeperResult(BaseModel):
    """Deterministic admittance decision for a ticker."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    admitted: bool
    reject_reason: str | None = None
    flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Config protocol — what the gate function needs from config
# ---------------------------------------------------------------------------


class RiskConfigLike(Protocol):
    max_concurrent_positions: int


class ConfigLike(Protocol):
    risk: RiskConfigLike


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_halted_or_delisted(ticker: str, db_path: Path) -> bool:
    """Query the universe table for halted/delisted status."""
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT halted, delisted FROM universe WHERE ticker = ?",
                (ticker,),
            )
            row = cur.fetchone()
            if row is not None:
                return bool(row[0]) or bool(row[1])
        finally:
            conn.close()
    except sqlite3.OperationalError:
        # Table doesn't exist yet — assume not halted
        pass
    return False


def _count_active_positions(db_path: Path) -> int:
    """Count active (open) positions in the holdings table."""
    if not db_path.exists():
        return 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM holdings WHERE state = 'OPEN'"
            )
            row = cur.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return 0


def _has_active_position(ticker: str, db_path: Path) -> bool:
    """Check if ticker already has an open position."""
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT 1 FROM holdings WHERE ticker = ? AND state = 'OPEN'",
                (ticker,),
            )
            return cur.fetchone() is not None
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return False


def _days_of_history(ticker: str, db_path: Path) -> int:
    """Estimate days of OHLCV history available for a ticker.

    Stub: returns 120 (above the 90-day threshold) until the data pipeline
    is fully wired. Future implementation will query the pricing table.
    """
    return 120


def _adv_90d(ticker: str, db_path: Path) -> float:
    """Average daily volume over 90 days in USD.

    Stub: returns a value above typical thresholds ($1M) until the data
    pipeline is fully wired. Future implementation will query pricing data.
    """
    return 5_000_000.0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADV_MINIMUM_USD = 1_000_000.0
LIMITED_HISTORY_DAYS = 90

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def gate(
    ticker: str,
    cycle_id: str,
    *,
    db_path: Path,
    config: ConfigLike,
    kill_switch_engaged: bool = False,
    stale_critical: bool = False,
) -> GatekeeperResult:
    """Run all admittance checks for a ticker.

    Args:
        ticker: Ticker symbol.
        cycle_id: Current cycle identifier (for audit/logging).
        db_path: Path to the SQLite database.
        config: PMACS config (needs risk.max_concurrent_positions).
        kill_switch_engaged: Whether the kill switch is currently active.
        stale_critical: Whether CRITICAL data is stale for this ticker.

    Returns:
        GatekeeperResult with admission decision and any flags.
    """
    # 1. Kill switch check
    if kill_switch_engaged:
        return GatekeeperResult(
            ticker=ticker,
            admitted=False,
            reject_reason="KILL_SWITCH_ENGAGED",
        )

    # 2. Halted / delisted check
    if _is_halted_or_delisted(ticker, db_path):
        return GatekeeperResult(
            ticker=ticker,
            admitted=False,
            reject_reason="HALTED_OR_DELISTED",
        )

    # 3. Stale CRITICAL data check
    if stale_critical:
        return GatekeeperResult(
            ticker=ticker,
            admitted=False,
            reject_reason="STALE_CRITICAL_DATA",
        )

    # 4. Max concurrent positions check
    active_count = _count_active_positions(db_path)
    max_positions = config.risk.max_concurrent_positions
    if active_count >= max_positions:
        if not _has_active_position(ticker, db_path):
            return GatekeeperResult(
                ticker=ticker,
                admitted=False,
                reject_reason="PORTFOLIO_LIMIT_HIT",
            )

    # 5. Antipattern check (MemoryEngine stub — always passes)
    antipattern = check_antipattern(ticker, cycle_id)
    if antipattern:
        return GatekeeperResult(
            ticker=ticker,
            admitted=False,
            reject_reason=f"ANTIPATTERN: {antipattern}",
        )

    # 6 & 7. Flagging (does NOT reject)
    flags: list[str] = []

    if _days_of_history(ticker, db_path) < LIMITED_HISTORY_DAYS:
        flags.append("LIMITED_HISTORY")

    if _adv_90d(ticker, db_path) < ADV_MINIMUM_USD:
        flags.append("ADV_BELOW_THRESHOLD")

    return GatekeeperResult(
        ticker=ticker,
        admitted=True,
        flags=flags,
    )
