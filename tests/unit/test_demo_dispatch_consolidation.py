"""Regression tests for the SOLO/demo path's dataless-skip (Rec2-equivalent).

The demo path (pmacs/web/routes/pipeline.py::_run_single_agent) ALREADY skips the
LLM call for insider_activity / short_interest when their primary data markers are
absent from the fundamentals string — via the ``_DATA_DEPENDENT_AGENTS`` check that
returns a neutral INSUFFICIENT_DATA dict BEFORE ``_call_openrouter``. This is the
ORIGINAL mechanism the orchestrator's Rec2 (commit d1350ae) mirrored. These tests
guard that pre-existing behavior so a refactor doesn't silently regress it.

Rec1 (macro DP cache across tickers) is intentionally NOT ported to the demo path:
the demo ``macro_regime`` consumes ticker-specific news + fundamentals (not
cycle-level FRED/FOMC evidence like the orchestrator), so caching across tickers
would apply ticker A's macro analysis to ticker B — a correctness hazard. The real
fix for the demo path's limitations is unification (route SOLO through the
orchestrator), not porting Rec1.

spec_ref: Architecture.md §12.2, Agents.md §9/§10.
"""
import json

import pytest

import pmacs.web.routes.pipeline as P


@pytest.fixture
def stub_llm(monkeypatch):
    """Neutralize the LLM and prompt loader; count LLM calls."""
    # Empty system prompt → skips the episodic-context DB-read block entirely.
    monkeypatch.setattr(P, "_load_persona_prompt", lambda persona: "")
    calls: list[int] = []

    def _fake_call(prompt, max_tokens=5000, temperature=0.01, system_prompt=None):
        calls.append(1)
        return json.dumps({
            "p_up": 0.40, "p_flat": 0.30, "p_down": 0.30,
            "analysis": "mock analysis", "key_signal": "MOCK_SIGNAL",
            "confidence": 0.60, "evidence_cited": ["mock-data"],
        })

    monkeypatch.setattr(P, "_call_openrouter", _fake_call)
    return calls


def test_demo_insider_dataless_skips_llm(stub_llm):
    """No form4/insider markers in fundamentals → INSUFFICIENT_DATA, no LLM call."""
    r = P._run_single_agent("insider_activity", "OUST", 45.0, [], "")
    assert r["key_signal"] == "INSUFFICIENT_DATA"
    assert r["confidence"] == 0.0
    assert stub_llm == [], "dataless insider must NOT call the LLM"


def test_demo_short_dataless_skips_llm(stub_llm):
    """No finra/short_interest markers → INSUFFICIENT_DATA, no LLM call."""
    r = P._run_single_agent("short_interest", "OUST", 45.0, [], "some generic fundamentals")
    assert r["key_signal"] == "INSUFFICIENT_DATA"
    assert r["confidence"] == 0.0
    assert stub_llm == []


def test_demo_insider_with_data_calls_llm(stub_llm):
    """form4 marker present → the LLM IS called (pre-check does not fire)."""
    r = P._run_single_agent(
        "insider_activity", "OUST", 45.0, [],
        "form4_transaction: CEO purchased 1000 shares at $44.10",
    )
    assert len(stub_llm) == 1, "insider with data must call the LLM exactly once"
    assert r["persona"] == "insider_activity"
    assert r["key_signal"] == "MOCK_SIGNAL"


def test_demo_short_with_data_calls_llm(stub_llm):
    """finra marker present → the LLM IS called."""
    r = P._run_single_agent(
        "short_interest", "OUST", 45.0, [],
        "finra_short_interest: 9.56% of float, days_to_cover=4.2",
    )
    assert len(stub_llm) == 1
    assert r["persona"] == "short_interest"


def test_demo_non_data_persona_always_calls_llm(stub_llm):
    """Non-data-dependent personas (e.g. growth_hunter) call the LLM regardless."""
    r = P._run_single_agent("growth_hunter", "OUST", 45.0, [], "revenue growth 48.9%")
    assert len(stub_llm) == 1
    assert r["persona"] == "growth_hunter"
