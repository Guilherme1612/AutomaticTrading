"""Mutation promotion -- operator-gated (Architecture.md §10, Agents.md §17.4).

ALL mutations require an explicit operator action. No auto-promote.
The Mutation Engine is advisor-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.constants import MUTATION_PROBATION_CYCLES
from pmacs.mutation.candidate_generator import EXCLUDED_TARGETS


def operator_promote(
    proposal_id: str,
    *,
    config: Any = None,
    registry_path: Path | None = None,
    db_path: Path | None = None,
    audit_path: Path | None = None,
    candidate_value: str = "",
    target: str = "",
    dimension: str = "",
    sse_publisher: Any = None,
    cycle_id: str = "",
) -> dict[str, Any]:
    """Operator promotes a mutation candidate.

    This is a single-operator, loopback-only system: calling this function IS
    the explicit operator action. There is no second-factor gate; the promotion
    is recorded in the hash-chained audit log.

    Args:
        proposal_id: The mutation proposal to promote.
        config: MutationConfig for probation_cycles.
        registry_path: Path to model_registry.json.
        db_path: Path to SQLite database.
        audit_path: Path to audit log.
        candidate_value: Candidate config to apply.
        target: Config target being mutated.
        dimension: Mutation dimension.
        sse_publisher: Optional SSE publisher.
        cycle_id: Current cycle ID (REQUIRED for audit).

    Returns:
        Promotion audit data dict.
    """
    # Phases.md §6.3 Checkpoint C: reject excluded targets
    if target and target in EXCLUDED_TARGETS:
        raise ValueError(
            f"Mutation target '{target}' is excluded per spec safety invariant "
            f"(Phases.md §6.3 Checkpoint C). Cannot mutate: {EXCLUDED_TARGETS}"
        )

    probation = getattr(config, "probation_cycles", MUTATION_PROBATION_CYCLES)
    now = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "proposal_id": proposal_id,
        "promoted_at": now,
        "promoted_by": "operator",
        "probation_cycles": probation,
    }

    # Apply to registry if paths provided
    if registry_path and db_path and audit_path:
        from pmacs.nervous.mutation import apply_candidate_to_registry

        apply_result = apply_candidate_to_registry(
            proposal_id=proposal_id,
            registry_path=registry_path,
            db_path=db_path,
            audit_path=audit_path,
            candidate_value=candidate_value,
            target=target,
            dimension=dimension,
            sse_publisher=sse_publisher,
            cycle_id=cycle_id,
        )
        result["applied"] = apply_result

    return result
