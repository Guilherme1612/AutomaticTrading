"""Shared helper for rendering wave-1 persona outputs as agent-readable text.

The cross-persona auditor, bull advocate, and bear advocate all consume
`DirectionalProbability` outputs from the wave-1 personas. Each persona
writes its narrative to a different field on its Pydantic schema:

  - macro_regime         → ``regime_reasoning`` (fallback: sector_rotation_summary)
  - catalyst_summarizer  → ``net_catalyst_outlook`` (fallback: catalysts[].description)
  - moat_analyst         → ``competitive_entry_reasoning`` (fallback: moat_components[].reasoning)
  - growth_hunter        → ``growth_durability_reasoning`` (fallback: key_risk_to_growth)
  - insider_activity     → ``signal_reasoning``
  - short_interest       → ``anomaly_reasoning``
  - forensics            → ``red_flags[].description`` (fallback: overall_accounting_quality)

Older code only walked a hardcoded subset (``reasoning → key_signal →
analysis → growth_durability_reasoning → signal_reasoning``), which
surfaced the narrative for only 2 of 7 personas — the auditor then
flagged CONCLUSION_UNSUPPORTED on every cycle and forced Crucible to
abort. ``_PERSONA_NARRATIVE_FIELDS`` below maps each persona to its
actual field cascade so all three renderers get the same view.

Helpers exported here:
  - ``_PERSONA_NARRATIVE_FIELDS`` / ``_UNIVERSAL_NARRATIVE_FIELDS``: cascade maps
  - ``_aggregate_nested_narrative(body)``: synthesizes flat ``_aggregated_*``
    keys from nested-list fields (catalysts, moat_components, red_flags)
  - ``extract_narrative(name, body)``: returns the first non-empty narrative
    string for a persona, walking the cascade
"""

from __future__ import annotations

from typing import Any

from pmacs.schemas.agents import PersonaName


# ─── Field maps ─────────────────────────────────────────────────────────────

_PERSONA_NARRATIVE_FIELDS: dict[str, tuple[str, ...]] = {
    PersonaName.MACRO_REGIME.value: (
        "regime_reasoning",
        "sector_rotation_summary",
        "key_signal",
        "analysis",
        "reasoning",
    ),
    PersonaName.CATALYST_SUMMARIZER.value: (
        "net_catalyst_outlook",
        "catalysts_aggregated_description",
        "key_signal",
        "analysis",
        "reasoning",
    ),
    PersonaName.MOAT_ANALYST.value: (
        "competitive_entry_reasoning",
        "moat_components_aggregated_reasoning",
        "key_signal",
        "analysis",
        "reasoning",
    ),
    PersonaName.GROWTH_HUNTER.value: (
        "growth_durability_reasoning",
        "key_risk_to_growth",
        "key_signal",
        "analysis",
        "reasoning",
    ),
    PersonaName.INSIDER_ACTIVITY.value: (
        "signal_reasoning",
        "key_signal",
        "analysis",
        "reasoning",
    ),
    PersonaName.SHORT_INTEREST.value: (
        "anomaly_reasoning",
        "key_signal",
        "analysis",
        "reasoning",
    ),
    PersonaName.FORENSICS.value: (
        "red_flags_aggregated_description",
        "overall_accounting_quality",
        "key_signal",
        "analysis",
        "reasoning",
    ),
}

# Universal fallback for personas NOT in the map (e.g. wave-2 personas,
# edge cases). Walks the same candidate names in the historical order.
_UNIVERSAL_NARRATIVE_FIELDS: tuple[str, ...] = (
    "key_signal",
    "analysis",
    "reasoning",
)


def _aggregate_nested_narrative(body: dict[str, Any]) -> dict[str, str]:
    """Synthesize flat ``_aggregated_*`` keys from nested-list fields.

    Some personas (catalyst_summarizer, moat_analyst, forensics) keep their
    narrative text inside per-item fields. The cascade is name-based, so
    we collapse each list to a single string here. Empty/missing lists
    yield empty strings — the cascade then falls through to the next candidate.
    """
    out: dict[str, str] = {}
    cats = body.get("catalysts")
    if isinstance(cats, list) and cats:
        descs = [
            str(c.get("description", "")).strip()
            for c in cats
            if isinstance(c, dict) and str(c.get("description", "")).strip()
        ]
        out["catalysts_aggregated_description"] = " | ".join(descs)
    moat = body.get("moat_components")
    if isinstance(moat, list) and moat:
        rs = [
            str(m.get("reasoning", "")).strip()
            for m in moat
            if isinstance(m, dict) and str(m.get("reasoning", "")).strip()
        ]
        out["moat_components_aggregated_reasoning"] = " | ".join(rs)
    flags = body.get("red_flags")
    if isinstance(flags, list) and flags:
        descs = [
            str(f.get("description", "")).strip()
            for f in flags
            if isinstance(f, dict) and str(f.get("description", "")).strip()
        ]
        out["red_flags_aggregated_description"] = " | ".join(descs)
    return out


def extract_narrative(name: str, body: dict[str, Any]) -> str:
    """Walk the persona-specific cascade to find a non-empty narrative string.

    Merges nested-list aggregations into ``body`` first so the cascade
    reads them uniformly with the per-persona narrative field names.
    The first non-empty candidate wins; returns ``""`` when none match.
    """
    body = {**body, **_aggregate_nested_narrative(body)}
    fields = _PERSONA_NARRATIVE_FIELDS.get(name, _UNIVERSAL_NARRATIVE_FIELDS)
    for field in fields:
        candidate = body.get(field)
        if candidate:
            return str(candidate)
    return ""
