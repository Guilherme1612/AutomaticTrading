"""Staleness checker — returns FreshnessResult, never mutates packets (§16.4)."""

from __future__ import annotations

from datetime import datetime, timezone

from pmacs.schemas.data import EvidencePacket
from pmacs.schemas.freshness import (
    CriticalityLevel,
    FreshnessResult,
    FreshnessStatus,
)


def check_freshness(
    packet: EvidencePacket,
    source: str,
    criticality: CriticalityLevel,
    max_age_seconds: int,
) -> FreshnessResult:
    """Check freshness of an evidence packet.

    Returns a FreshnessResult. Does NOT mutate the packet (Architecture.md §16.4).

    Args:
        packet: The evidence packet to check.
        source: Source name.
        criticality: Criticality level (CRITICAL/IMPORTANT/NICE_TO_HAVE).
        max_age_seconds: Maximum acceptable age in seconds.

    Returns:
        FreshnessResult with status and metadata.
    """
    now = datetime.now(timezone.utc)
    age = int((now - packet.fetched_at).total_seconds())

    if age <= max_age_seconds:
        status = FreshnessStatus.FRESH
        message = f"Data fresh (age={age}s, max={max_age_seconds}s)"
    else:
        status = FreshnessStatus.STALE
        message = f"Data stale (age={age}s, max={max_age_seconds}s)"

    return FreshnessResult(
        source=source,
        status=status,
        criticality=criticality,
        age_seconds=age,
        max_age_seconds=max_age_seconds,
        message=message,
    )


def check_all_freshness(
    packet: EvidencePacket,
    budgets: dict[str, tuple[CriticalityLevel, int]],
) -> list[FreshnessResult]:
    """Check freshness of all sources referenced in an evidence packet.

    Args:
        packet: The evidence packet.
        budgets: Dict of source_name -> (criticality, max_age_seconds).

    Returns:
        List of FreshnessResult, one per source.
    """
    results = []
    sources_seen = set()

    for evidence in packet.evidence:
        source = evidence.source.value
        if source in budgets and source not in sources_seen:
            criticality, max_age = budgets[source]
            results.append(check_freshness(packet, source, criticality, max_age))
            sources_seen.add(source)

    return results
