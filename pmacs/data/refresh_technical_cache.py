"""One-shot refresh of technical evidence for tickers missing it in the cache.

The /ticker/{ticker} page (Source.md §16.8) shows a Technical section with RSI,
SMA50/200, 52-week range. When a ticker has been added to the universe but
never run a full cycle that triggered the technical fetcher, those packets
don't exist in the cache and the section falls back to em-dashes for every
field.

Polygon DOES return bars for these tickers (probe confirmed HIMS=275 bars,
OMDA=260 bars); the cache gap is from the cycle never invoking the technical
fetcher for them. This utility re-fetches technicals directly and writes the
evidence rows.

Usage::

    python -m pmacs.data.refresh_technical_cache            # all cached tickers
    python -m pmacs.data.refresh_technical_cache HIMS OMDA  # specific tickers
"""
from __future__ import annotations

import sys

from pmacs.data.evidence_router import _save_evidence_cache
from pmacs.data.gateway import DataGateway
from pmacs.data.sources.technical import fetch_technical
from pmacs.storage.keychain import get_api_key


def _has_technical(ticker: str) -> bool:
    """True when both technical_* packets are present and non-empty."""
    import sqlite3
    from pmacs.config import data_dir
    db = data_dir() / "pmacs.db"
    if not db.exists():
        return False
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT evidence_id FROM evidence_cache "
            "WHERE ticker = ? AND evidence_id IN (?, ?)",
            (ticker,
             f"technical_{ticker}_moving_averages",
             f"technical_{ticker}_momentum"),
        ).fetchall()
        return len(rows) >= 2
    finally:
        con.close()


def refresh(ticker: str, *, force: bool = False) -> bool:
    """Re-fetch technical evidence for ``ticker`` and overwrite the cache rows.

    Returns True if any evidence was written.
    """
    if not force and _has_technical(ticker):
        return False
    api_key = get_api_key("pmacs.data.polygon", "api_key") or get_api_key(
        "pmacs.credentials", "polygon_key"
    )
    if not api_key:
        print(f"{ticker}: no Polygon API key — cannot refresh")
        return False
    with DataGateway() as gw:
        packet = fetch_technical(ticker, gw, api_key, cycle_id="refresh_technical")
    if not packet.evidence:
        print(f"{ticker}: no technical evidence produced (Polygon returned no bars)")
        return False
    _save_evidence_cache(ticker, list(packet.evidence), cycle_id="refresh_technical")
    return True


def _all_cached_tickers() -> list[str]:
    import sqlite3
    from pmacs.config import data_dir
    db = data_dir() / "pmacs.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    try:
        rows = con.execute("SELECT DISTINCT ticker FROM evidence_cache ORDER BY ticker").fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def main(argv: list[str]) -> int:
    tickers = [t.upper() for t in argv[1:]] or _all_cached_tickers()
    if not tickers:
        print("no tickers in cache; nothing to refresh")
        return 0
    refreshed = 0
    for t in tickers:
        try:
            ok = refresh(t)
        except Exception as exc:
            print(f"{t}: FAILED ({exc})")
            continue
        if ok:
            print(f"{t}: refreshed")
            refreshed += 1
        else:
            print(f"{t}: already current (skipped)")
    print(f"\n{refreshed}/{len(tickers)} ticker(s) refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
