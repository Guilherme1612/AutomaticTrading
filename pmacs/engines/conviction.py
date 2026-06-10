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
        # Bootstrap: no calibration history exists yet, so maturity dampening is
        # meaningless — we can't say an agent is "poorly calibrated", we simply have
        # no evidence either way. Use maturity_factor = 1.0 so that a genuine majority
        # consensus (direction >= 0.35 with reasonable Crucible + EV) can cross the
        # BUY threshold. Once agents have 30+ historical predictions, they graduate to
        # mature mode and Brier-inverse weighting takes over.
        maturity_factor = 1.0
    else:
        # Floor at 0.25 so partial maturity (e.g. 1/4 sources mature) still allows
        # conviction to register. In non-bootstrap with 0 mature sources, arbitration
        # should have aborted before reaching here.
        maturity_factor = max(0.25, min(arb.matured_sources_used / 4.0, 1.0))

    # Crucible amplification: high severity is MORE punitive than linear.
    # severity^0.7 makes severity 0.66 → effective 0.735 (factor 0.265 vs linear 0.34).
    # Models the reality that agents often miss what crucible catches.
    amplified_severity = crucible_severity ** 0.7
    crucible_factor = max(0.0, 1.0 - amplified_severity)
    # Clamp ev_factor to [0, 1]: negative EV means no edge; we suppress conviction
    # to 0 rather than inverting it (which would produce a false BUY for bearish setups).
    ev_factor = max(0.0, min(ev_multiple / 1.5, 1.0))

    conviction = direction * maturity_factor * crucible_factor * ev_factor
    # Clamp to [-1.0, 1.0]
    return max(-1.0, min(conviction, 1.0))


def verdict_tier(
    conviction: float,
    is_active_holding: bool = False,
    thesis_valid: bool = True,
    is_bootstrap: bool = False,
) -> VerdictTier:
    """Maps conviction to verdict tier.

    Standard: STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3.
    Bootstrap: STRONG_BUY >= 0.40, BUY >= 0.15, HOLD >= 0.05.
    Negative conviction always SKIP (no shorting in v1).
    Active holding with valid thesis -> HOLD.

    Bootstrap thresholds are lower because paper positions use virtual
    capital and generate needed trade data for Sharpe/drawdown/win-rate
    calculations required for mode promotion (Source.md §5, Phases.md §4).
    """
    if is_active_holding and thesis_valid:
        return VerdictTier.HOLD  # Active position with valid thesis
    if is_bootstrap:
        if conviction >= 0.40:
            return VerdictTier.STRONG_BUY
        if conviction >= 0.15:
            return VerdictTier.BUY
        if conviction >= 0.05:
            return VerdictTier.HOLD
        return VerdictTier.SKIP
    if conviction >= 0.6:
        return VerdictTier.STRONG_BUY
    if conviction >= 0.3:
        return VerdictTier.BUY
    return VerdictTier.SKIP
