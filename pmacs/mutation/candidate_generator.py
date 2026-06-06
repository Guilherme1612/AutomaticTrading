"""Rule-based mutation candidate generation (Agents.md §17.2).

Generates candidates from FDE failure clusters only after
ACTIVATION_THRESHOLD (50) PAPER cycles completed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pmacs.constants import MUTATION_ACTIVATION_CYCLES
from pmacs.schemas.mutation import MutationDimension
from pmacs.storage.audit import canonical_json


# Phases.md §6.3 Checkpoint C: excluded mutation targets
EXCLUDED_TARGETS: frozenset[str] = frozenset({
    "arbitration.formula",
    "arbitration.weights",
    "state_machine.transitions",
    "kill_switch.thresholds",
    "conviction.floor",
    "conviction.thresholds",
})

# Rule-based candidate generation (Agents.md §17.2)
GENERATION_RULES: list[dict] = [
    {
        "taxonomy": "MOAT_DRIFT_OVERESTIMATE",
        "min_count": 5,
        "window_cycles": 30,
        "dimension": MutationDimension.PERSONA_PROMPT,
        "target": "moat_analyst.system_prompt",
        "candidate_change": (
            "Add 'consider competitive entry risk with specific evidence' directive"
        ),
    },
    {
        "taxonomy": "GROWTH_STALL_MISSED",
        "min_count": 5,
        "window_cycles": 30,
        "dimension": MutationDimension.PERSONA_PROMPT,
        "target": "growth_hunter.system_prompt",
        "candidate_change": (
            "Add 'compare current growth rate to 2-quarter-ago rate; flag deceleration'"
        ),
    },
    {
        "taxonomy": "FORENSICS_FLAG_IGNORED",
        "min_count": 5,
        "window_cycles": 30,
        "dimension": MutationDimension.PERSONA_WEIGHT,
        "target": "forensics.weight",
        "candidate_change": "Increase Forensics weight by 15%",
    },
    {
        "taxonomy": "STOP_HUNTED",
        "min_count": 3,
        "window_cycles": 30,
        "dimension": MutationDimension.SIZING_FRACTION,
        "target": "stop_loss.atr_multiplier",
        "candidate_change": "Widen stop by 0.1 ATR",
    },
    {
        "taxonomy": "CATALYST_FALSE_POSITIVE",
        "min_count": 5,
        "window_cycles": 30,
        "dimension": MutationDimension.PERSONA_PROMPT,
        "target": "catalyst_summarizer.system_prompt",
        "candidate_change": (
            "Add 'require >1 corroborating source for positive catalyst resolution'"
        ),
    },
    {
        "taxonomy": "SIZING_OVERLEVERAGED",
        "min_count": 5,
        "window_cycles": 30,
        "dimension": MutationDimension.SIZING_FRACTION,
        "target": "sizing.half_kelly_multiplier",
        "candidate_change": "Reduce from 0.5 to 0.4",
    },
    # -- Additional rules from spec analysis (Agents.md §17.2) --
    {
        "taxonomy": "INSIDER_SIGNAL_FALSE",
        "min_count": 5,
        "window_cycles": 30,
        "dimension": MutationDimension.PERSONA_WEIGHT,
        "target": "insider_activity.weight",
        "candidate_change": "Decrease insider_activity weight by 10-15%",
    },
    {
        "taxonomy": "SHORT_INTEREST_CORRECT",
        "min_count": 5,
        "window_cycles": 30,
        "dimension": MutationDimension.PERSONA_WEIGHT,
        "target": "short_interest.weight",
        "candidate_change": "Increase short_interest weight by 10-15%",
    },
    {
        "taxonomy": "PERSONA_BRIER_DRIFT",
        "min_count": 3,
        "window_cycles": 50,
        "dimension": MutationDimension.PERSONA_PROMPT,
        "target": "affected_persona.system_prompt",
        "candidate_change": "Add stronger evidence-citation requirements for drifted persona",
    },
    {
        "taxonomy": "PERSONA_TICKER_AFFINITY",
        "min_count": 3,
        "window_cycles": 50,
        "dimension": MutationDimension.PERSONA_WEIGHT,
        "target": "affected_persona.ticker_affinity",
        "candidate_change": "Adjust persona weight +/-10% based on observed Brier by ticker",
    },
]


@dataclass
class MutationCandidateData:
    """Dataclass for a generated mutation candidate."""

    id: str
    dimension: str
    target: str
    trigger_taxonomy: str
    trigger_count: int
    baseline_config: str  # JSON
    candidate_config: str  # JSON
    diff_summary: str
    reversible: bool = True
    rollback_config: str = ""  # = baseline_config


def generate_candidates(
    failure_clusters: list[dict],
    paper_cycle_count: int = 0,
    config: Any | None = None,
) -> list[MutationCandidateData]:
    """Generate mutation candidates from FDE failure clusters.

    Only generates if paper_cycle_count >= activation threshold.
    Each rule matches a failure taxonomy and requires a minimum count
    within a window of recent cycles.
    """
    threshold = getattr(config, 'min_paper_cycles', MUTATION_ACTIVATION_CYCLES) if config is not None else MUTATION_ACTIVATION_CYCLES
    if paper_cycle_count < threshold:
        return []

    candidates: list[MutationCandidateData] = []
    for rule in GENERATION_RULES:
        # Phases.md §6.3 Checkpoint C: reject excluded targets
        if rule["target"] in EXCLUDED_TARGETS:
            continue

        matching = [
            f for f in failure_clusters if f.get("taxonomy") == rule["taxonomy"]
        ]
        count = sum(f.get("count", 0) for f in matching)
        if count >= rule["min_count"]:
            baseline = _read_baseline_config(rule["target"])
            candidate_id = hashlib.sha256(
                f"{rule['dimension']}:{rule['target']}:{rule['taxonomy']}".encode()
            ).hexdigest()[:16]
            candidates.append(
                MutationCandidateData(
                    id=candidate_id,
                    dimension=rule["dimension"],
                    target=rule["target"],
                    trigger_taxonomy=rule["taxonomy"],
                    trigger_count=count,
                    baseline_config=baseline,
                    candidate_config=canonical_json({"change": rule["candidate_change"]}),
                    diff_summary=rule["candidate_change"],
                    reversible=True,
                    rollback_config=baseline,
                )
            )

    return candidates


def _read_baseline_config(target: str) -> str:
    """Read the actual current production value for a mutation target.

    Falls back to a generic placeholder if the config file is unavailable.
    """
    try:
        from pathlib import Path
        config_path = Path("config/model_registry.json")
        if config_path.exists():
            import json
            with open(config_path) as f:
                registry = json.load(f)
            # Navigate dot-separated path into the registry
            parts = target.replace(".system_prompt", "").replace(".weight", "").split(".")
            current = registry
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part, {})
            return canonical_json({"current": current if current else "production"})
    except Exception:
        pass
    return canonical_json({"current": "production"})
