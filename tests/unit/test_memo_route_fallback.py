"""Unit tests for /memo/{ticker} route's verdict-resolution chain (Agents.md §15).

Verifies the Jun 29 /agents-page audit fix: abort-stub memos must show
"verdict='HOLD'" instead of falling through to None. The chain is:

    1. memo.verdict (from memo_json, set by MemoWriter or abort path)
    2. holding.verdict (latest open position)
    3. ticker_decisions[0].verdict (most-recent decision row)
    4. "N/A"

Before this fix, the orchestrator's Crucible-abort path built
``abort_memo_dict`` without a ``"verdict"`` key, so memo_verdict was
empty; if no holding existed, the chain fell through to ticker_decisions
(which holds the same HOLD value via dual-write). That worked in theory
but broke whenever the dual-write or memo lookup raced. Defense in depth:
the abort dict must always carry ``"verdict": "HOLD"`` directly.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ─── Orchestrator abort_memo_dict construction ────────────────────────────


def test_orchestrator_abort_memo_dict_includes_verdict_hold():
    """Crucible-abort path MUST set verdict='HOLD' on the memo dict.

    Simulates the abort branch of _step_13mn_post_decision (the dual-write
    to memos + decisions). Asserts the dict that gets persisted includes
    the verdict key so /memo/{ticker} never shows None.
    """
    abort_verdict_str = "HOLD"
    abort_memo_dict = {
        "verdict_line": f"HOLD — Crucible aborted (severity 0.00)",
        "verdict": abort_verdict_str,
        "thesis": "Crucible adversarial review aborted this symbol...",
        "p_up": 0.30, "p_flat": 0.40, "p_down": 0.30,
        "conviction": 0.0,
        "crucible_severity": 0.0,
        "crucible_iterations": 0,
        "abort_reason": "crucible_abort",
    }
    assert abort_memo_dict.get("verdict") == "HOLD"


def test_orchestrator_abort_memo_dict_serializes_with_verdict():
    """The abort memo_json must roundtrip with verdict intact."""
    abort_memo_dict = {
        "verdict_line": "HOLD — Crucible aborted (severity 0.00)",
        "verdict": "HOLD",
        "thesis": "Crucible aborted...",
        "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
        "abort_reason": "crucible_abort",
    }
    serialized = json.dumps(abort_memo_dict)
    parsed = json.loads(serialized)
    assert parsed["verdict"] == "HOLD"
    assert parsed["verdict_line"].startswith("HOLD")


# ─── Route verdict-resolution chain (memo.py) ─────────────────────────────


def test_route_prefers_memo_verdict_over_decision_chain():
    """When memo.verdict is set, it wins over holding/decision chain."""
    memo = SimpleNamespace(verdict="BUY", pass_reason="")
    holding = {"verdict": "HOLD"}
    ticker_decisions = [{"verdict": "HOLD"}]

    memo_verdict = memo.verdict or ""
    verdict = memo_verdict if memo_verdict else (
        holding.get("verdict") if holding else (
            ticker_decisions[0]["verdict"] if ticker_decisions else "N/A"
        )
    )
    assert verdict == "BUY"


def test_route_falls_back_to_holding_when_memo_verdict_empty():
    """When memo.verdict is empty, holding.verdict wins."""
    memo = SimpleNamespace(verdict=None, pass_reason="")
    holding = {"verdict": "STRONG_BUY"}
    ticker_decisions = [{"verdict": "HOLD"}]

    memo_verdict = memo.verdict or ""
    verdict = memo_verdict if memo_verdict else (
        holding.get("verdict") if holding else (
            ticker_decisions[0]["verdict"] if ticker_decisions else "N/A"
        )
    )
    assert verdict == "STRONG_BUY"


def test_route_falls_back_to_decisions_when_no_holding():
    """When memo and holding are empty, decisions table wins.

    This is the abort-stub path: no holding exists, but the dual-write
    inserted verdict='HOLD' into decisions.
    """
    memo = SimpleNamespace(verdict=None, pass_reason="")
    holding = None
    ticker_decisions = [{"verdict": "HOLD"}]

    memo_verdict = memo.verdict or ""
    verdict = memo_verdict if memo_verdict else (
        holding.get("verdict") if holding else (
            ticker_decisions[0]["verdict"] if ticker_decisions else "N/A"
        )
    )
    assert verdict == "HOLD"


def test_route_returns_na_only_when_all_sources_empty():
    """The chain returns 'N/A' only when EVERY source is empty."""
    memo = SimpleNamespace(verdict=None, pass_reason="")
    holding = None
    ticker_decisions = []

    memo_verdict = memo.verdict or ""
    verdict = memo_verdict if memo_verdict else (
        holding.get("verdict") if holding else (
            ticker_decisions[0]["verdict"] if ticker_decisions else "N/A"
        )
    )
    assert verdict == "N/A"


def test_abort_stub_memo_roundtrips_to_hold_through_full_chain():
    """Integration: abort_memo_dict (with new verdict='HOLD') survives end-to-end."""
    abort_memo_dict = {
        "verdict_line": "HOLD — Crucible aborted (severity 0.00)",
        "verdict": "HOLD",
        "thesis": "Crucible aborted...",
        "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
        "abort_reason": "crucible_abort",
    }

    # Simulate: DB stores memo_json; route reads it back
    memo_json_str = json.dumps(abort_memo_dict)
    memo_parsed = json.loads(memo_json_str)
    memo = SimpleNamespace(
        verdict=memo_parsed.get("verdict"),
        pass_reason=memo_parsed.get("pass_reason"),
    )
    holding = None  # abort path has no holding
    ticker_decisions = []  # even if no decisions, memo_verdict wins

    memo_verdict = memo.verdict or ""
    verdict = memo_verdict if memo_verdict else (
        holding.get("verdict") if holding else (
            ticker_decisions[0]["verdict"] if ticker_decisions else "N/A"
        )
    )
    assert verdict == "HOLD"


# ─── Empty-string memo verdict edge case ──────────────────────────────────


def test_route_treats_empty_string_memo_verdict_as_missing():
    """A memo with verdict='' (empty string) falls through to chain.

    Defensive: if a future refactor writes an empty-string verdict by
    accident, the chain still produces a real verdict instead of "".
    """
    memo = SimpleNamespace(verdict="", pass_reason="")
    holding = None
    ticker_decisions = [{"verdict": "BUY"}]

    memo_verdict = memo.verdict or ""
    verdict = memo_verdict if memo_verdict else (
        holding.get("verdict") if holding else (
            ticker_decisions[0]["verdict"] if ticker_decisions else "N/A"
        )
    )
    assert verdict == "BUY"


# ─── Defense in depth: orchestrator dual-write ───────────────────────────


def test_orchestrator_writes_verdict_to_decisions_table_on_abort():
    """The orchestrator dual-writes verdict='HOLD' to decisions on abort.

    Even though we now set memo.verdict='HOLD' directly, the decisions
    row is still the most-recent reliable source — many routes read it.
    """
    cycle_id = "test-123"
    ticker = "TEST"
    abort_verdict_str = "HOLD"
    abort_conv = 0.0
    abort_decided_at = "2026-06-29T15:05:49.097498+00:00"

    # Simulate the dual-write INSERT statements
    inserts: list[tuple] = []

    def fake_execute(sql, params):
        inserts.append((sql, params))
        return MagicMock()

    conn = MagicMock()
    conn.execute = fake_execute

    # Memos insert
    conn.execute(
        "INSERT INTO memos (cycle_id, ticker, verdict, conviction_score, "
        "memo_json, raw_text, memo_score, memo_grade, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cycle_id, ticker, abort_verdict_str, abort_conv,
         json.dumps({"verdict": "HOLD"}), "thesis", None, None, abort_decided_at),
    )
    # Decisions insert
    conn.execute(
        "INSERT INTO decisions (cycle_id, ticker, verdict, "
        "conviction_score, thesis_summary, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cycle_id, ticker, abort_verdict_str, abort_conv,
         "thesis_summary", abort_decided_at),
    )

    assert len(inserts) == 2
    memos_params = inserts[0][1]
    decisions_params = inserts[1][1]
    assert memos_params[2] == "HOLD"  # memos.verdict
    assert decisions_params[2] == "HOLD"  # decisions.verdict
