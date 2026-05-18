"""Data schemas — Evidence, EvidencePacket."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataSource(str, Enum):
    EDGAR = "edgar"
    POLYGON = "polygon"
    FINNHUB = "finnhub"
    ALPACA_DATA = "alpaca_data"
    OPENFDA = "openfda"
    FINRA = "finra"
    FORM4 = "form4"
    IR_PAGES = "ir_pages"
    PRESS = "press"
    FOMC = "fomc"
    FRED = "fred"
    ECB = "ecb"
    FUNDAMENTALS = "fundamentals"


class EvidenceType(str, Enum):
    FINANCIAL_STATEMENT = "financial_statement"
    MARKET_DATA = "market_data"
    SEC_FILING = "sec_filing"
    NEWS = "news"
    REGULATORY = "regulatory"
    INSIDER_FILING = "insider_filing"
    ECONOMIC_DATA = "economic_data"
    CORPORATE_EVENT = "corporate_event"
    EARNINGS = "earnings"
    PRESS_RELEASE = "press_release"
    ANALYST_DATA = "analyst_data"


class Evidence(BaseModel):
    """Single piece of evidence from a data source."""
    model_config = ConfigDict(frozen=True)

    id: str
    source: DataSource
    type: EvidenceType
    ticker: str
    fetched_at: datetime
    content_hash: str
    data: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    title: str | None = None
    published_at: datetime | None = None


class EvidencePacket(BaseModel):
    """Collection of evidence for a single ticker in a cycle."""
    model_config = ConfigDict(frozen=True)

    ticker: str
    cycle_id: str
    evidence: list[Evidence] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_count: int = 0
    has_stale_data: bool = False

    @property
    def by_source(self) -> dict[DataSource, list[Evidence]]:
        result: dict[DataSource, list[Evidence]] = {}
        for e in self.evidence:
            result.setdefault(e.source, []).append(e)
        return result
