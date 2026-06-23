"""Forward-valuation schema (Architecture.md §9.4b).

Pure-Python, LLM-free. The ForwardValuationEngine consumes the ValuationAgent's
structured bull/base/bear scenario assumptions (revenue growth path to a 6-12
month horizon, EBITDA margin at horizon, exit EV/EBITDA multiple, acquisition
revenue contribution) and computes a per-scenario forward fair-value price.

This is the deterministic scenario-price ASSUMPTION consumer — it does NOT enter
Arbitration and does NOT amend the conviction formula (Five Non-Negotiable #2).
Feeds ScenarioPriceEngine (preferred when ``is_available``) and MemoWriter.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ForwardScenarioPoint(BaseModel):
    """Per-scenario forward fair-value computation chain (one of bull/base/bear).

    Each stage is None when a primitive for that scenario was missing — the engine
    degrades scenario-by-scenario rather than all-or-nothing.
    """

    model_config = ConfigDict(frozen=True)

    scenario: Literal["bull", "base", "bear"]
    # Echo of the agent's assumption inputs (fractions, e.g. 0.18 = 18%).
    revenue_growth_path_pct: float | None = None
    ebitda_margin_at_horizon_pct: float | None = None
    exit_multiple: float | None = None
    acquisition_revenue_contribution_pct: float | None = None
    # Computed chain (USD). None when the scenario could not be valued.
    forward_revenue_usd: float | None = None
    forward_ebitda_usd: float | None = None
    forward_ev_usd: float | None = None
    equity_value_usd: float | None = None
    price_usd: float | None = None
    notes: str = ""


class ForwardValuationResult(BaseModel):
    """Result of the forward-valuation engine.

    spec_ref: Architecture.md §9.4b, Source.md §16.9
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    cycle_id: str = ""
    horizon_months: int = 12
    # Per-scenario forward fair-value prices (the consumer-facing fields).
    bull_price: float | None = None
    base_price: float | None = None
    bear_price: float | None = None
    # Probability-weighted expected price across the agent's scenario
    # probability_of_occurrence (NOT the Arbitrated p_up/p_flat/p_down — this
    # agent does not enter Arbitration). None when any scenario price is None.
    expected_price_usd: float | None = None
    # Echo of each scenario's computation chain (audit transparency for the memo).
    scenario_points: dict[str, ForwardScenarioPoint] = Field(default_factory=dict)
    # Engine-level market primitives used (so the memo can show the chain).
    current_price_usd: float | None = None
    shares_outstanding: float | None = None
    net_debt_usd: float | None = None
    current_revenue_ttm_usd: float | None = None
    # Why a field is None (missing primitive, non-positive margin, etc.). Never
    # fabricated — the engine prefers None + a note over a wrong number.
    notes: str = ""

    @property
    def is_available(self) -> bool:
        """True when at least the base-case price was computed (not a fallback)."""
        return self.base_price is not None and self.base_price > 0
