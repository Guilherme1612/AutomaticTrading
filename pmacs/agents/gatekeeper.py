"""Gatekeeper — deterministic admittance filter (Agents.md §4).

This is NOT an LLM persona. Pure Python. Runs before slot dispatch.

Admittance checks (ordered; fail-fast):
  1. Kill switch check
  2. Halted / delisted check
  3. Stale CRITICAL data check
  4. Max concurrent positions check
  5. Antipattern check (MemoryEngine — stub, always passes)
  6. Earnings blackout: REJECT if earnings within EARNINGS_BLACKOUT_DAYS days
  7. Market cap: REJECT if below MIN_MARKET_CAP_USD
  8. Limited-history flagging (does NOT reject; just flags)
  9. ADV check (flags if below threshold)
"""

from __future__ import annotations

import sqlite3  # noqa: F811 — kept for type refs

from pmacs.storage.sqlite import connect as _sql_connect
from dataclasses import dataclass
from datetime import datetime, timezone
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
        conn = _sql_connect(db_path, read_only=True)
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
        conn = _sql_connect(db_path, read_only=True)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM holdings WHERE state = 'ACTIVE'"
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
        conn = _sql_connect(db_path, read_only=True)
        try:
            cur = conn.execute(
                "SELECT 1 FROM holdings WHERE ticker = ? AND state = 'ACTIVE'",
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


def _days_until_earnings(ticker: str, db_path: Path) -> int | None:
    """Days until the next scheduled earnings date for ticker.

    Queries the universe table for next_earnings_date (ISO date string).
    Returns None if no earnings date is known (safe — skip the check).
    Returns 0 if earnings are today or overdue.
    """
    if not db_path.exists():
        return None
    try:
        conn = _sql_connect(db_path, read_only=True)
        try:
            cur = conn.execute(
                "SELECT next_earnings_date FROM universe WHERE ticker = ?",
                (ticker,),
            )
            row = cur.fetchone()
            if row is None or row[0] is None:
                return None
            # next_earnings_date expected as "YYYY-MM-DD"
            earnings_dt = datetime.strptime(str(row[0])[:10], "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            today = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            delta = (earnings_dt - today).days
            return max(0, delta)
        finally:
            conn.close()
    except (sqlite3.OperationalError, ValueError):
        # Column doesn't exist yet or bad date format — skip the check
        return None


def _market_cap_usd(ticker: str, db_path: Path) -> float | None:
    """Market cap in USD from the universe table.

    Returns None if unknown (safe — skip the check).
    Stub fallback: returns None until universe table is populated with market_cap_usd.
    """
    if not db_path.exists():
        return None
    try:
        conn = _sql_connect(db_path, read_only=True)
        try:
            cur = conn.execute(
                "SELECT market_cap_usd FROM universe WHERE ticker = ?",
                (ticker,),
            )
            row = cur.fetchone()
            if row is None or row[0] is None:
                return None
            return float(row[0])
        finally:
            conn.close()
    except (sqlite3.OperationalError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ADV_MINIMUM_USD = 1_000_000.0
LIMITED_HISTORY_DAYS = 90
# Earnings blackout: reject new entries if earnings within this many calendar days.
# Active holdings are NOT rejected — only new admissions. This avoids entering
# a position right before binary earnings risk.
EARNINGS_BLACKOUT_DAYS = 3
# Minimum market cap: reject nano-caps / pink sheets. $500M = small-cap floor.
MIN_MARKET_CAP_USD = 500_000_000.0

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

    Hard rejects (in order):
      1. Kill switch engaged
      2. Halted or delisted
      3. Stale CRITICAL data
      4. Portfolio position limit (unless already holding)
      5. Antipattern (MemoryEngine)
      6. Earnings within EARNINGS_BLACKOUT_DAYS (new admissions only)
      7. Market cap below MIN_MARKET_CAP_USD (if known)

    Soft flags (admitted=True, flags populated):
      8. LIMITED_HISTORY (< 90 days OHLCV)
      9. ADV_BELOW_THRESHOLD (< $1M daily volume)

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

    # 6. Earnings blackout: reject NEW admissions within EARNINGS_BLACKOUT_DAYS.
    # Active holdings are skipped — we don't force-close on earnings approach;
    # the stop-loss daemon handles that separately.
    if not _has_active_position(ticker, db_path):
        days_to_earnings = _days_until_earnings(ticker, db_path)
        if days_to_earnings is not None and days_to_earnings <= EARNINGS_BLACKOUT_DAYS:
            return GatekeeperResult(
                ticker=ticker,
                admitted=False,
                reject_reason=f"EARNINGS_BLACKOUT:{days_to_earnings}d",
            )

    # 7. Market cap floor: reject nano-caps. If market_cap is unknown, pass
    # (benefit of the doubt — universe curation is the primary defence).
    mktcap = _market_cap_usd(ticker, db_path)
    if mktcap is not None and mktcap < MIN_MARKET_CAP_USD:
        return GatekeeperResult(
            ticker=ticker,
            admitted=False,
            reject_reason=f"MARKET_CAP_TOO_SMALL:{mktcap:.0f}",
        )

    # 8 & 9. Flagging (does NOT reject)
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
