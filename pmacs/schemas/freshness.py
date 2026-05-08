"""Freshness schemas — staleness checking results (Architecture.md §16.4)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    DEGRADED = "DEGRADED"


class CriticalityLevel(str, Enum):
    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    NICE_TO_HAVE = "NICE_TO_HAVE"


class StalenessBudget(BaseModel):
    """Configuration for how stale data can be per source."""
    model_config = ConfigDict(frozen=True)

    source: str
    criticality: CriticalityLevel
    max_age_seconds: int
    abort_on_stale: bool = False  # True for CRITICAL sources


class FreshnessResult(BaseModel):
    """Result of staleness check. Does NOT mutate the EvidencePacket (§16.4)."""
    model_config = ConfigDict(frozen=True)

    source: str
    status: FreshnessStatus
    criticality: CriticalityLevel
    age_seconds: int
    max_age_seconds: int
    message: str = ""

    @property
    def should_abort(self) -> bool:
        return self.status == FreshnessStatus.STALE and self.criticality == CriticalityLevel.CRITICAL

    @property
    def should_degrade(self) -> bool:
        return (
            self.status == FreshnessStatus.STALE
            and self.criticality == CriticalityLevel.IMPORTANT
        )
