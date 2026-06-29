"""Position-sizing math for the /memo/{ticker} hero.

Allocator-grade display: R:R + share counts at three risk budgets + binding-cap
detection. Pure functions, no I/O. Used by memo.py route context.

spec_ref: spec/Source.md §13.1 (visual identity for sizing card);
          .planning/memo_allocator_redesign_prompt.md §3 (hero sizing card)

Five Non-Negotiables: LLMs NEVER math. Sizing math lives in Python here.
The memo_writer LLM produces a fair_value/price_target, never a share count.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingInputs:
    """Inputs to the position-sizing computation."""
    target_price: float           # ForwardValuation.base_price (or .expected_price_usd)
    stop_price: float             # Catastrophe-net stop below current
    current_price: float          # Live price from yfinance
    portfolio_value: float        # Paper portfolio value (sum of holdings)
    max_position_pct: float       # From config/risk.toml [position]


@dataclass(frozen=True)
class SizingResult:
    """Output of position-sizing computation, ready for the hero sizing card."""
    # Risk:Reward = (target - current) / (current - stop). None when stop >= current
    # (i.e. the stop is on the wrong side of current — degenerate setup).
    rr_ratio: float | None
    # Shares at three risk budgets. None when the math is degenerate.
    shares_at_1pct: int | None
    shares_at_2pct: int | None
    shares_at_5pct: int | None
    # Cost in USD for each risk budget.
    cost_at_1pct: float | None
    cost_at_2pct: float | None
    cost_at_5pct: float | None
    # The binding cap that wins when 20% portfolio cap is tighter than risk budget.
    # One of: "risk_1pct", "risk_2pct", "risk_5pct", "position_cap".
    binding_constraint: str
    # True when the math is well-defined and the result should render.
    is_available: bool
    # When is_available is False, this explains why (kept short for UI display).
    notes: str = ""


def compute_rr_ratio(target: float, stop: float, current: float) -> float | None:
    """R:R = (target - current) / (current - stop).

    Returns None when:
    - target <= current (no upside; not a trade)
    - stop >= current (stop on the wrong side; degenerate)
    - current <= 0 (live price unavailable)
    - any of the three are non-positive
    """
    if current <= 0 or target <= 0 or stop <= 0:
        return None
    if stop >= current:
        return None
    if target <= current:
        return None  # Negative R:R — operator should pass
    return (target - current) / (current - stop)


def compute_shares_at_risk(
    current: float, stop: float, portfolio_value: float, risk_pct: float
) -> int | None:
    """Shares you can buy such that (current - stop) * shares == risk_pct * portfolio_value.

    Returns 0 (not None) when the math is degenerate and floor is the only sane
    answer; returns None when the inputs make the math undefined.
    """
    if current <= 0 or stop <= 0 or portfolio_value <= 0 or risk_pct <= 0:
        return None
    if stop >= current:
        return None
    risk_dollars = risk_pct * portfolio_value
    loss_per_share = current - stop
    if loss_per_share <= 0:
        return None
    # 1-share minimum when the budget would round to zero — operators still
    # want to see *something* on the card, even if the position is a paper trade.
    raw = risk_dollars / loss_per_share
    return max(int(raw), 1)


def compute_sizing(inputs: SizingInputs) -> SizingResult:
    """Compute the full hero sizing display.

    Edge cases (returns is_available=False with notes):
    - current <= 0: live price unavailable
    - stop >= current: stop on the wrong side of current
    - target <= current: no upside (negative R:R)
    - portfolio_value <= 0: paper book empty
    - max_position_pct <= 0: config misconfigured

    Otherwise, computes:
    - R:R (rounded to 0.1)
    - Shares at 1%, 2%, 5% portfolio risk
    - Cost at each budget (current * shares)
    - Binding constraint (whichever is most restrictive: risk budget vs 20% cap)
    """
    notes = []
    if inputs.current_price <= 0:
        notes.append("live price unavailable")
    if inputs.portfolio_value <= 0:
        notes.append("paper portfolio empty")
    if inputs.max_position_pct <= 0:
        notes.append("max_position_pct not configured")
    if inputs.stop_price <= 0 or inputs.stop_price >= inputs.current_price:
        notes.append("stop is not below current price")

    if notes:
        return SizingResult(
            rr_ratio=None,
            shares_at_1pct=None, shares_at_2pct=None, shares_at_5pct=None,
            cost_at_1pct=None, cost_at_2pct=None, cost_at_5pct=None,
            binding_constraint="none",
            is_available=False,
            notes="; ".join(notes),
        )

    # R:R
    rr_raw = compute_rr_ratio(inputs.target_price, inputs.stop_price, inputs.current_price)
    if rr_raw is None or rr_raw <= 0:
        # No upside or no edge. The position-sizing card still renders share
        # counts (operators may want to see what 2%-risk looks like even on
        # a degenerate setup), but R:R is suppressed and the card is flagged.
        rr_ratio = None
    else:
        rr_ratio = round(rr_raw, 1)

    # Share counts at each risk budget
    s1 = compute_shares_at_risk(inputs.current_price, inputs.stop_price, inputs.portfolio_value, 0.01)
    s2 = compute_shares_at_risk(inputs.current_price, inputs.stop_price, inputs.portfolio_value, 0.02)
    s5 = compute_shares_at_risk(inputs.current_price, inputs.stop_price, inputs.portfolio_value, 0.05)

    # Costs
    def cost(shares: int | None) -> float | None:
        return round(shares * inputs.current_price, 2) if shares is not None else None

    c1, c2, c5 = cost(s1), cost(s2), cost(s5)

    # Binding constraint: 20% portfolio cap vs the largest share-count that
    # would breach the cap. The 20% cap is binding when its USD cost is
    # lower than the 5%-risk cost.
    cap_cost = inputs.max_position_pct * inputs.portfolio_value
    if c5 is not None and c5 > cap_cost:
        # The 20% cap is the tightest binding constraint.
        # Use the 2% risk budget as the "sensible default" share count for display.
        binding = "position_cap"
        # The cap-driven share count: floor(cap_cost / current_price)
        cap_shares = max(int(cap_cost / inputs.current_price), 1)
        s1 = min(s1, cap_shares) if s1 is not None else cap_shares
        s2 = min(s2, cap_shares) if s2 is not None else cap_shares
        s5 = min(s5, cap_shares) if s5 is not None else cap_shares
        c1, c2, c5 = cost(s1), cost(s2), cost(s5)
    else:
        # 5% risk is the most aggressive. Whichever the operator picks.
        binding = "risk_5pct" if (c5 is not None and c5 > cap_cost * 0.5) else "risk_2pct"

    return SizingResult(
        rr_ratio=rr_ratio,
        shares_at_1pct=s1,
        shares_at_2pct=s2,
        shares_at_5pct=s5,
        cost_at_1pct=c1,
        cost_at_2pct=c2,
        cost_at_5pct=c5,
        binding_constraint=binding,
        is_available=True,
        notes="",
    )
