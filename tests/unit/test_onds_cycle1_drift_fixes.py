"""Regression tests for ONDS Cycle 1 (Jun 30 2026) schema-drift fixes.

Cycle 1 SOLO-ONDS-20260630T092420 surfaced 4 distinct drift classes in
the deepseek-v4-flash output on openrouter. Each caused Pydantic
ValidationError and (without the fix) the persona would have aborted at
attempt 3, leaving the cycle in safe-default fallback:

  1. forensics: ``red_flags[].category = "GUIDANCE_CREDIBILITY"``
     (not in the 8-value RedFlag literal enum)
  2. forensics: ``overall_accounting_quality = "POOR"``
     (not in the 5-value ForensicsOutput literal enum)
  3. insider_activity: ``transactions = 0`` (int, schema requires list)
  4. macro_regime: missing ``vix_regime`` field entirely
  5. catalyst_summarizer: missing ``catalysts[].catalyst_type`` and
     ``catalysts[].description`` (both required Literal/str)

Each test pre-validates a representative LLM output dict and asserts
that the persona's ``_pre_validate`` (and downstream ``model_validate``)
succeed. Pydantic-validate-through is the bar: drift fix is only
"real" if Pydantic accepts the post-fix dict.
"""
from __future__ import annotations

import pytest

from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Forensics: GUIDANCE_CREDIBILITY + POOR
# ---------------------------------------------------------------------------


def test_forensics_guidance_credibility_coerced_to_earnings_quality():
    """red_flags[].category = GUIDANCE_CREDIBILITY (not in 8-value enum)
    must coerce to EARNINGS_QUALITY so Pydantic accepts the entry."""
    from pmacs.agents.forensics import ForensicsRunner
    from pmacs.schemas.personas import ForensicsOutput

    parsed = {
        "ticker": "ONDS",
        "overall_accounting_quality": "MINOR_CONCERNS",
        "red_flag_count": 1,
        "red_flags": [
            {
                "category": "GUIDANCE_CREDIBILITY",  # invalid; not in enum
                "severity": 0.55,
                "description": "Management issued soft guidance",
                "evidence_ids": ["E001"],
            },
        ],
        "p_up": 0.40,
        "p_flat": 0.35,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }

    runner = ForensicsRunner(cycle_id="test-onds-drift-1")
    fixed = runner._pre_validate(dict(parsed))

    # The category must now be a valid enum value
    assert fixed["red_flags"][0]["category"] == "EARNINGS_QUALITY"

    # And Pydantic must accept the result end-to-end
    out = ForensicsOutput.model_validate(fixed)
    assert out.red_flags[0].category == "EARNINGS_QUALITY"


def test_forensics_poor_quality_coerced_to_material_concerns():
    """overall_accounting_quality = "POOR" (not in 5-value enum) must
    coerce to MATERIAL_CONCERNS (with SEVERE_RISK escalation if severity
    warrants)."""
    from pmacs.agents.forensics import ForensicsRunner
    from pmacs.schemas.personas import ForensicsOutput

    # Case 1: POOR with low-severity flags → MATERIAL_CONCERNS
    parsed = {
        "ticker": "ONDS",
        "overall_accounting_quality": "POOR",
        "red_flag_count": 1,
        "red_flags": [
            {
                "category": "EARNINGS_QUALITY",
                "severity": 0.30,
                "description": "Mild accounting quality issue",
                "evidence_ids": ["E001"],
            },
        ],
        "p_up": 0.40,
        "p_flat": 0.35,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }
    runner = ForensicsRunner(cycle_id="test-onds-drift-2a")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["overall_accounting_quality"] == "MATERIAL_CONCERNS"
    out = ForensicsOutput.model_validate(fixed)
    assert out.overall_accounting_quality == "MATERIAL_CONCERNS"

    # Case 2: POOR with high-severity flags → SEVERE_RISK
    parsed["red_flags"][0]["severity"] = 0.80
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["overall_accounting_quality"] == "SEVERE_RISK"
    out = ForensicsOutput.model_validate(fixed)
    assert out.overall_accounting_quality == "SEVERE_RISK"


def test_forensics_poor_with_trailing_reasoning_text_stripped():
    """Cycle 1 ONDS Jun 30 surfaced the LLM emitting
    ``"POOR — Aggressive accounting..."`` (concatenated reasoning
    onto the enum literal). The fix must strip the trailing text and
    coerce to a clean enum value."""
    from pmacs.agents.forensics import ForensicsRunner
    from pmacs.schemas.personas import ForensicsOutput

    parsed = {
        "ticker": "ONDS",
        "overall_accounting_quality": "POOR — Aggressive accounting patterns suggesting capitalization of expenses or one-time items.",
        "red_flag_count": 1,
        "red_flags": [
            {
                "category": "EARNINGS_QUALITY",
                "severity": 0.30,
                "description": "Mild accounting quality issue",
                "evidence_ids": ["E001"],
            },
        ],
        "p_up": 0.40,
        "p_flat": 0.35,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }

    runner = ForensicsRunner(cycle_id="test-onds-drift-2c")
    fixed = runner._pre_validate(dict(parsed))
    # Must be a clean canonical enum, not the LLM's concatenated string
    assert fixed["overall_accounting_quality"] == "MATERIAL_CONCERNS"
    out = ForensicsOutput.model_validate(fixed)
    assert out.overall_accounting_quality == "MATERIAL_CONCERNS"


def test_forensics_low_quality_with_trailing_text_coerced_to_severe_risk():
    """Cycle 3 ONDS Jun 30 surfaced the LLM emitting
    ``"LOW — severe accrual distortions inflate earnings"`` —
    ``LOW`` is not in the 5-value enum, and the trailing ``severe``
    text means the LLM is reporting a serious accounting concern.
    Must coerce to ``SEVERE_RISK`` (or ``MATERIAL_CONCERNS`` if max
    red_flag severity is below 0.7)."""
    from pmacs.agents.forensics import ForensicsRunner
    from pmacs.schemas.personas import ForensicsOutput

    # Case 1: LOW with low-severity flags → MATERIAL_CONCERNS
    parsed = {
        "ticker": "ONDS",
        "overall_accounting_quality": "LOW — soft accruals but not catastrophic",
        "red_flag_count": 1,
        "red_flags": [
            {
                "category": "EARNINGS_QUALITY",
                "severity": 0.30,
                "description": "Mild concern",
                "evidence_ids": ["E001"],
            },
        ],
        "p_up": 0.40,
        "p_flat": 0.35,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }
    runner = ForensicsRunner(cycle_id="test-onds-drift-2d")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["overall_accounting_quality"] == "MATERIAL_CONCERNS"
    out = ForensicsOutput.model_validate(fixed)
    assert out.overall_accounting_quality == "MATERIAL_CONCERNS"

    # Case 2: LOW with high-severity flags → SEVERE_RISK
    parsed["overall_accounting_quality"] = "LOW — severe accrual distortions inflate earnings"
    parsed["red_flags"][0]["severity"] = 0.85
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["overall_accounting_quality"] == "SEVERE_RISK"
    out = ForensicsOutput.model_validate(fixed)
    assert out.overall_accounting_quality == "SEVERE_RISK"


# ---------------------------------------------------------------------------
# InsiderActivity: transactions: <int> → []
# ---------------------------------------------------------------------------


def test_insider_activity_transactions_int_coerced_to_empty_list():
    """transactions = 0 (int) must coerce to [] (empty list)."""
    from pmacs.agents.insider_activity import InsiderActivityRunner
    from pmacs.schemas.personas import InsiderActivityOutput

    parsed = {
        "ticker": "ONDS",
        "transactions": 0,  # invalid: schema requires list[InsiderTransaction]
        "signal": "NO_SIGNAL",
        "signal_reasoning": "No Form 4 filings in the window",
        "p_up": 0.40,
        "p_flat": 0.40,
        "p_down": 0.20,
        "evidence_ids": ["E001"],
    }

    runner = InsiderActivityRunner(cycle_id="test-onds-drift-3")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["transactions"] == []

    out = InsiderActivityOutput.model_validate(fixed)
    assert out.transactions == []
    assert out.signal == "NO_SIGNAL"


def test_insider_activity_transactions_none_coerced_to_empty_list():
    """transactions = None must coerce to []."""
    from pmacs.agents.insider_activity import InsiderActivityRunner
    from pmacs.schemas.personas import InsiderActivityOutput

    parsed = {
        "ticker": "ONDS",
        "transactions": None,
        "signal": "INSUFFICIENT_DATA",
        "signal_reasoning": "Form 4 data unavailable",
        "p_up": 0.35,
        "p_flat": 0.35,
        "p_down": 0.30,
        "evidence_ids": ["E001"],
    }

    runner = InsiderActivityRunner(cycle_id="test-onds-drift-3b")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["transactions"] == []
    out = InsiderActivityOutput.model_validate(fixed)
    assert out.transactions == []


# ---------------------------------------------------------------------------
# MacroRegime: missing vix_regime
# ---------------------------------------------------------------------------


def test_macro_regime_missing_vix_regime_injected_as_moderate():
    """Missing vix_regime field must be injected as 'MODERATE' (middle
    of LOW/MODERATE/ELEVATED/CRISIS, the honest I-don't-know)."""
    from pmacs.agents.macro_regime import MacroRegimeRunner
    from pmacs.schemas.personas import MacroRegimeOutput

    parsed = {
        "ticker": "ONDS",
        "regime": "LATE_CYCLE",
        "regime_confidence": 0.65,
        "regime_reasoning": "Late-cycle dynamics with tightening liquidity",
        "yield_curve_signal": "INVERTED",
        # vix_regime deliberately omitted
        "sector_rotation_summary": "Rotation out of growth into value",
        "p_up": 0.35,
        "p_flat": 0.40,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }

    runner = MacroRegimeRunner(cycle_id="test-onds-drift-4")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["vix_regime"] == "MODERATE"

    out = MacroRegimeOutput.model_validate(fixed)
    assert out.vix_regime == "MODERATE"


def test_macro_regime_vix_regime_none_injected_as_moderate():
    """vix_regime = None must inject MODERATE."""
    from pmacs.agents.macro_regime import MacroRegimeRunner
    from pmacs.schemas.personas import MacroRegimeOutput

    parsed = {
        "ticker": "ONDS",
        "regime": "LATE_CYCLE",
        "regime_confidence": 0.65,
        "regime_reasoning": "Late-cycle dynamics",
        "yield_curve_signal": "INVERTED",
        "vix_regime": None,
        "sector_rotation_summary": "Rotation",
        "p_up": 0.35,
        "p_flat": 0.40,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }

    runner = MacroRegimeRunner(cycle_id="test-onds-drift-4b")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["vix_regime"] == "MODERATE"
    out = MacroRegimeOutput.model_validate(fixed)
    assert out.vix_regime == "MODERATE"


# ---------------------------------------------------------------------------
# CatalystSummarizer: missing catalyst_type / description
# ---------------------------------------------------------------------------


def test_catalyst_summarizer_missing_catalyst_type_injected_as_earnings():
    """catalysts[].catalyst_type missing must be injected as 'earnings'
    (most-common class)."""
    from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
    from pmacs.schemas.personas import CatalystSummarizerOutput

    parsed = {
        "ticker": "ONDS",
        "catalysts": [
            {
                # catalyst_type deliberately omitted
                "description": "Q3 earnings expected Oct 30",
                "expected_date": "2026-10-30",
                "status": "PENDING",
                "thesis_impact": "NEUTRAL",
                "evidence_ids": ["E001"],
            },
        ],
        "net_catalyst_outlook": "Earnings near-term; momentum improving",
        "p_up": 0.45,
        "p_flat": 0.35,
        "p_down": 0.20,
        "evidence_ids": ["E001"],
    }

    runner = CatalystSummarizerRunner(cycle_id="test-onds-drift-5")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["catalysts"][0]["catalyst_type"] == "earnings"
    out = CatalystSummarizerOutput.model_validate(fixed)
    assert out.catalysts[0].catalyst_type == "earnings"


def test_catalyst_summarizer_missing_description_injected():
    """catalysts[].description missing must be synthesized as a
    placeholder referencing the catalyst_type."""
    from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
    from pmacs.schemas.personas import CatalystSummarizerOutput

    parsed = {
        "ticker": "ONDS",
        "catalysts": [
            {
                "catalyst_type": "fda_decision",
                # description deliberately omitted
                "expected_date": "2026-12-15",
                "status": "PENDING",
                "thesis_impact": "POSITIVE",
                "evidence_ids": ["E001"],
            },
        ],
        "net_catalyst_outlook": "FDA decision pending; positive bias",
        "p_up": 0.50,
        "p_flat": 0.30,
        "p_down": 0.20,
        "evidence_ids": ["E001"],
    }

    runner = CatalystSummarizerRunner(cycle_id="test-onds-drift-5b")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["catalysts"][0]["description"]  # truthy, non-empty
    assert "fda_decision" in fixed["catalysts"][0]["description"]

    out = CatalystSummarizerOutput.model_validate(fixed)
    assert "fda_decision" in out.catalysts[0].description


def test_catalyst_summarizer_missing_both_type_and_description():
    """catalysts[] entry missing BOTH catalyst_type and description
    must inject both so the entry is still parseable."""
    from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
    from pmacs.schemas.personas import CatalystSummarizerOutput

    parsed = {
        "ticker": "ONDS",
        "catalysts": [
            {
                "expected_date": "2026-12-15",
                "status": "PENDING",
                "thesis_impact": "NEUTRAL",
                "evidence_ids": ["E001"],
            },
        ],
        "net_catalyst_outlook": "Multiple catalysts pending",
        "p_up": 0.40,
        "p_flat": 0.35,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }

    runner = CatalystSummarizerRunner(cycle_id="test-onds-drift-5c")
    fixed = runner._pre_validate(dict(parsed))
    assert fixed["catalysts"][0]["catalyst_type"] == "earnings"
    assert fixed["catalysts"][0]["description"]

    out = CatalystSummarizerOutput.model_validate(fixed)
    assert out.catalysts[0].catalyst_type == "earnings"
    assert out.catalysts[0].description


# ---------------------------------------------------------------------------
# MoatAnalyst: TEAM_EXPERTISE (not in 6-value enum)
# ---------------------------------------------------------------------------


def test_moat_analyst_team_expertise_coerced_to_network_effects():
    """moat_type = TEAM_EXPERTISE (not in 6-value Literal enum) must
    coerce to the first enum member (NETWORK_EFFECTS) so Pydantic accepts.

    Cycle 1 ONDS Jun 30 surfaced Pydantic literal_error on the third
    moat_components entry. Without normalization the persona aborts.
    """
    from pmacs.agents.moat_analyst import MoatAnalystRunner
    from pmacs.schemas.personas import MoatAnalystOutput

    parsed = {
        "ticker": "ONDS",
        "moat_components": [
            {
                "type": "SWITCHING_COSTS",  # LLM uses 'type' (natural lang)
                "strength": 0.35,
                "trajectory": "STABLE",
                "reasoning": "Net revenue retention 112%",
                "evidence_ids": ["E001"],
            },
            {
                "moat_type": "TEAM_EXPERTISE",  # not in enum; should default
                "strength": 0.40,
                "trajectory": "STABLE",
                "reasoning": "Engineering team depth",
                "evidence_ids": ["E001"],
            },
        ],
        "moat_strength": 0.40,
        "competitive_entry_risk": "MODERATE",
        "competitive_entry_reasoning": "Crowded market but not commoditized",
        "p_up": 0.40,
        "p_flat": 0.35,
        "p_down": 0.25,
        "evidence_ids": ["E001"],
    }

    runner = MoatAnalystRunner(cycle_id="test-onds-drift-6")
    fixed = runner._pre_validate(dict(parsed))

    # Both renames and enum normalizations applied
    assert fixed["moat_components"][0]["moat_type"] == "SWITCHING_COSTS"
    assert fixed["moat_components"][1]["moat_type"] == "NETWORK_EFFECTS"

    # And Pydantic must accept
    out = MoatAnalystOutput.model_validate(fixed)
    assert out.moat_components[0].moat_type == "SWITCHING_COSTS"
    assert out.moat_components[1].moat_type == "NETWORK_EFFECTS"
