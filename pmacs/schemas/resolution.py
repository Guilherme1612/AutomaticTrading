"""Resolution result schemas (Architecture.md §7)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pmacs.schemas.catalysts import CatalystStatus, CatalystType


class ResolutionResult(BaseModel):
    """Outcome of catalyst resolution detection."""

    model_config = ConfigDict(frozen=True)

    catalyst_id: str
    ticker: str
    catalyst_type: CatalystType
    old_status: CatalystStatus
    new_status: CatalystStatus
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    corroboration_tier: str  # "TIER_A" / "TIER_B" / "TIER_C" / "TIMEOUT"
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    price_change_pct: float | None = None
    summary: str = ""
    price_consistent: bool | None = None
    data: dict[str, Any] = Field(default_factory=dict)
