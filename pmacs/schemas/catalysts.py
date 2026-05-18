"""Catalyst schemas — the 7 catalyst types (Architecture.md §7.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CatalystType(str, Enum):
    EARNINGS_RELEASE = "EARNINGS_RELEASE"
    FDA_DECISION = "FDA_DECISION"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    REGULATORY_RULING = "REGULATORY_RULING"
    MA_CLOSE = "MA_CLOSE"
    PARTNERSHIP_ANNOUNCEMENT = "PARTNERSHIP_ANNOUNCEMENT"
    GUIDANCE_UPDATE = "GUIDANCE_UPDATE"


class CatalystStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    RESOLVED_POSITIVE = "RESOLVED_POSITIVE"
    RESOLVED_NEGATIVE = "RESOLVED_NEGATIVE"
    RESOLVED_NEUTRAL = "RESOLVED_NEUTRAL"
    CANCELLED = "CANCELLED"
    DELAYED = "DELAYED"


class Catalyst(BaseModel):
    """A catalyst event for a ticker."""
    model_config = ConfigDict(frozen=True)

    id: str
    ticker: str
    type: CatalystType
    status: CatalystStatus = CatalystStatus.PENDING
    expected_date: date | None = None
    actual_date: date | None = None
    description: str = ""
    source_urls: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
