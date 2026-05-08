"""Mutation promotion — operator-gated with TOTP (Architecture.md §10, Agents.md §17.4).

ALL mutations require operator TOTP. No auto-promote.
The Mutation Engine is advisor-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROBATION_CYCLES = 30  # fallback; prefer config.mutation.probation_cycles


def operator_promote(
    proposal_id: str,
    totp_code: str,
    *,
    verify_fn: Callable[[str], bool] | None = None,
    totp_secret: str = "",
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
    """Operator promotes a mutation candidate. Requires TOTP.

    Args:
        proposal_id: The mutation proposal to promote.
        totp_code: 6-digit TOTP code from operator.
        verify_fn: Callback ``lambda code: bool`` — preferred over raw secret.
            The caller constructs the closure to avoid leaking the secret into
            this module's stack frame.
        totp_secret: Raw TOTP secret — DEPRECATED, use verify_fn instead.
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

    Raises:
        PermissionError: If TOTP verification fails.
    """
    # Verify TOTP — prefer callback, fall back to raw secret for backward compat
    if verify_fn is not None:
        if not verify_fn(totp_code):
            raise PermissionError("Invalid TOTP code")
    else:
        from pmacs.cortex.totp import verify_totp

        if not verify_totp(totp_secret, totp_code):
            raise PermissionError("Invalid TOTP code")

    probation = getattr(config, "probation_cycles", PROBATION_CYCLES)
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
