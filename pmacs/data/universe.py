"""Universe management — operator-curated ticker list (Source.md §8)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class UniverseEntry:
    ticker: str
    sector: str | None = None
    subsector: str | None = None
    halted: bool = False
    delisted: bool = False
    catalyst_type: str | None = None
    pinned_priority: int | None = None


def init_universe_table(conn: sqlite3.Connection) -> None:
    """Create the universe table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universe (
            ticker TEXT PRIMARY KEY,
            sector TEXT,
            subsector TEXT,
            halted INTEGER NOT NULL DEFAULT 0,
            delisted INTEGER NOT NULL DEFAULT 0,
            catalyst_type TEXT,
            pinned_priority INTEGER,
            added_at TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.commit()


def add_ticker(conn: sqlite3.Connection, entry: UniverseEntry) -> None:
    """Add or update a ticker in the universe."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO universe (ticker, sector, subsector, halted, delisted, catalyst_type, pinned_priority, added_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.ticker, entry.sector, entry.subsector,
            int(entry.halted), int(entry.delisted),
            entry.catalyst_type, entry.pinned_priority, now,
        ),
    )
    conn.commit()


def remove_ticker(conn: sqlite3.Connection, ticker: str) -> bool:
    """Remove a ticker from the universe. Returns True if it existed."""
    cursor = conn.execute("DELETE FROM universe WHERE ticker = ?", (ticker,))
    conn.commit()
    return cursor.rowcount > 0


def get_universe(conn: sqlite3.Connection, include_halted: bool = False) -> list[UniverseEntry]:
    """Get all tickers in the universe."""
    query = "SELECT ticker, sector, subsector, halted, delisted, catalyst_type, pinned_priority FROM universe"
    if not include_halted:
        query += " WHERE halted = 0 AND delisted = 0"
    # pinned_priority ASC first (1 = highest), then by added_at oldest-first for stability
    query += " ORDER BY COALESCE(pinned_priority, 999) ASC, added_at ASC"

    rows = conn.execute(query).fetchall()
    return [
        UniverseEntry(
            ticker=r[0], sector=r[1], subsector=r[2],
            halted=bool(r[3]), delisted=bool(r[4]),
            catalyst_type=r[5], pinned_priority=r[6],
        )
        for r in rows
    ]


def flag_halted(conn: sqlite3.Connection, ticker: str, halted: bool = True) -> None:
    """Flag/unflag a ticker as halted."""
    conn.execute("UPDATE universe SET halted = ? WHERE ticker = ?", (int(halted), ticker))
    conn.commit()


_DEFAULT_UNIVERSE: dict[str, dict[str, str | None]] = {
    # Large-cap tech with richest data for all 7 agents (IMP-5)
    "MSFT": {"sector": "Technology", "subsector": "Cloud / Enterprise"},
    "AMZN": {"sector": "Technology", "subsector": "E-Commerce / Cloud"},
}


def seed_default_universe(conn: sqlite3.Connection) -> list[str]:
    """Insert default tickers into the universe if they are missing.

    Idempotent: uses INSERT OR IGNORE. Returns the list of tickers actually
    inserted in this call.
    """
    from datetime import datetime, timezone

    init_universe_table(conn)
    inserted: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for ticker, meta in _DEFAULT_UNIVERSE.items():
        cursor = conn.execute(
            """INSERT OR IGNORE INTO universe
               (ticker, sector, subsector, halted, delisted, catalyst_type, pinned_priority, added_at)
               VALUES (?, ?, ?, 0, 0, NULL, NULL, ?)""",
            (ticker, meta.get("sector"), meta.get("subsector"), now),
        )
        if cursor.rowcount > 0:
            inserted.append(ticker)
    conn.commit()
    return inserted
