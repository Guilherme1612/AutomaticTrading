"""Currency schemas — FX rates with usd_per_eur convention (Architecture.md §16.8).

IMPORTANT: Always use `usd_per_eur`, NEVER `eur_per_usd`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FxRate(BaseModel):
    """Single FX rate snapshot. Convention: usd_per_eur (ECB standard)."""
    model_config = ConfigDict(frozen=True)

    pair: Literal["EURUSD"] = "EURUSD"
    usd_per_eur: float = Field(gt=0.5, lt=2.0, description="USD per 1 EUR (ECB convention)")
    business_date: date  # ECB publication date (CET-based)
    fetched_at: datetime  # UTC timestamp
    source: Literal["ECB"] = "ECB"

    @model_validator(mode="after")
    def _no_eur_per_usd_field(self) -> "FxRate":
        # Guard: ensure no one adds eur_per_usd as a declared field (property is OK)
        if "eur_per_usd" in self.__class__.model_fields:
            raise ValueError("Use usd_per_eur convention, not eur_per_usd (Architecture.md §16.8)")
        # Belt-and-suspenders: also check model_dump() keys for any dynamic construction
        if "eur_per_usd" in self.model_dump():
            raise ValueError("Use usd_per_eur convention, not eur_per_usd (Architecture.md §16.8)")
        return self

    @property
    def eur_per_usd(self) -> float:
        """Derived property: how many EUR per 1 USD."""
        return 1.0 / self.usd_per_eur


class FxSnapshot(BaseModel):
    """FX snapshot stored per cycle."""
    model_config = ConfigDict(frozen=True)

    cycle_id: str
    rate: FxRate


def usd_to_eur(amount_usd: float, rate: FxRate) -> float:
    """Convert USD to EUR using ECB usd_per_eur rate."""
    return amount_usd / rate.usd_per_eur


def eur_to_usd(amount_eur: float, rate: FxRate) -> float:
    """Convert EUR to USD using ECB usd_per_eur rate."""
    return amount_eur * rate.usd_per_eur
