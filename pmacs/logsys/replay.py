"""Audit log replay module (Architecture.md §5).

Parse audit log (tab-separated JSON lines), filter by event_type / cycle_id /
time range, verify hash chain integrity, return parsed event payloads.

Audit log format per line:
    <iso_ts>\\t<prev_sha256>\\t<event_type>\\t<canonical_json>\\t<this_sha256>

Spec: Architecture.md §5.1 (audit chain), §1.5 (hash-chained format).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pmacs.constants import AUDIT_GENESIS_PREV_SHA


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuditEvent:
    """Parsed audit log entry."""

    line_number: int
    iso_ts: str
    prev_sha: str
    event_type: str
    payload: dict
    this_sha: str

    @property
    def timestamp(self) -> datetime:
        """Parse ISO timestamp to datetime."""
        return datetime.fromisoformat(self.iso_ts)

    @property
    def cycle_id(self) -> str | None:
        """Extract cycle_id from payload if present."""
        return self.payload.get("cycle_id")


@dataclass
class ReplayResult:
    """Result of an audit replay operation."""

    events: list[AuditEvent] = field(default_factory=list)
    chain_valid: bool = True
    chain_error: str | None = None
    total_lines: int = 0
    filtered_count: int = 0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_line(line: str, line_number: int) -> AuditEvent | None:
    """Parse a single tab-separated audit line into an AuditEvent.

    Returns None for blank lines or malformed entries.
    """
    line = line.strip()
    if not line:
        return None

    parts = line.split("\t")
    if len(parts) != 5:
        return None

    iso_ts, prev_sha, event_type, canon_json, this_sha = parts

    try:
        payload = json.loads(canon_json)
    except json.JSONDecodeError:
        payload = {"_raw": canon_json}

    return AuditEvent(
        line_number=line_number,
        iso_ts=iso_ts,
        prev_sha=prev_sha,
        event_type=event_type,
        payload=payload,
        this_sha=this_sha,
    )


def _compute_hash(iso_ts: str, prev_sha: str, event_type: str, canon: str) -> str:
    """Compute the expected SHA256 for an audit entry.

    Hash: sha256(iso_ts || prev_sha || event_type || canonical_json)
    with null-byte separators (Architecture.md §5.1).
    """
    hasher = hashlib.sha256()
    hasher.update(iso_ts.encode("utf-8") + b"\x00")
    hasher.update(prev_sha.encode("utf-8") + b"\x00")
    hasher.update(event_type.encode("utf-8") + b"\x00")
    hasher.update(canon.encode("utf-8"))
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Iterators
# ---------------------------------------------------------------------------

def iter_events(path: str | Path) -> Iterator[AuditEvent]:
    """Yield parsed AuditEvents from an audit log file.

    Skips blank and malformed lines silently.
    """
    path = Path(path)
    if not path.exists():
        return

    with open(path) as f:
        for line_number, line in enumerate(f, start=1):
            event = _parse_line(line, line_number)
            if event is not None:
                yield event


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _matches(
    event: AuditEvent,
    event_type: str | None = None,
    cycle_id: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
) -> bool:
    """Check if an event matches all provided filters."""
    if event_type is not None and event.event_type != event_type:
        return False
    if cycle_id is not None and event.cycle_id != cycle_id:
        return False
    if after is not None:
        try:
            if event.timestamp < after:
                return False
        except (ValueError, OSError):
            return False
    if before is not None:
        try:
            if event.timestamp >= before:
                return False
        except (ValueError, OSError):
            return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def replay(
    path: str | Path,
    event_type: str | None = None,
    cycle_id: str | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    verify_chain: bool = True,
) -> ReplayResult:
    """Replay audit log with optional filters and chain verification.

    Args:
        path: Path to the audit log file.
        event_type: Filter to this canonical event type.
        cycle_id: Filter to this cycle_id (from payload).
        after: Only events at or after this datetime.
        before: Only events before this datetime.
        verify_chain: Whether to verify hash chain integrity.

    Returns:
        ReplayResult with filtered events and chain status.
    """
    result = ReplayResult()
    path = Path(path)

    if not path.exists():
        result.chain_valid = True
        result.chain_error = None
        return result

    prev_sha: str = AUDIT_GENESIS_PREV_SHA
    total_lines = 0

    with open(path) as f:
        for line in f:
            total_lines += 1
            raw_line = line.strip()
            if not raw_line:
                continue

            parts = raw_line.split("\t")
            if len(parts) != 5:
                continue

            iso_ts, stored_prev, event_type_raw, canon_json, stored_sha = parts

            # Chain verification
            if verify_chain:
                if stored_prev != prev_sha:
                    result.chain_valid = False
                    result.chain_error = (
                        f"Line {total_lines}: chain broken "
                        f"(expected prev={prev_sha[:16]}..., got={stored_prev[:16]}...)"
                    )
                    return result

                computed = _compute_hash(iso_ts, prev_sha, event_type_raw, canon_json)
                if computed != stored_sha:
                    result.chain_valid = False
                    result.chain_error = (
                        f"Line {total_lines}: hash mismatch (tampered)"
                    )
                    return result

            prev_sha = stored_sha

            # Parse and filter
            event = _parse_line(line, total_lines)
            if event is None:
                continue

            if _matches(event, event_type=event_type, cycle_id=cycle_id,
                        after=after, before=before):
                result.events.append(event)

    result.total_lines = total_lines
    result.filtered_count = len(result.events)
    return result


def replay_cycle(
    path: str | Path,
    cycle_id: str,
    verify_chain: bool = True,
) -> ReplayResult:
    """Replay all events for a specific cycle_id.

    Convenience wrapper around replay() filtering by cycle_id.

    Args:
        path: Path to the audit log file.
        cycle_id: The cycle to replay.
        verify_chain: Whether to verify hash chain integrity.

    Returns:
        ReplayResult with events for the specified cycle.
    """
    return replay(path, cycle_id=cycle_id, verify_chain=verify_chain)


def replay_events_by_type(
    path: str | Path,
    event_type: str,
    verify_chain: bool = True,
) -> ReplayResult:
    """Replay all events of a given type.

    Convenience wrapper around replay() filtering by event_type.

    Args:
        path: Path to the audit log file.
        event_type: Canonical event type to filter for.
        verify_chain: Whether to verify hash chain integrity.

    Returns:
        ReplayResult with matching events.
    """
    return replay(path, event_type=event_type, verify_chain=verify_chain)


def verify_chain(path: str | Path) -> tuple[bool, str | None]:
    """Verify the full hash chain integrity of an audit log.

    Returns (is_valid, error_message).
    """
    path = Path(path)
    if not path.exists():
        return True, None

    prev_sha: str = AUDIT_GENESIS_PREV_SHA
    line_num = 0

    with open(path) as f:
        for line in f:
            line_num += 1
            raw = line.strip()
            if not raw:
                continue

            parts = raw.split("\t")
            if len(parts) != 5:
                return False, f"Line {line_num}: expected 5 fields, got {len(parts)}"

            iso_ts, stored_prev, event_type_raw, canon_json, stored_sha = parts

            if stored_prev != prev_sha:
                return False, f"Line {line_num}: chain broken"

            computed = _compute_hash(iso_ts, prev_sha, event_type_raw, canon_json)
            if computed != stored_sha:
                return False, f"Line {line_num}: hash mismatch (tampered)"

            prev_sha = stored_sha

    return True, None
