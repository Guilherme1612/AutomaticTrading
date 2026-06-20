"""Reverse-DCF valuation schema (Architecture.md §9.4b).

Pure-Python, LLM-free. The ReverseDcfEngine solves the growth rate the market is
implying from the current price and compares it to the GrowthHunter's estimated
growth. This is the deterministic bull/bear valuation anchor — it does NOT enter
Arbitration and does NOT amend the conviction formula.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReverseDcfResult(BaseModel):
    """Result of a reverse-DCF valuation.

    spec_ref: Architecture.md §9.4b, Source.md §16.9
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    cycle_id: str = ""
    # Growth the market is pricing into the current price (solved from price + FCF).
    implied_growth_pct: float | None = None
    # Growth the analysis layer estimates (GrowthHunter.revenue_yoy_pct or yfinance).
    assumed_growth_pct: float | None = None
    # assumed - implied. Positive => market under-pricing growth (bullish lean);
    # negative => market over-pricing growth (bearish lean).
    growth_gap_pct: float | None = None
    # Fair value at the assumed growth rate (what the price *would* be if the
    # estimated growth were correct). None when primitives are missing.
    fair_value_usd: float | None = None
    current_price_usd: float | None = None
    valuation_lean: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
    # Sensitivity: fair value at a few growth assumptions around the estimate,
    # so the operator can see how thin the margin is. {growth_pct: fair_value_usd}.
    sensitivity: dict[str, float] = Field(default_factory=dict)
    # Why a field is None (missing primitive, non-positive FCF, etc.). Never
    # fabricated — the engine prefers None + a note over a wrong number.
    notes: str = ""

    @property
    def is_available(self) -> bool:
        """True when the reverse-DCF produced real numbers (not a NEUTRAL fallback)."""
        return self.implied_growth_pct is not None and self.fair_value_usd is not None