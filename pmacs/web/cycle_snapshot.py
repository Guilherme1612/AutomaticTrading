"""DB-backed reconstruction of per-ticker cycle results from persisted memos.

Task #8 Part D — replaces the demo path's in-memory globals
(``_last_cycle_agent_results`` / ``_last_cycle_crucible_results`` /
``_last_cycle_arbitration`` / ``_last_cycle_id`` / ``_current_cycle_tickers`` /
``_current_ticker_processing``) with reads of the ``memos`` table.

The orchestrator (Part C, orchestrator.py ``_step_13mn_post_decision``) now
deterministically injects ``agent_signals`` + the four ``crucible_*`` fields +
the authoritative arbitration numbers into ``memos.memo_json``, so the
agents / memo / ticker pages render from persisted JSON alone — no in-process
globals. This survives process restarts, does not depend on the demo path
(which Part E deletes), and reflects any cycle, not just the last in-memory one.

The reconstructed shapes match what the demo globals exposed so the route code
and templates are unchanged:

- ``results``: list of ``{persona, key_signal, direction, p_up, p_down, p_flat,
  confidence, analysis, evidence_cited, completed_at}`` (one per agent signal).
- ``crucible``: ``{severity, thesis_survives, summary, attacks, completed_at}``
  (empty dict when no crucible ran).
- ``arb``: ``{p_up, p_down, p_flat, direction, agents_used, conviction,
  ev_multiple, verdict}`` (empty dict when no arbitration numbers).

spec_ref: Architecture.md §16.9 (memos table), Source.md §15.5/§16.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def parse_memo_json(raw: str | None) -> dict:
    """Parse a memo_json string into a dict; empty dict on failure."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _signal_to_result(sig: dict, decided_at: str) -> dict:
    """Map one injected agent_signal to the demo path's per-persona result shape."""
    p_up = float(sig.get("p_up", 0.0) or 0.0)
    p_down = float(sig.get("p_down", 0.0) or 0.0)
    p_flat = float(sig.get("p_flat", 0.0) or 0.0)
    # Defend against malformed p_flat (should be 1 - p_up - p_down).
    if p_flat <= 0.0:
        p_flat = max(0.0, 1.0 - p_up - p_down)
    return {
        "persona": sig.get("persona", ""),
        "key_signal": sig.get("signal") or sig.get("key_signal") or "",
        "direction": sig.get("direction", ""),
        "p_up": p_up,
        "p_down": p_down,
        "p_flat": p_flat,
        "confidence": float(sig.get("confidence", 0.0) or 0.0),
        "analysis": sig.get("analysis", "") or "",
        "evidence_cited": list(sig.get("evidence_cited", []) or []),
        "completed_at": decided_at,
    }


def _memo_to_crucible(memo: dict, decided_at: str) -> dict:
    """Reconstruct the crucible-result shape from the injected crucible_* fields."""
    if "crucible_severity" not in memo:
        return {}
    return {
        "severity": float(memo.get("crucible_severity", 0.0) or 0.0),
        "thesis_survives": bool(memo.get("crucible_thesis_survives", True)),
        "summary": memo.get("crucible_summary", "") or "",
        "attacks": list(memo.get("crucible_attacks", []) or []),
        "completed_at": decided_at,
    }


def _memo_to_arbitration(memo: dict) -> dict:
    """Reconstruct the arbitration-result shape from the injected numbers."""
    if "p_up" not in memo and "conviction" not in memo:
        return {}
    p_up = float(memo.get("p_up", 0.0) or 0.0)
    p_down = float(memo.get("p_down", 0.0) or 0.0)
    p_flat = float(memo.get("p_flat", 0.0) or 0.0)
    if p_flat <= 0.0:
        p_flat = max(0.0, 1.0 - p_up - p_down)
    verdict_line = memo.get("verdict_line", "") or ""
    # verdict_line is "<VERDICT> — conviction <n>" — take the tag before the dash.
    verdict = verdict_line.split(" — ")[0].strip() if verdict_line else ""
    return {
        "p_up": p_up,
        "p_down": p_down,
        "p_flat": p_flat,
        "direction": round(p_up - p_down, 6),
        "agents_used": len(memo.get("agent_signals") or []),
        "conviction": float(memo.get("conviction", 0.0) or 0.0),
        "ev_multiple": float(memo.get("ev_multiple", 0.0) or 0.0),
        "verdict": verdict,
    }


def latest_memo(
    db: sqlite3.Connection, ticker: str | None = None,
) -> tuple[str, dict, str] | None:
    """Return ``(ticker, memo_dict, decided_at)`` for the latest memo.

    When ``ticker`` is given, the latest memo for that ticker; otherwise the
    single most-recent memo across all tickers. ``None`` when no memos exist.
    """
    if ticker:
        row = db.execute(
            "SELECT ticker, memo_json, decided_at FROM memos WHERE ticker = ? "
            "ORDER BY decided_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT ticker, memo_json, decided_at FROM memos "
            "ORDER BY decided_at DESC LIMIT 1",
        ).fetchone()
    if not row:
        return None
    return row[0], parse_memo_json(row[1]), row[2]


def ticker_snapshot(
    db: sqlite3.Connection, ticker: str | None = None,
) -> dict[str, Any]:
    """Reconstruct the demo-global shapes from the latest memo (optionally a ticker).

    Returns ``{ticker, results, crucible, arb, decided_at}``. ``ticker`` is the
    empty string and the result containers are empty when no memo exists.
    """
    row = latest_memo(db, ticker)
    if not row:
        return {
            "ticker": ticker or "",
            "results": [],
            "crucible": {},
            "arb": {},
            "decided_at": "",
        }
    t, memo, decided_at = row
    results = [_signal_to_result(s, decided_at) for s in (memo.get("agent_signals") or [])]
    return {
        "ticker": t,
        "results": results,
        "crucible": _memo_to_crucible(memo, decided_at),
        "arb": _memo_to_arbitration(memo),
        "decided_at": decided_at or "",
    }


def recent_tickers(db: sqlite3.Connection, limit: int = 50) -> list[str]:
    """Tickers with persisted memos, most-recently-analyzed first."""
    rows = db.execute(
        "SELECT ticker FROM memos GROUP BY ticker ORDER BY MAX(decided_at) DESC "
        f"LIMIT {int(limit)}"
    ).fetchall()
    return [r[0] for r in rows]


def running_cycle_state(db: sqlite3.Connection) -> dict[str, Any]:
    """Live cycle state from the cycles + queue tables (replaces the demo globals
    ``_current_cycle_tickers`` / ``_current_ticker_processing`` / ``_last_cycle_id``).

    Returns ``{is_running, cycle_id, current_ticker, next_ticker, cycle_tickers}``.
    The in-flight ticker is the queue row with ``started_at`` set and
    ``completed_at`` NULL; the next is the first still-pending (started_at NULL)
    row in priority order.
    """
    running = db.execute(
        "SELECT cycle_id FROM cycles WHERE state = 'RUNNING' "
        "ORDER BY opened_at DESC LIMIT 1"
    ).fetchone()
    cycle_id = running[0] if running else ""
    is_running = running is not None
    current_ticker = ""
    next_ticker = ""
    cycle_tickers: list[str] = []
    if is_running:
        rows = db.execute(
            "SELECT ticker, started_at, completed_at FROM queue "
            "WHERE cycle_id = ? ORDER BY priority_band, enqueued_at",
            (cycle_id,),
        ).fetchall()
        cycle_tickers = [r[0] for r in rows]
        for ticker, started_at, completed_at in rows:
            if started_at and not completed_at:
                current_ticker = ticker
            elif not started_at and not next_ticker:
                next_ticker = ticker
    return {
        "is_running": is_running,
        "cycle_id": cycle_id,
        "current_ticker": current_ticker,
        "next_ticker": next_ticker,
        "cycle_tickers": cycle_tickers,
    }
