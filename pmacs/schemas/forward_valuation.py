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
    # EV/Sales multiple used for the pre-profit path (when EBITDA <= 0).
    exit_sales_multiple: float | None = None
    # Which valuation path priced this scenario ("ev_ebitda" | "ev_sales").
    valuation_path: str | None = None
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
    # Observable valuation anchor + Wall-Street reference, populated by the
    # orchestrator from the same evidence the ValuationAgent saw. The memo surfaces
    # these so the operator can see the gap between (a) the multiple the market is
    # paying today, (b) the multiple the agent assumed at the horizon, and (c) the
    # analyst price-target consensus — the non-obvious reconciliation most memos
    # omit. None when the primitive was unavailable (never fabricated).
    current_ev_sales: float | None = None
    analyst_target_mean_usd: float | None = None
    # True when the equity floor kicked in (forward EV < net debt → equity = $0).
    # Carries the distress signal in-band instead of silently degrading
    # `is_available` to False and dropping the result into the reverse-DCF fallback.
    base_price_underwater: bool = False
    # Why a field is None (missing primitive, non-positive margin, etc.). Never
    # fabricated — the engine prefers None + a note over a wrong number.
    notes: str = ""
    # ── Tier 3 — gap / distress / convergence signals (Commit 3) ──────────
    # All three default to safe no-ops so existing call sites are unaffected.
    # When forward_vs_reverse_dcf_gap_pct is set and |gap| > 0.50, the engine
    # raises forward_vs_reverse_dcf_warning ("LLM hallucination check"); the
    # memo renders it as a ⚠ WARNING line. base_price_underwater=True is
    # surfaced into the memo via agent_scenario_convergence_warning prefixed
    # with the ⚠ DISTRESS tag. Probability convergence (|p_bull - p_bear| <
    # 0.10) appends a LOW-CONFIDENCE FORWARD VALUATION tag to the same field.
    forward_vs_reverse_dcf_gap_pct: float | None = None
    forward_vs_reverse_dcf_warning: str = ""
    agent_scenario_convergence_warning: str = ""

    @property
    def is_available(self) -> bool:
        """True when at least the base-case price was computed.

        A floored-at-$0 base price counts as available (the distress signal is
        real, the math ran, the equity was floored) — distinguish from a
        scenario that never produced a price. Consumers that want to react to
        underwater distress should check ``base_price_underwater`` instead.
        """
        return self.base_price is not None
