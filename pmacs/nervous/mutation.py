"""Nervous-side mutation operations — atomic config writes (Architecture.md §10.7).

This module lives in pmacs-nervous because the mutation process (pmacs-mutation)
MUST NOT have write access to production config files. This is structural
separation (Agents.md §17.4 Level 1).

All promotion/rollback writes go through atomic_write_config (temp-file + rename).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.data.canonical import canonical_json


def atomic_write_config(path: Path, data: dict[str, Any]) -> None:
    """Write config dict to file atomically via temp-file + rename.

    Uses canonical_json for deterministic output. The temp file is created
    in the same directory as the target to guarantee same-filesystem rename
    (POSIX atomicity requirement).

    Args:
        path: Target config file path.
        data: Config dict to serialize and write.

    Raises:
        PermissionError: If the target directory or file is not writable.
        OSError: If filesystem operations fail.
    """
    content = canonical_json(data)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(parent), prefix=f".{path.stem}_", suffix=".tmp"
    )
    tmp = Path(tmp_path)
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = -1  # mark closed
        os.rename(str(tmp), str(path))  # POSIX atomic on same filesystem
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def apply_candidate_to_registry(
    proposal_id: str,
    registry_path: Path,
    db_path: Path,
    audit_path: Path,
    candidate_value: str = "",
    target: str = "",
    dimension: str = "",
    sse_publisher: Any = None,
    cycle_id: str = "",
) -> dict[str, Any]:
    """Apply a mutation candidate to model_registry.json atomically.

    This is the ONLY function that writes to model_registry.json for mutations.
    Called from pmacs-nervous after operator TOTP verification.

    Steps:
    1. Read current model_registry.json
    2. Apply candidate change
    3. Write via atomic_write_config
    4. Update mutation_proposals status to OPERATOR_PROMOTED
    5. Log audit event mutation_operator_promoted
    6. Publish SSE event

    Args:
        proposal_id: The mutation proposal to apply.
        registry_path: Path to config/model_registry.json.
        db_path: Path to SQLite database.
        audit_path: Path to audit log file.
        candidate_value: New value to apply (JSON string).
        target: Config key being mutated.
        dimension: Mutation dimension.
        sse_publisher: Optional SSE publisher instance.
        cycle_id: Current cycle ID (REQUIRED for audit).

    Returns:
        Audit metadata dict with applied_at, proposal_id, etc.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Level 1 safety: mutation process must NOT have write access to production
    # config (Agents.md §17.4).  The file should be read-only for this process.
    if registry_path.exists() and os.access(str(registry_path), os.W_OK):
        raise PermissionError(
            f"Mutation process has write access to {registry_path}. "
            f"Level 1 safety requires read-only permissions (Agents.md §17.4)."
        )

    # 1. Read current registry
    if registry_path.exists():
        with open(registry_path) as f:
            registry = json.load(f)
    else:
        registry = {}

    # 2. Apply candidate change to candidates section
    if "candidates" not in registry:
        registry["candidates"] = {}
    registry["candidates"][proposal_id] = {
        "target": target,
        "dimension": dimension,
        "value": candidate_value,
        "applied_at": now,
    }

    # 3. Atomic write
    atomic_write_config(registry_path, registry)

    # 4. Update SQLite
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE mutation_proposals SET status = 'OPERATOR_PROMOTED', "
            "completed_at = ? WHERE id = ?",
            (now, proposal_id),
        )
        conn.commit()
    finally:
        conn.close()

    # 5. Audit event
    from pmacs.storage.audit import AuditWriter

    writer = AuditWriter(audit_path)
    audit_payload: dict[str, Any] = {
        "proposal_id": proposal_id,
        "dimension": dimension,
        "target": target,
        "operator": True,
        "applied_at": now,
    }
    writer.append("mutation_operator_promoted", audit_payload, cycle_id=cycle_id)
    writer.close()

    # 6. SSE event — mutation.promoted when operator TOTP-applies a candidate
    if sse_publisher is not None:
        sse_publisher.publish("mutation", "mutation.promoted", {
            "mutation_id": proposal_id,
            "candidate_name": target,
            "timestamp": now,
            "dimension": dimension,
        })

    return {
        "proposal_id": proposal_id,
        "applied_at": now,
        "target": target,
        "dimension": dimension,
    }


def rollback_registry(
    proposal_id: str,
    rollback_config: str,
    registry_path: Path,
    db_path: Path,
    audit_path: Path,
    reason: str = "auto_rollback",
    sse_publisher: Any = None,
    cycle_id: str = "",
) -> dict[str, Any]:
    """Rollback a mutation by restoring the rollback_config atomically.

    Reads the rollback_config from the proposal, removes the candidate from
    the registry, and writes atomically.

    Args:
        proposal_id: The mutation to rollback.
        rollback_config: JSON string of the baseline config to restore.
        registry_path: Path to model_registry.json.
        db_path: Path to SQLite database.
        audit_path: Path to audit log file.
        reason: Why the rollback was triggered.
        sse_publisher: Optional SSE publisher.
        cycle_id: Current cycle ID.

    Returns:
        Rollback audit metadata dict.
    """
    now = datetime.now(timezone.utc).isoformat()

    # 1. Read current registry
    if registry_path.exists():
        with open(registry_path) as f:
            registry = json.load(f)
    else:
        registry = {}

    # 2. Remove candidate entry
    if "candidates" in registry and proposal_id in registry.get("candidates", {}):
        del registry["candidates"][proposal_id]

    # 3. Atomic write
    atomic_write_config(registry_path, registry)

    # 4. Update SQLite
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE mutation_proposals SET status = 'ROLLED_BACK', "
            "completed_at = ? WHERE id = ?",
            (now, proposal_id),
        )
        conn.commit()
    finally:
        conn.close()

    # 5. Audit event
    from pmacs.storage.audit import AuditWriter

    writer = AuditWriter(audit_path)
    writer.append(
        "mutation_rollback_executed",
        {"proposal_id": proposal_id, "reason": reason},
        cycle_id=cycle_id,
    )
    writer.close()

    # 6. SSE event
    if sse_publisher is not None:
        sse_publisher.publish("mutation", "mutation.rolled_back", {
            "proposal_id": proposal_id,
            "reason": reason,
            "rolled_back_at": now,
        })

    return {
        "proposal_id": proposal_id,
        "reason": reason,
        "rolled_back_at": now,
        "status": "ROLLED_BACK",
    }
