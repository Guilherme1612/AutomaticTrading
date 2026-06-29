"""Tests for prior-memo extraction and reinjection (Architecture.md §16.9, Tier 2A).

The orchestrator's _step_13c_episodic_context previously pulled only 7 fields
from the prior memo and truncated prior_key_signal to 200 chars. The full
thesis, fair_value, methodology, key_evidence, key_risks, what_would_change_my_mind,
and forward_valuation.expected_price_usd were never reinjected — so on cycle 2+
the LLM re-derived facts already in the persisted memo. These tests verify the
new extract_prior_memo_summary() helper and the build_context_brief kwargs.

spec_ref: Architecture.md §16.9; Agents.md §13.5
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# extract_prior_memo_summary tests
# ---------------------------------------------------------------------------

def test_extract_returns_empty_dict_on_empty_input():
    """Empty/None input returns {} without raising."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    assert extract_prior_memo_summary("") == {}
    assert extract_prior_memo_summary(None) == {}


def test_extract_returns_empty_dict_on_malformed_json():
    """Truncated or invalid JSON returns {} (no exception)."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    assert extract_prior_memo_summary("{not valid json") == {}
    assert extract_prior_memo_summary("[1, 2, 3]") == {}  # non-dict → {} (ignored)


def test_extract_parses_full_prior_memo():
    """A complete prior memo returns all rich fields."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    memo = {
        "thesis": "Strong moat + accelerating growth.",
        "verdict_line": "BUY — conviction 0.42",
        "fair_value_estimate": "$28.50",
        "valuation_methodology": "EV/EBITDA at 14x on FY26 EBITDA",
        "key_evidence": ["ev1", "ev2", "ev3"],
        "key_risks": ["FX risk", "concentration risk"],
        "what_would_change_my_mind": ["margin compression"],
        "forward_valuation": {"expected_price_usd": 32.10},
        "crucible_severity": 0.35,
        "conviction": 0.42,
        "decided_at": "2026-06-28T10:00:00Z",
    }
    out = extract_prior_memo_summary(json.dumps(memo))
    assert out["thesis"] == "Strong moat + accelerating growth."
    assert out["verdict_line"] == "BUY — conviction 0.42"
    assert out["fair_value"] == "$28.50"
    assert out["valuation_methodology"] == "EV/EBITDA at 14x on FY26 EBITDA"
    assert out["key_evidence"] == ["ev1", "ev2", "ev3"]
    assert out["key_risks"] == ["FX risk", "concentration risk"]
    assert out["what_would_change_my_mind"] == ["margin compression"]
    assert out["forward_expected_price_usd"] == 32.10
    assert out["crucible_severity"] == 0.35
    assert out["conviction"] == 0.42
    assert out["decided_at"] == "2026-06-28T10:00:00Z"


def test_extract_handles_missing_optional_fields():
    """Missing fields default to None / empty list — no KeyError."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    out = extract_prior_memo_summary(json.dumps({"thesis": "ok"}))
    assert out["thesis"] == "ok"
    assert out["verdict_line"] is None
    assert out["fair_value"] is None
    assert out["key_evidence"] == []
    assert out["key_risks"] == []
    assert out["what_would_change_my_mind"] == []
    assert out["forward_expected_price_usd"] is None


def test_extract_supports_fair_value_alias():
    """Some memos use 'fair_value' instead of 'fair_value_estimate' — both work."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    out_a = extract_prior_memo_summary(json.dumps({"fair_value_estimate": "$10"}))
    out_b = extract_prior_memo_summary(json.dumps({"fair_value": "$20"}))
    assert out_a["fair_value"] == "$10"
    assert out_b["fair_value"] == "$20"


def test_extract_caps_list_lengths_to_8():
    """Long lists are truncated to 8 entries to bound prompt size."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    long_list = [f"item_{i}" for i in range(50)]
    out = extract_prior_memo_summary(
        json.dumps({"key_evidence": long_list})
    )
    assert len(out["key_evidence"]) == 8


def test_extract_handles_non_numeric_forward_price():
    """forward_valuation.expected_price_usd must be numeric; else None."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    out = extract_prior_memo_summary(
        json.dumps({"forward_valuation": {"expected_price_usd": "not_a_number"}})
    )
    assert out["forward_expected_price_usd"] is None


def test_extract_handles_string_list_field():
    """Some memos store what_would_change_my_mind as a single string."""
    from pmacs.agents.sanity.memo_scorer import extract_prior_memo_summary
    out = extract_prior_memo_summary(
        json.dumps({"what_would_change_my_mind": "guidance cut"})
    )
    assert out["what_would_change_my_mind"] == ["guidance cut"]


# ---------------------------------------------------------------------------
# build_context_brief prior-memo kwargs tests
# ---------------------------------------------------------------------------

def test_build_context_brief_emits_prior_memo_block_on_cycle_2():
    """When prior_thesis + prior_fair_value are supplied, the brief includes
    a [PRIOR MEMO CONTEXT] section.
    """
    from pmacs.agents.episodic_context import build_context_brief

    brief = build_context_brief(
        persona="all",
        ticker="OUST",
        ticker_analysis_count=1,
        prior_thesis="Strong moat + accelerating growth.",
        prior_fair_value="$28.50",
        prior_valuation_methodology="EV/EBITDA at 14x on FY26 EBITDA",
        prior_key_evidence=["ev1", "ev2"],
        prior_key_risks=["FX risk"],
        prior_what_changed=["margin compression"],
        prior_forward_expected_price_usd=32.10,
        prior_decided_at="2026-06-28",
    )
    assert "PRIOR MEMO CONTEXT" in brief
    assert "Strong moat + accelerating growth." in brief
    assert "$28.50" in brief
    assert "EV/EBITDA at 14x" in brief
    assert "FX risk" in brief
    assert "$32.10" in brief


def test_build_context_brief_omits_prior_memo_block_on_first_cycle():
    """On cycle 1 (ticker_analysis_count = 0), no PRIOR MEMO CONTEXT is emitted
    even if kwargs are supplied (defensive guard).
    """
    from pmacs.agents.episodic_context import build_context_brief

    brief = build_context_brief(
        persona="all",
        ticker="OUST",
        ticker_analysis_count=0,
        prior_thesis="Should be ignored",
    )
    assert "PRIOR MEMO CONTEXT" not in brief


def test_build_context_brief_omits_prior_memo_block_when_all_kwargs_none():
    """When all prior-memo kwargs are None, the block is omitted (no-op)."""
    from pmacs.agents.episodic_context import build_context_brief

    brief = build_context_brief(
        persona="all",
        ticker="OUST",
        ticker_analysis_count=2,
    )
    assert "PRIOR MEMO CONTEXT" not in brief


def test_build_context_brief_word_limit_respected():
    """With all 7 prior-memo kwargs populated, brief stays within word limit."""
    from pmacs.agents.episodic_context import build_context_brief

    brief = build_context_brief(
        persona="all",
        ticker="OUST",
        ticker_analysis_count=1,
        prior_thesis="A" * 1000,  # forces truncation
        prior_fair_value="B" * 200,
        prior_valuation_methodology="C" * 800,
        prior_key_evidence=["D" * 200 for _ in range(5)],
        prior_key_risks=["E" * 200 for _ in range(5)],
        prior_what_changed=["F" * 200 for _ in range(3)],
        prior_forward_expected_price_usd=99.99,
    )
    word_count = len(brief.split())
    # Repeat-cycle limit is 700 words per the existing 400/700 contract.
    assert word_count <= 750, f"Brief too long: {word_count} words"


# ---------------------------------------------------------------------------
# format_persona_weight_table tests (also Commit 4 prep, kept here for shared)
# ---------------------------------------------------------------------------

def test_format_persona_weight_table_handles_empty_input():
    """Empty/None weights → empty string (callers can short-circuit)."""
    from pmacs.agents.sanity.memo_scorer import format_persona_weight_table
    assert format_persona_weight_table(None) == ""
    assert format_persona_weight_table([]) == ""


def test_format_persona_weight_table_sorts_by_weight_desc():
    """Rows render in descending weight order."""
    from pmacs.agents.sanity.memo_scorer import format_persona_weight_table

    @dataclass_helper
    class _W:
        persona: str
        weight: float
        brier_score: float = 0.20
        calibration_count: int = 50
        weight_multiplier: float = 1.0

    weights = [
        _W("moat_analyst", 0.30),
        _W("growth_hunter", 0.50),
        _W("macro_regime", 0.20),
    ]
    out = format_persona_weight_table(weights)
    # growth_hunter (50%) should appear before moat_analyst (30%) before macro_regime (20%).
    pos_gh = out.find("growth_hunter")
    pos_ma = out.find("moat_analyst")
    pos_mr = out.find("macro_regime")
    assert pos_gh < pos_ma < pos_mr


def test_format_persona_weight_table_marks_low_confidence_personas():
    """When per-persona DuckDB brier > 0.25, the row gets a LOW CONFIDENCE tag."""
    from pmacs.agents.sanity.memo_scorer import format_persona_weight_table

    @dataclass_helper
    class _W:
        persona: str
        weight: float
        brier_score: float = 0.20
        calibration_count: int = 50
        weight_multiplier: float = 1.0

    weights = [_W("macro_regime", 0.30)]
    out = format_persona_weight_table(
        weights,
        per_persona_calibration={"macro_regime": 0.30},  # > 0.25 → LOW CONFIDENCE
    )
    assert "LOW CONFIDENCE" in out
    assert "macro_regime" in out


def test_format_persona_weight_table_no_low_confidence_when_under_threshold():
    """When per-persona brier <= 0.25, no LOW CONFIDENCE tag."""
    from pmacs.agents.sanity.memo_scorer import format_persona_weight_table

    @dataclass_helper
    class _W:
        persona: str
        weight: float
        brier_score: float = 0.20
        calibration_count: int = 50
        weight_multiplier: float = 1.0

    weights = [_W("moat_analyst", 0.30)]
    out = format_persona_weight_table(
        weights,
        per_persona_calibration={"moat_analyst": 0.15},
    )
    assert "LOW CONFIDENCE" not in out


# Tiny dataclass helper since @dataclass requires the import
from dataclasses import dataclass as _dc  # noqa: E402


def dataclass_helper(cls):
    return _dc(cls)