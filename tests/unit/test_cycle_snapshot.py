"""Task #8 Part D — DB-backed snapshot reconstruction from persisted memos.

Tests pmacs.web.cycle_snapshot, which replaces the demo path's live globals
with reads of the memos table. The reconstructed shapes must match what the
demo globals exposed (so route/template code is unchanged).

spec_ref: Architecture.md §16.9 (memos table), Source.md §15.5/§16.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pmacs.storage.sqlite import init_db
from pmacs.web.cycle_snapshot import (
    latest_memo,
    recent_tickers,
    running_cycle_state,
    ticker_snapshot,
)


def _insert_memo(db, ticker: str, memo: dict, decided_at: str, verdict: str = "BUY") -> None:
    cycle_id = f"CYC-{ticker}-{decided_at}"
    db.execute(
        "INSERT OR IGNORE INTO cycles (cycle_id, opened_at, state, trigger, mode) "
        "VALUES (?, ?, 'CLOSED', 'manual', 'PAPER')",
        (cycle_id, decided_at),
    )
    db.execute(
        "INSERT INTO memos (cycle_id, ticker, verdict, conviction_score, memo_json, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cycle_id, ticker, verdict, 0.55, json.dumps(memo), decided_at),
    )
    db.commit()


def _full_memo() -> dict:
    return {
        "verdict_line": "STRONG_BUY — conviction 0.65",
        "p_up": 0.62, "p_flat": 0.20, "p_down": 0.18, "conviction": 0.65,
        "agent_signals": [
            {"persona": "growth_hunter", "signal": "bullish", "direction": "bullish",
             "p_up": 0.70, "p_flat": 0.20, "p_down": 0.10, "confidence": 0.60,
             "analysis": "rev growth 48%", "evidence_cited": ["e1", "e2"]},
            {"persona": "short_interest", "signal": "bearish", "direction": "bearish",
             "p_up": 0.20, "p_flat": 0.30, "p_down": 0.50, "confidence": 0.40,
             "analysis": "short 9.5%", "evidence_cited": ["e3"]},
        ],
        "crucible_severity": 0.35,
        "crucible_attacks": [{"attack_type": "moat", "description": "thin"}],
        "crucible_summary": "Thesis survives with minor concerns.",
        "crucible_thesis_survives": True,
    }


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()


class TestTickerSnapshot:
    def test_reconstructs_results_crucible_arb_from_memo(self, db):
        _insert_memo(db, "OUST", _full_memo(), "2026-06-24T10:00:00Z")
        snap = ticker_snapshot(db, "OUST")
        assert snap["ticker"] == "OUST"
        assert snap["decided_at"] == "2026-06-24T10:00:00Z"

        results = snap["results"]
        assert len(results) == 2
        gh = next(r for r in results if r["persona"] == "growth_hunter")
        assert gh["key_signal"] == "bullish"
        assert gh["p_up"] == pytest.approx(0.70)
        assert gh["p_flat"] == pytest.approx(0.20)
        assert gh["p_down"] == pytest.approx(0.10)
        assert gh["confidence"] == pytest.approx(0.60)
        assert gh["analysis"] == "rev growth 48%"
        assert gh["evidence_cited"] == ["e1", "e2"]
        assert gh["completed_at"] == "2026-06-24T10:00:00Z"

        cru = snap["crucible"]
        assert cru["severity"] == pytest.approx(0.35)
        assert cru["thesis_survives"] is True
        assert cru["summary"] == "Thesis survives with minor concerns."
        assert cru["attacks"] == [{"attack_type": "moat", "description": "thin"}]

        arb = snap["arb"]
        assert arb["p_up"] == pytest.approx(0.62)
        assert arb["p_down"] == pytest.approx(0.18)
        assert arb["agents_used"] == 2
        assert arb["conviction"] == pytest.approx(0.65)
        assert arb["verdict"] == "STRONG_BUY"

    def test_latest_overall_when_no_ticker_given(self, db):
        _insert_memo(db, "AAPL", {"verdict_line": "HOLD — conviction 0.10",
                                  "p_up": 0.4, "p_down": 0.4, "conviction": 0.10},
                     "2026-06-24T09:00:00Z", verdict="HOLD")
        _insert_memo(db, "OUST", _full_memo(), "2026-06-24T10:00:00Z")
        snap = ticker_snapshot(db)
        # Most recent overall is OUST (10:00 > 09:00).
        assert snap["ticker"] == "OUST"
        assert len(snap["results"]) == 2

    def test_empty_when_no_memo(self, db):
        snap = ticker_snapshot(db, "NOPE")
        assert snap["ticker"] == "NOPE"
        assert snap["results"] == []
        assert snap["crucible"] == {}
        assert snap["arb"] == {}

    def test_missing_p_flat_is_derived(self, db):
        _insert_memo(db, "PLTR", {"p_up": 0.6, "p_down": 0.3, "conviction": 0.5,
                                  "agent_signals": [{"persona": "moat_analyst",
                                                     "p_up": 0.6, "p_down": 0.3,
                                                     "confidence": 0.5}]},
                     "2026-06-24T11:00:00Z")
        snap = ticker_snapshot(db, "PLTR")
        assert snap["results"][0]["p_flat"] == pytest.approx(0.10)
        assert snap["arb"]["p_flat"] == pytest.approx(0.10)

    def test_crucible_empty_when_not_injected(self, db):
        # Crucible skipped (e.g. low-severity fast path) — no crucible_* keys.
        _insert_memo(db, "NET", {"p_up": 0.5, "p_down": 0.3, "conviction": 0.2,
                                 "agent_signals": []},
                     "2026-06-24T12:00:00Z")
        snap = ticker_snapshot(db, "NET")
        assert snap["crucible"] == {}


class TestRecentTickers:
    def test_ordered_most_recent_first(self, db):
        _insert_memo(db, "AAPL", {"p_up": 0.5}, "2026-06-24T09:00:00Z")
        _insert_memo(db, "OUST", {"p_up": 0.5}, "2026-06-24T10:00:00Z")
        _insert_memo(db, "AAPL", {"p_up": 0.5}, "2026-06-24T11:00:00Z")
        assert recent_tickers(db) == ["AAPL", "OUST"]


class TestRunningCycleState:
    def test_no_running_cycle(self, db):
        st = running_cycle_state(db)
        assert st["is_running"] is False
        assert st["cycle_id"] == ""
        assert st["current_ticker"] == ""
        assert st["cycle_tickers"] == []

    def test_running_cycle_with_in_flight_ticker(self, db):
        now = datetime.now(timezone.utc).isoformat()
        # The orchestrator's in-progress state is OPEN (initiate_cycle inserts
        # OPEN, close_cycle sets CLOSED); RUNNING was the demo path's state.
        db.execute(
            "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) "
            "VALUES (?, ?, 'OPEN', 'manual', 'PAPER')", ("CYC-2", now))
        # P3 (OUST) started but not done; P1 (AAPL) pending; P2 (MSFT) done.
        db.execute(
            "INSERT INTO queue (cycle_id, ticker, priority_band, pinned, enqueued_at, "
            "started_at, completed_at) VALUES (?, 'MSFT', 2, 0, ?, ?, ?)",
            ("CYC-2", now, now, now))
        db.execute(
            "INSERT INTO queue (cycle_id, ticker, priority_band, pinned, enqueued_at, "
            "started_at, completed_at) VALUES (?, 'OUST', 3, 0, ?, ?, NULL)",
            ("CYC-2", now, now))
        db.execute(
            "INSERT INTO queue (cycle_id, ticker, priority_band, pinned, enqueued_at, "
            "started_at, completed_at) VALUES (?, 'AAPL', 1, 0, ?, NULL, NULL)",
            ("CYC-2", now))
        db.commit()
        st = running_cycle_state(db)
        assert st["is_running"] is True
        assert st["cycle_id"] == "CYC-2"
        # MSFT done, OUST in-flight, AAPL pending → next is AAPL (first pending by band).
        assert st["current_ticker"] == "OUST"
        assert st["next_ticker"] == "AAPL"
        assert set(st["cycle_tickers"]) == {"MSFT", "OUST", "AAPL"}

    def test_legacy_running_state_still_detected(self, db):
        # Legacy demo-path cycles used state='RUNNING'; the IN clause keeps them
        # detectable so a DB with old rows still shows in-flight state.
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO cycles (cycle_id, opened_at, state, trigger, mode) "
            "VALUES (?, ?, 'RUNNING', 'manual', 'PAPER')", ("CYC-LEG", now))
        db.execute(
            "INSERT INTO queue (cycle_id, ticker, priority_band, pinned, enqueued_at) "
            "VALUES (?, 'OUST', 1, 0, ?)", ("CYC-LEG", now))
        db.commit()
        st = running_cycle_state(db)
        assert st["is_running"] is True
        assert st["cycle_id"] == "CYC-LEG"


class TestLatestMemo:
    def test_returns_none_when_empty(self, db):
        assert latest_memo(db) is None
        assert latest_memo(db, "OUST") is None
