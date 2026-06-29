"""Unit tests for the Ticker Data route helpers.

Covers deterministic extraction of primitives from stored evidence and the
analyst-consensus normalization. The full page render is tested via integration
fixtures; this module stays fast and offline.
"""
from __future__ import annotations

from unittest.mock import patch

from pmacs.web.routes.ticker_data import (
    _build_evidence_text,
    _evidence_fresh_enough,
    _extract_analyst,
    _is_universe_ticker,
    _LAZY_FETCH_IN_FLIGHT,
    _maybe_warm_evidence_cache,
    _universe_meta,
)


def test_build_evidence_text_joins_all_evidence():
    ev = {
        "fundamentals_TST_metrics": {"peNormalizedAnnual": 20.0, "forwardPE": 18.0},
        "technical_TST_moving_averages": {"current_price": 150.0},
        "agent_TST_value": {"analysis": "NRR is 118% and ARR grew to $1.2B."},
    }
    evidence_text, agent_text = _build_evidence_text("TST", ev)
    assert "peNormalizedAnnual: 20.0" in evidence_text
    assert "current_price: 150.0" in evidence_text
    assert "NRR is 118%" in agent_text


def test_extract_analyst_prefers_yahoo_price_target():
    ev = {
        "yahoo_TST_price_target": {
            "target_mean": 180.0,
            "target_high": 200.0,
            "target_low": 150.0,
            "target_median": 175.0,
            "num_analysts": 15,
            "current_price": 160.0,
            "upside_to_mean_pct": 12.5,
        },
        "finnhub_TST_price_target": {
            "target_mean": 170.0,
            "analyst_count": 10,
        },
        "finnhub_TST_analyst_recommendations": {
            "strong_buy": 3,
            "buy": 6,
            "hold": 5,
            "sell": 1,
            "strong_sell": 0,
            "total_analysts": 15,
            "consensus": "Buy",
        },
    }
    a = _extract_analyst("TST", ev)
    assert a["target_mean"] == 180.0
    assert a["num_analysts"] == 15
    assert a["buy"] == 6
    assert a["total_analysts"] == 15
    assert a["consensus"] == "Buy"


def test_extract_analyst_falls_back_to_finnhub():
    ev = {
        "finnhub_TST_price_target": {
            "target_mean": 170.0,
            "target_high": 190.0,
            "target_low": 140.0,
            "target_median": 165.0,
            "analyst_count": 10,
        },
    }
    a = _extract_analyst("TST", ev)
    assert a["target_mean"] == 170.0
    assert a["num_analysts"] == 10


def test_extract_analyst_returns_empty_on_no_data():
    assert _extract_analyst("TST", {}) == {
        "target_mean": None,
        "target_median": None,
        "target_high": None,
        "target_low": None,
        "num_analysts": None,
        "current_price": None,
        "upside_to_mean_pct": None,
        "strong_buy": None,
        "buy": None,
        "hold": None,
        "sell": None,
        "strong_sell": None,
        "total_analysts": None,
        "consensus": None,
    }


# ── Lazy-fetch helpers (operator directive 2026-06-19) ───────────────────


def test_is_universe_ticker_handles_missing_db(tmp_path, monkeypatch):
    """No pmacs.db → not in universe → no fetch attempt."""
    from pmacs.config import data_dir
    monkeypatch.setattr(data_dir, "__call__", lambda: tmp_path)
    # data_dir() returns Path; point it at the empty tmp
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)
    assert _is_universe_ticker("AMZN") is False


def test_is_universe_ticker_reads_universe_table(tmp_path, monkeypatch):
    """Tickers present in `universe` table (halted=0, delisted=0) are detected."""
    import sqlite3
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)

    db = tmp_path / "pmacs.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE universe (ticker TEXT, halted INT, delisted INT)"
    )
    con.execute("INSERT INTO universe VALUES ('AMZN', 0, 0)")
    con.execute("INSERT INTO universe VALUES ('BADX', 1, 0)")  # halted
    con.execute("INSERT INTO universe VALUES ('OLDX', 0, 1)")  # delisted
    con.commit()
    con.close()

    assert _is_universe_ticker("AMZN") is True
    assert _is_universe_ticker("BADX") is False
    assert _is_universe_ticker("OLDX") is False
    assert _is_universe_ticker("ZZNODATA") is False


def test_evidence_fresh_enough_no_db(tmp_path, monkeypatch):
    """No pmacs.db → evidence not fresh → lazy fetch will fire."""
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)
    assert _evidence_fresh_enough("AMZN") is False


def test_evidence_fresh_enough_respects_ttl(tmp_path, monkeypatch):
    """Evidence fetched within the TTL is fresh; older rows are stale."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)

    db = tmp_path / "pmacs.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE evidence_cache (ticker TEXT, fetched_at TEXT)"
    )
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
    con.execute("INSERT INTO evidence_cache VALUES ('FRESH', ?)", (fresh,))
    con.execute("INSERT INTO evidence_cache VALUES ('STALE', ?)", (stale,))
    con.commit()
    con.close()

    assert _evidence_fresh_enough("FRESH") is True
    assert _evidence_fresh_enough("STALE") is False
    assert _evidence_fresh_enough("MISSING") is False


def test_maybe_warm_evidence_cache_dedupes_when_in_flight():
    """Concurrent reloads while a fetch is running must NOT spawn a second one."""
    _LAZY_FETCH_IN_FLIGHT.add("AMZN")
    try:
        with patch(
            "pmacs.web.routes.ticker_data.asyncio.create_task"
        ) as mock_task:
            result = _maybe_warm_evidence_cache("AMZN")
            assert result is True
            mock_task.assert_not_called()
    finally:
        _LAZY_FETCH_IN_FLIGHT.discard("AMZN")


def test_maybe_warm_evidence_cache_skips_when_fresh(tmp_path, monkeypatch):
    """Fresh evidence → no fetch dispatched."""
    import sqlite3
    from datetime import datetime, timezone
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)

    db = tmp_path / "pmacs.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE evidence_cache (ticker TEXT, fetched_at TEXT)")
    fresh = datetime.now(timezone.utc).isoformat()
    con.execute("INSERT INTO evidence_cache VALUES ('AMZN', ?)", (fresh,))
    con.commit()
    con.close()

    with patch(
        "pmacs.web.routes.ticker_data.asyncio.create_task"
    ) as mock_task:
        result = _maybe_warm_evidence_cache("AMZN")
        assert result is False
        mock_task.assert_not_called()


# ── _universe_meta (operator directive 2026-06-23: always-visible
#    Universe column on the ticker-page workspace summary strip) ─────────


def test_universe_meta_returns_empty_shape_when_no_db(tmp_path, monkeypatch):
    """No pmacs.db → empty shape with in_universe falsy."""
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)
    meta = _universe_meta("AMZN")
    assert meta == {
        "in_universe": None,
        "sector": None,
        "subsector": None,
        "catalyst_type": None,
        "pinned_priority": None,
        "halted": None,
        "delisted": None,
        "added_at": None,
    }
    assert not meta["in_universe"]


def test_universe_meta_returns_empty_shape_when_ticker_missing(
    tmp_path, monkeypatch
):
    """DB exists, ticker not present → empty shape (not an error)."""
    import sqlite3
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)

    db = tmp_path / "pmacs.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE universe ("
        "ticker TEXT PRIMARY KEY, sector TEXT, subsector TEXT, "
        "halted INTEGER NOT NULL DEFAULT 0, "
        "delisted INTEGER NOT NULL DEFAULT 0, "
        "catalyst_type TEXT, pinned_priority INTEGER, added_at TEXT NOT NULL DEFAULT '')"
    )
    con.execute(
        "INSERT INTO universe VALUES ('OTHER', 'Tech', 'Infra', 0, 0, 'AI', 3, '2026-01-15')"
    )
    con.commit()
    con.close()

    meta = _universe_meta("AMZN")
    assert meta["in_universe"] is None
    assert meta["catalyst_type"] is None


def test_universe_meta_reads_full_row_with_correct_types(tmp_path, monkeypatch):
    """Round-trip a full row, asserting halted/delisted coerce to bool and
    pinned_priority / catalyst_type / added_at / sector / subsector pass
    through unchanged."""
    import sqlite3
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)

    db = tmp_path / "pmacs.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE universe ("
        "ticker TEXT PRIMARY KEY, sector TEXT, subsector TEXT, "
        "halted INTEGER NOT NULL DEFAULT 0, "
        "delisted INTEGER NOT NULL DEFAULT 0, "
        "catalyst_type TEXT, pinned_priority INTEGER, added_at TEXT NOT NULL DEFAULT '')"
    )
    con.execute(
        "INSERT INTO universe VALUES "
        "('AAPL', 'Technology', 'Hardware', 1, 0, 'AI_CATALYST', 5, '2026-02-20')"
    )
    con.execute(
        "INSERT INTO universe VALUES "
        "('NORM', 'Healthcare', 'Biotech', 0, 0, 'FDA_TRIAL', NULL, '2026-03-01')"
    )
    con.commit()
    con.close()

    # halted row
    halted = _universe_meta("AAPL")
    assert halted["in_universe"] is True
    assert halted["sector"] == "Technology"
    assert halted["subsector"] == "Hardware"
    assert halted["catalyst_type"] == "AI_CATALYST"
    assert halted["pinned_priority"] == 5
    assert halted["halted"] is True  # bool coercion from int 1
    assert halted["delisted"] is False
    assert halted["added_at"] == "2026-02-20"

    # NULL pinned_priority round-trips as None
    norm = _universe_meta("NORM")
    assert norm["in_universe"] is True
    assert norm["pinned_priority"] is None
    assert norm["halted"] is False
    assert norm["delisted"] is False


def test_universe_meta_delisted_row(tmp_path, monkeypatch):
    """Delisted row must surface delisted=True (the empty-state link copy
    uses in_universe truthy regardless, but the summary strip must show
    the DELISTED badge)."""
    import sqlite3
    import pmacs.config as _cfg
    monkeypatch.setattr(_cfg, "data_dir", lambda: tmp_path)

    db = tmp_path / "pmacs.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE universe ("
        "ticker TEXT PRIMARY KEY, sector TEXT, subsector TEXT, "
        "halted INTEGER NOT NULL DEFAULT 0, "
        "delisted INTEGER NOT NULL DEFAULT 0, "
        "catalyst_type TEXT, pinned_priority INTEGER, added_at TEXT NOT NULL DEFAULT '')"
    )
    con.execute(
        "INSERT INTO universe VALUES ('OLDX', 'Tech', '', 0, 1, '', NULL, '')"
    )
    con.commit()
    con.close()

    meta = _universe_meta("OLDX")
    assert meta["in_universe"] is True
    assert meta["delisted"] is True
    assert meta["halted"] is False
