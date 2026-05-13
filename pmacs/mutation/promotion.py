"""Mutation promotion -- operator-gated with TOTP (Architecture.md §10, Agents.md §17.4).

ALL mutations require operator TOTP. No auto-promote.
The Mutation Engine is advisor-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pmacs.constants import MUTATION_PROBATION_CYCLES


def _resolve_verify_fn(
    verify_fn: Callable[[str], bool] | None = None,
) -> Callable[[str], bool]:
    """Resolve TOTP verification function.

    Priority:
    1. Caller-provided verify_fn (for testing or custom TOTP backends)
    2. Keychain-based: read secret from macOS Keychain and build verify closure

    The TOTP secret is never exposed outside this function's scope.
    """
    if verify_fn is not None:
        return verify_fn

    from pmacs.storage.keychain import get_api_key
    from pmacs.cortex.totp import verify_totp

    secret = get_api_key("pmacs.security", "totp_secret")
    return lambda code: verify_totp(secret, code)


def operator_promote(
    proposal_id: str,
    totp_code: str,
    *,
    verify_fn: Callable[[str], bool] | None = None,
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

    The TOTP secret is read from macOS Keychain internally.
    For testing, pass verify_fn to bypass Keychain.

    Args:
        proposal_id: The mutation proposal to promote.
        totp_code: 6-digit TOTP code from operator.
        verify_fn: Override for TOTP verification (for testing).
            If None, reads secret from Keychain and builds verify closure.
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
    # Resolve verification function (Keychain-based unless overridden)
    resolved_verify = _resolve_verify_fn(verify_fn)

    if not resolved_verify(totp_code):
        raise PermissionError("Invalid TOTP code")

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
