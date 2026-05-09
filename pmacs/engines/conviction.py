"""Conviction engine — maps probability + Crucible severity + EV to conviction scalar.

Spec ref: Architecture.md §9.2
"""

from __future__ import annotations

from pmacs.schemas.arbitration import Arbitrated
from pmacs.schemas.conviction import ConvictionResult, VerdictTier


def compute_conviction(
    arb: Arbitrated,
    crucible_severity: float,
    ev_multiple: float,
    is_bootstrap: bool = False,
) -> float:
    """Maps probability + Crucible severity + EV to conviction scalar.

    Range: -1.0 to 1.0. Negative = SKIP.

    direction = arb.p_up - arb.p_down
    maturity_factor = matured_sources / 4.0 (floored at 0.50 for bootstrap)
    crucible_factor = 1.0 - crucible_severity
    ev_factor = ev_multiple / 1.5 (capped at 1.0)

    conviction = direction * maturity_factor * crucible_factor * ev_factor
    """
    direction = arb.p_up - arb.p_down

    if is_bootstrap:
        maturity_factor = max(0.50, min(arb.matured_sources_used / 4.0, 1.0))
    else:
        maturity_factor = max(0.25, min(arb.matured_sources_used / 4.0, 1.0))

    crucible_factor = max(0.0, 1.0 - crucible_severity)
    ev_factor = min(ev_multiple / 1.5, 1.0)

    conviction = direction * maturity_factor * crucible_factor * ev_factor
    # Clamp to [-1.0, 1.0]
    return max(-1.0, min(conviction, 1.0))


def verdict_tier(
    conviction: float,
    is_active_holding: bool = False,
    thesis_valid: bool = True,
) -> VerdictTier:
    """Maps conviction to verdict tier.

    STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3 or negative.
    Negative conviction always SKIP (no shorting in v1).
    Active holding with valid thesis -> HOLD.
    """
    if is_active_holding and thesis_valid:
        return VerdictTier.HOLD  # Active position with valid thesis
    if conviction >= 0.6:
        return VerdictTier.STRONG_BUY
    if conviction >= 0.3:
        return VerdictTier.BUY
    return VerdictTier.SKIP
