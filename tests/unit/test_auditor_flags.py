"""Unit tests for the cross-persona auditor narrative cascade (Agents.md §11d).

Verifies the Jun 29 /agents-page audit fix: the auditor's
`_render_peer_outputs` cascade must surface the per-persona narrative
field for EVERY wave-1 persona, not just the 2 names hard-coded in the
old cascade (growth_durability_reasoning, signal_reasoning).

Before this fix, the cascade missed `regime_reasoning`, `net_catalyst_outlook`,
`competitive_entry_reasoning`, `anomaly_reasoning`, and the nested-list
narratives of moat_analyst + forensics — so the auditor saw empty
reasoning for 5 of 7 personas, fired CONCLUSION_UNSUPPORTED (sev 0.8)
on every cycle, and forced Crucible to abort with severity 0.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pmacs.agents.cross_persona_auditor import (
    _PERSONA_NARRATIVE_FIELDS,
    _UNIVERSAL_NARRATIVE_FIELDS,
    _aggregate_nested_narrative,
    _render_peer_outputs,
)
from pmacs.schemas.agents import PersonaName


# ─── Helpers ────────────────────────────────────────────────────────────────


def _persona_output(persona: str, body: dict) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking a DirectionalProbability with raw_output."""
    return SimpleNamespace(
        persona=SimpleNamespace(value=persona),
        raw_output=json.dumps(body) if body else "",
        p_up=body.get("p_up"),
        p_down=body.get("p_down"),
        p_flat=body.get("p_flat"),
    )


# ─── Cascade field map coverage ─────────────────────────────────────────────


def test_cascade_map_covers_all_seven_wave1_personas():
    """Every wave-1 persona name must appear in the cascade map."""
    expected = {
        PersonaName.MACRO_REGIME.value,
        PersonaName.CATALYST_SUMMARIZER.value,
        PersonaName.MOAT_ANALYST.value,
        PersonaName.GROWTH_HUNTER.value,
        PersonaName.INSIDER_ACTIVITY.value,
        PersonaName.SHORT_INTEREST.value,
        PersonaName.FORENSICS.value,
    }
    assert set(_PERSONA_NARRATIVE_FIELDS.keys()) == expected


def test_cascade_map_includes_universal_fallback():
    """Universal fallback tuple must include the historic hard-coded names."""
    for name in ("key_signal", "analysis", "reasoning"):
        assert name in _UNIVERSAL_NARRATIVE_FIELDS


# ─── Persona-specific cascade tests ────────────────────────────────────────


def test_cascade_macro_regime():
    out = _persona_output(
        "macro_regime",
        {
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "regime_reasoning": "Yield curve inverts, signaling late-cycle",
        },
    )
    rendered = _render_peer_outputs([out])
    assert "Yield curve inverts" in rendered
    assert "reasoning:" in rendered


def test_cascade_macro_regime_falls_back_to_sector_rotation():
    """When regime_reasoning is empty, sector_rotation_summary is used."""
    out = _persona_output(
        "macro_regime",
        {
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "regime_reasoning": "",
            "sector_rotation_summary": "Tech into Defensives",
        },
    )
    rendered = _render_peer_outputs([out])
    assert "Tech into Defensives" in rendered


def test_cascade_catalyst_summarizer():
    out = _persona_output(
        "catalyst_summarizer",
        {
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "net_catalyst_outlook": "FDA decision Q2 catalyst upside",
            "catalysts": [
                {"description": "FDA decision Q2", "thesis_impact": "POSITIVE"},
                {"description": "Earnings Q3", "thesis_impact": "NEUTRAL"},
            ],
        },
    )
    rendered = _render_peer_outputs([out])
    assert "FDA decision Q2 catalyst upside" in rendered
    # Aggregated nested descriptions also surface
    assert "FDA decision Q2" in rendered


def test_cascade_moat_analyst():
    out = _persona_output(
        "moat_analyst",
        {
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "competitive_entry_reasoning": "High switching cost; entrenched accounts",
            "moat_components": [
                {"moat_type": "SWITCHING_COSTS", "reasoning": "Contracts 3-yr"},
                {"moat_type": "NETWORK_EFFECTS", "reasoning": "Platform GMV growing"},
            ],
        },
    )
    rendered = _render_peer_outputs([out])
    assert "High switching cost" in rendered
    # Cascade picks the first non-empty field; nested-list is the
    # second-priority fallback (used when competitive_entry_reasoning is empty)
    assert "Contracts 3-yr" not in rendered


def test_cascade_moat_analyst_falls_back_to_aggregated_reasoning():
    """When competitive_entry_reasoning is empty, moat_components_aggregated_reasoning wins."""
    out = _persona_output(
        "moat_analyst",
        {
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "competitive_entry_reasoning": "",
            "moat_components": [
                {"moat_type": "SWITCHING_COSTS", "reasoning": "Contracts 3-yr"},
                {"moat_type": "NETWORK_EFFECTS", "reasoning": "Platform GMV growing"},
            ],
        },
    )
    rendered = _render_peer_outputs([out])
    # Aggregated nested reasoning surfaces as the cascade fallback
    assert "Contracts 3-yr" in rendered
    assert "Platform GMV growing" in rendered


def test_cascade_growth_hunter():
    out = _persona_output(
        "growth_hunter",
        {
            "p_up": 0.6, "p_flat": 0.3, "p_down": 0.1,
            "evidence_ids": ["ev1"],
            "growth_durability_reasoning": "TAM expanding 30% YoY",
        },
    )
    rendered = _render_peer_outputs([out])
    assert "TAM expanding 30% YoY" in rendered


def test_cascade_insider_activity():
    out = _persona_output(
        "insider_activity",
        {
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "signal_reasoning": "CEO bought $5M, cluster buy pattern",
        },
    )
    rendered = _render_peer_outputs([out])
    assert "CEO bought $5M" in rendered


def test_cascade_short_interest():
    out = _persona_output(
        "short_interest",
        {
            "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
            "evidence_ids": ["ev1"],
            "anomaly_reasoning": "Short interest stable at 18% of float",
        },
    )
    rendered = _render_peer_outputs([out])
    assert "Short interest stable" in rendered


def test_cascade_forensics():
    out = _persona_output(
        "forensics",
        {
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "overall_accounting_quality": "CLEAN",
            "red_flags": [
                {"description": "Revenue recognition timing"},
                {"description": "Goodwill impairment Q3"},
            ],
        },
    )
    rendered = _render_peer_outputs([out])
    # Aggregated red flag descriptions
    assert "Revenue recognition timing" in rendered
    assert "Goodwill impairment Q3" in rendered


# ─── Fallthrough behavior ───────────────────────────────────────────────────


def test_cascade_falls_through_to_key_signal():
    """When persona-specific fields are empty, key_signal is used."""
    out = _persona_output(
        "macro_regime",
        {
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["ev1"],
            "regime_reasoning": "",
            "sector_rotation_summary": "",
            "key_signal": "Macro is bullish, key signal wins",
        },
    )
    rendered = _render_peer_outputs([out])
    assert "key signal wins" in rendered


def test_cascade_empty_when_no_narrative():
    """When no narrative field is populated, the reasoning line is absent."""
    out = _persona_output(
        "macro_regime",
        {
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["ev1"],
        },
    )
    rendered = _render_peer_outputs([out])
    # Probabilities still render, but no reasoning line
    assert "p_up=0.400" in rendered
    assert "reasoning:" not in rendered


def test_cascade_unknown_persona_uses_universal_fallback():
    """A wave-2 persona (e.g. gatekeeper) outside the map uses the universal cascade."""
    out = _persona_output(
        "gatekeeper",
        {
            "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
            "evidence_ids": ["ev1"],
            "key_signal": "Gate passes after risk check",
        },
    )
    rendered = _render_peer_outputs([out])
    assert "Gate passes after risk check" in rendered


# ─── Full render integration ────────────────────────────────────────────────


def test_full_render_includes_all_persona_names_and_probs():
    """All 7 wave-1 personas render in a single call with their probabilities."""
    outputs = [
        _persona_output("macro_regime", {"p_up": 0.4, "p_flat": 0.4, "p_down": 0.2, "evidence_ids": ["ev"], "regime_reasoning": "Late cycle"}),
        _persona_output("catalyst_summarizer", {"p_up": 0.5, "p_flat": 0.3, "p_down": 0.2, "evidence_ids": ["ev"], "net_catalyst_outlook": "FDA catalyst"}),
        _persona_output("moat_analyst", {"p_up": 0.6, "p_flat": 0.2, "p_down": 0.2, "evidence_ids": ["ev"], "competitive_entry_reasoning": "Strong moat"}),
        _persona_output("growth_hunter", {"p_up": 0.5, "p_flat": 0.3, "p_down": 0.2, "evidence_ids": ["ev"], "growth_durability_reasoning": "TAM expanding"}),
        _persona_output("insider_activity", {"p_up": 0.4, "p_flat": 0.3, "p_down": 0.3, "evidence_ids": ["ev"], "signal_reasoning": "CEO buy"}),
        _persona_output("short_interest", {"p_up": 0.3, "p_flat": 0.4, "p_down": 0.3, "evidence_ids": ["ev"], "anomaly_reasoning": "Stable short"}),
        _persona_output("forensics", {"p_up": 0.4, "p_flat": 0.4, "p_down": 0.2, "evidence_ids": ["ev"], "overall_accounting_quality": "CLEAN"}),
    ]
    rendered = _render_peer_outputs(outputs)
    for persona in (
        "macro_regime", "catalyst_summarizer", "moat_analyst", "growth_hunter",
        "insider_activity", "short_interest", "forensics",
    ):
        assert f"### {persona}" in rendered
    # Spot-check at least one reasoning line from each
    assert "Late cycle" in rendered
    assert "FDA catalyst" in rendered
    assert "Strong moat" in rendered
    assert "TAM expanding" in rendered
    assert "CEO buy" in rendered
    assert "Stable short" in rendered
    assert "CLEAN" in rendered  # forensics uses overall_accounting_quality as fallback


def test_render_empty_input_returns_empty_string():
    """Empty persona_outputs returns empty string (not error)."""
    assert _render_peer_outputs([]) == ""
    assert _render_peer_outputs(None) == ""


# ─── Aggregator unit tests ──────────────────────────────────────────────────


def test_aggregate_catalyst_summarizer_nested_descriptions():
    out = _aggregate_nested_narrative({
        "catalysts": [
            {"description": "FDA Q2"},
            {"description": "Earnings Q3"},
            {"description": ""},  # empty entries skipped
            {"foo": "bar"},  # no description key, skipped
        ],
    })
    assert "FDA Q2" in out["catalysts_aggregated_description"]
    assert "Earnings Q3" in out["catalysts_aggregated_description"]
    assert " | " in out["catalysts_aggregated_description"]


def test_aggregate_moat_analyst_nested_reasoning():
    out = _aggregate_nested_narrative({
        "moat_components": [
            {"reasoning": "Switching costs"},
            {"reasoning": "Network effects"},
        ],
    })
    assert "Switching costs" in out["moat_components_aggregated_reasoning"]
    assert "Network effects" in out["moat_components_aggregated_reasoning"]


def test_aggregate_forensics_nested_red_flags():
    out = _aggregate_nested_narrative({
        "red_flags": [
            {"description": "Revenue timing"},
            {"description": "Goodwill impairment"},
        ],
    })
    assert "Revenue timing" in out["red_flags_aggregated_description"]
    assert "Goodwill impairment" in out["red_flags_aggregated_description"]


def test_aggregate_returns_empty_strings_for_missing_nested_lists():
    """Missing or non-list nested fields yield no aggregated keys."""
    out = _aggregate_nested_narrative({})
    assert out == {}
    out2 = _aggregate_nested_narrative({"catalysts": None, "moat_components": None, "red_flags": None})
    assert out2 == {}
