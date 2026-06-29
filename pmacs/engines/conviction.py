"""Conviction engine — maps probability + Crucible severity + EV to conviction scalar.

Spec ref: Architecture.md §9.2
"""

from __future__ import annotations

from dataclasses import dataclass

from pmacs.schemas.arbitration import Arbitrated
from pmacs.schemas.conviction import ConvictionResult, VerdictTier


# ── PASS verdict trigger thresholds ──────────────────────────────────────────
# Allocator-grade memo: PASS is the *active* no-bid verdict. SKIP is the
# passive one (conviction floor). PASS fires when analyst-derived signals
# say the setup is real but the edge doesn't justify entry. The trigger
# conditions live here so the orchestrator (Architecture.md §12) and the
# memo route (web/routes/memo.py) can call them with consistent semantics.
PASS_RR_THRESHOLD = 1.5       # R:R below this → R:R alone is a no-bid
PASS_GROWTH_THRESHOLD = 0.10  # Growth below this AND no comps → no-bid


@dataclass(frozen=True)
class PassSignal:
    """Carries a PASS trigger + the structured reason for the memo."""
    triggered: bool
    reason: str = ""
    reason_code: str = ""  # machine-readable: "rr_below_threshold" | "comps_empty_growth_below_threshold"


def evaluate_pass_signal(
    rr_ratio: float | None,
    comparable_transactions: list | None,
    growth_pct: float | None,
) -> PassSignal:
    """Evaluate whether PASS is warranted (active no-bid verdict).

    Returns a PassSignal whose `reason` field populates the memo's
    pass_reason field. Two triggers:
      - rr_ratio < PASS_RR_THRESHOLD and rr_ratio is not None
      - comparable_transactions empty (or None) AND growth_pct < PASS_GROWTH_THRESHOLD
    """
    # Trigger 1: R:R below threshold
    if rr_ratio is not None and rr_ratio < PASS_RR_THRESHOLD:
        return PassSignal(
            triggered=True,
            reason=f"R:R {rr_ratio:.2f} below threshold {PASS_RR_THRESHOLD:.2f} — edge does not justify capital at risk",
            reason_code="rr_below_threshold",
        )

    # Trigger 2: comps empty + growth below threshold
    comps_empty = not comparable_transactions or len(comparable_transactions) == 0
    if comps_empty and growth_pct is not None and growth_pct < PASS_GROWTH_THRESHOLD:
        return PassSignal(
            triggered=True,
            reason=f"No comparable transactions and growth {growth_pct:.1%} below threshold {PASS_GROWTH_THRESHOLD:.0%} — setup lacks both edge and credibility",
            reason_code="comps_empty_growth_below_threshold",
        )

    return PassSignal(triggered=False)


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

    # Crucible penalty: piecewise by thesis survival.
    # Below 0.50 (thesis survives): linear penalty — severity 0.41 → factor 0.59.
    #   A thesis that passed adversarial review deserves proportional, not amplified, penalty.
    # Above 0.50 (thesis rejected): amplified penalty (^0.7) — severity 0.66 → factor 0.27.
    #   Rejected theses get extra punishment because agents often miss what crucible catches.
    if crucible_severity <= 0.50:
        effective_severity = crucible_severity
    else:
        effective_severity = crucible_severity ** 0.7
    crucible_factor = max(0.0, 1.0 - effective_severity)
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
    *,
    rr_ratio: float | None = None,
    comparable_transactions: list | None = None,
    growth_pct: float | None = None,
) -> VerdictTier:
    """Maps conviction to verdict tier.

    Standard: STRONG_BUY >= 0.6, BUY >= 0.3, SKIP < 0.3.
    Bootstrap: STRONG_BUY >= 0.40, BUY >= 0.15, HOLD >= 0.05.
    Negative conviction always SKIP (no shorting in v1).
    Active holding with valid thesis -> HOLD.

    PASS triggers (allocator-grade memo): when ``rr_ratio`` < 1.5 OR
    (no comparable transactions AND growth < 10%), the verdict is PASS
    regardless of conviction floor. PASS is an *active* no-bid — operator
    commits to ``pass_reason`` for the memo. See ``evaluate_pass_signal``.

    Bootstrap thresholds are lower because paper positions use virtual
    capital and generate needed trade data for Sharpe/drawdown/win-rate
    calculations required for mode promotion (Source.md §5, Phases.md §4).
    """
    # PASS triggers fire BEFORE the conviction floor — operator-analyst
    # judgment (R:R, comps presence) beats a raw number.
    signal = evaluate_pass_signal(rr_ratio, comparable_transactions, growth_pct)
    if signal.triggered:
        return VerdictTier.PASS

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
