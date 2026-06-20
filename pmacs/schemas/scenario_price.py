"""Scenario-weighted expected price schema (Architecture.md §9.4b).

Pure-Python, LLM-free. The ScenarioPriceEngine consumes the Arbitrated probability
vector plus the reverse-DCF fair value / valuation range and produces a
probability-weighted expected price. Feeds MemoWriter only — it does NOT replace
compute_ev's ev_multiple (a trade-expectancy ratio, not a valuation multiple).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ScenarioPriceResult(BaseModel):
    """Probability-weighted expected price across bull/base/bear scenarios.

    spec_ref: Architecture.md §9.4b, Source.md §16.9
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    cycle_id: str = ""
    bull_price: float | None = None
    base_price: float | None = None
    bear_price: float | None = None
    # E[price] = p_up * bull + p_flat * base + p_down * bear (Arbitrated probs).
    expected_price_usd: float | None = None
    # The arbitrated probabilities used (for audit/transparency).
    p_up: float | None = None
    p_flat: float | None = None
    p_down: float | None = None
    current_price_usd: float | None = None
    # Expected return vs current price, when both are available.
    expected_return_pct: float | None = None
    notes: str = ""

    @property
    def is_available(self) -> bool:
        return self.expected_price_usd is not None