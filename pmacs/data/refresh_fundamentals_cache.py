"""One-shot refresh of stale fundamentals evidence in the SQLite evidence cache.

The Ticker Data page (Source.md §16.8) is read-only and renders *stored* evidence
only — it never re-fetches fundamentals, because the page must match the memo the
personas consumed (accuracy contract). Annual cash-flow / revenue / EBITDA / debt
/ cash series are required to compute the 3-year valuation averages.

After the 2026-06-17 switch to yfinance as the primary fundamentals source, any
ticker whose last cycle ran *before* the switch has a stale Finnhub
``fundamentals_{ticker}_metrics`` in the cache (``_source`` is None, annual series
missing) — so the page shows "—" / "3Y average unavailable" for every 3Y figure.

This utility re-fetches fundamentals via yfinance (primary) and overwrites the two
fundamentals evidence rows in the cache, leaving all other evidence untouched.
Run it when the page shows stale fundamentals for a ticker that yfinance can serve.

Usage::

    python -m pmacs.data.refresh_fundamentals_cache            # all stale tickers
    python -m pmacs.data.refresh_fundamentals_cache TEM ZETA    # specific tickers
"""
from __future__ import annotations

import sys

from pmacs.data.evidence_router import _load_evidence_cache, _save_evidence_cache
from pmacs.data.sources.yfinance_fundamentals import fetch_fundamentals_yf


def _is_stale(ticker: str) -> bool:
    """True if the cached fundamentals metrics lack the annual series or predate yfinance."""
    for e in _load_evidence_cache(ticker):
        if e.id != f"fundamentals_{ticker}_metrics":
            continue
        m = e.data or {}
        if m.get("_source") != "yfinance":
            return True
        for key in ("annual_freeCashFlow", "annual_revenue", "annual_ebitda"):
            if not isinstance(m.get(key), list) or not m.get(key):
                return True
        return False
    return True  # no fundamentals row at all


def refresh(ticker: str, *, force: bool = False) -> bool:
    """Re-fetch yfinance fundamentals for ``ticker`` and overwrite the cache rows.

    Returns True if the cache was updated.
    """
    if not force and not _is_stale(ticker):
        return False
    packet = fetch_fundamentals_yf(ticker, None, api_key="", cycle_id="refresh")
    if not packet.evidence:
        return False
    _save_evidence_cache(ticker, packet.evidence, cycle_id="refresh")
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
        except Exception as exc:  # pragma: no cover - network dependent
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
