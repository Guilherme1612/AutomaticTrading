"""Failure Diagnostic Engine — deterministic 18-type classifier (Agents.md §15).

This engine classifies terminal-state holdings into one of the 18 taxonomy
types defined in ``pmacs.schemas.failure.FailureTaxonomy``.  It is pure
Python — no LLM calls.

Spec reference: spec/Agents.md §15, spec/Architecture.md §9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from pmacs.schemas.failure import FailureClassification, FailedAssumption, FailureTaxonomy


# ---------------------------------------------------------------------------
# Lightweight context — avoids tight coupling to the full Holding ORM model.
# ---------------------------------------------------------------------------

@dataclass
class HoldingContext:
    """Simplified context for classification."""

    state: str  # terminal state name (e.g. "STOPPED_OUT")
    ticker: str
    entry_price: float
    exit_price: float | None = None
    stop_loss_price: float | None = None
    exit_reason: str | None = None
    exit_date: datetime | None = None
    actual_outcome: str | None = None  # "up", "flat", "down"
    price_48h_after_exit: float | None = None
    price_30d_after_exit: float | None = None
    sector_drop_5d_pct: float | None = None
    moat_strength: float | None = None
    revenue_acceleration: str | None = None  # "ACCELERATING" etc.
    forensics_flags: list[str] = field(default_factory=list)
    insider_signal: str | None = None
    short_anomaly: str | None = None
    realized_pnl_pct: float | None = None
    expected_max_loss_pct: float | None = None
    fill_slippage_pct: float | None = None
    correlation_with_sector: float | None = None


# ---------------------------------------------------------------------------
# Convenience dataclass returned by the public ``classify`` function.
# ---------------------------------------------------------------------------

@dataclass
class ClassifyResult:
    """Deterministic output of the failure diagnostic engine."""

    primary: FailureTaxonomy
    severity: float  # 0.0 – 1.0
    summary: str
    holding_id: str = ""
    cycle_id: str = ""


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def classify(holding: HoldingContext, **kwargs) -> ClassifyResult:  # noqa: ANN003
    """Classify a terminal-state holding into one of 18 taxonomy types.

    Returns a ``ClassifyResult`` with the primary taxonomy, severity and a
    human-readable summary.  The caller is responsible for converting this
    into a ``FailureClassification`` schema object if needed.
    """

    holding_id: str = kwargs.get("holding_id", "")
    cycle_id: str = kwargs.get("cycle_id", "")

    # --- Abort states — not failures, they're prevention -------------------
    if holding.state in ("ABORTED_PRE_LLM", "ABORTED_LLM", "ABORTED_RISK"):
        return ClassifyResult(
            primary=FailureTaxonomy.UNCLASSIFIED,
            severity=0.0,
            summary=f"Aborted: {holding.exit_reason or holding.state}",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # --- STOPPED_OUT / EXIT_TRAILING_STOP ----------------------------------
    if holding.state in ("STOPPED_OUT", "EXIT_TRAILING_STOP"):
        return _classify_stop(holding, holding_id=holding_id, cycle_id=cycle_id)

    # --- EXIT_THESIS_INVALIDATED -------------------------------------------
    if holding.state == "EXIT_THESIS_INVALIDATED":
        return _classify_thesis_invalidation(holding, holding_id=holding_id, cycle_id=cycle_id)

    # --- EXIT_OPPORTUNITY_COST ---------------------------------------------
    if holding.state == "EXIT_OPPORTUNITY_COST":
        return ClassifyResult(
            primary=FailureTaxonomy.OPPORTUNITY_COST_EXIT_CORRECT,
            severity=0.2,
            summary="Exit via opportunity cost decision",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # --- RESOLVED_DOWN / actual_outcome == "down" --------------------------
    if holding.actual_outcome == "down" or holding.state in ("RESOLVED_DOWN", "RESOLVED_MIXED"):
        return _classify_persona_failure(holding, holding_id=holding_id, cycle_id=cycle_id)

    # --- RESOLUTION_TIMEOUT ------------------------------------------------
    if holding.state == "RESOLUTION_TIMEOUT":
        return ClassifyResult(
            primary=FailureTaxonomy.CATALYST_TIMEOUT,
            severity=0.5,
            summary="Catalyst resolution timed out",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # --- PANIC_EXIT / EXIT_FAILED ------------------------------------------
    if holding.state in ("PANIC_EXIT", "EXIT_FAILED"):
        return ClassifyResult(
            primary=FailureTaxonomy.UNCLASSIFIED,
            severity=0.6,
            summary=f"Force exit: {holding.state}",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # --- Fallback ----------------------------------------------------------
    return ClassifyResult(
        primary=FailureTaxonomy.UNCLASSIFIED,
        severity=0.1,
        summary=f"Unclassified terminal state: {holding.state}",
        holding_id=holding_id,
        cycle_id=cycle_id,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_stop(
    holding: HoldingContext,
    **kwargs: str,
) -> ClassifyResult:
    """STOP_HUNTED vs STOP_LOSS_CORRECT vs EXOGENOUS_MACRO_SHOCK vs
    CORRELATION_REGIME_SHIFT."""

    holding_id: str = kwargs.get("holding_id", "")
    cycle_id: str = kwargs.get("cycle_id", "")

    # Exogenous macro shock — sector-wide drop >10% in 5d
    if (
        holding.sector_drop_5d_pct is not None
        and holding.sector_drop_5d_pct < -10.0
    ):
        return ClassifyResult(
            primary=FailureTaxonomy.EXOGENOUS_MACRO_SHOCK,
            severity=0.4,
            summary=f"Sector dropped {holding.sector_drop_5d_pct:.1f}% in 5d",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # STOP_HUNTED: price recovered within 48 h
    if (
        holding.price_48h_after_exit is not None
        and holding.entry_price is not None
        and holding.price_48h_after_exit > holding.entry_price * 1.02
    ):
        return ClassifyResult(
            primary=FailureTaxonomy.STOP_HUNTED,
            severity=0.7,
            summary=(
                f"Stopped at {holding.exit_price}, recovered to "
                f"{holding.price_48h_after_exit} within 48h"
            ),
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # STOP_LOSS_CORRECT: price stayed below stop for 30 d
    if (
        holding.price_30d_after_exit is not None
        and holding.stop_loss_price is not None
        and holding.price_30d_after_exit < holding.stop_loss_price
    ):
        return ClassifyResult(
            primary=FailureTaxonomy.STOP_LOSS_CORRECT,
            severity=0.2,
            summary="Stop saved money; price did not recover in 30d",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # Default for stops
    return ClassifyResult(
        primary=FailureTaxonomy.STOP_LOSS_CORRECT,
        severity=0.3,
        summary="Stop triggered, recovery unknown",
        holding_id=holding_id,
        cycle_id=cycle_id,
    )


def _classify_thesis_invalidation(
    holding: HoldingContext,
    **kwargs: str,
) -> ClassifyResult:
    """Determine FUNDAMENTAL vs COMPETITIVE vs REGULATORY thesis invalidation."""

    holding_id: str = kwargs.get("holding_id", "")
    cycle_id: str = kwargs.get("cycle_id", "")

    reason = (holding.exit_reason or "").lower()

    # Regulatory
    if "regulatory" in reason:
        return ClassifyResult(
            primary=FailureTaxonomy.THESIS_INVALIDATED_REGULATORY,
            severity=0.6,
            summary=f"Thesis invalidated by regulatory action: {holding.exit_reason}",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # Competitive / moat
    if any(kw in reason for kw in ("competitive", "moat")):
        return ClassifyResult(
            primary=FailureTaxonomy.THESIS_INVALIDATED_COMPETITIVE,
            severity=0.6,
            summary=f"Thesis invalidated by competitive dynamics: {holding.exit_reason}",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # Fundamental
    if "fundamental" in reason:
        return ClassifyResult(
            primary=FailureTaxonomy.THESIS_INVALIDATED_FUNDAMENTAL,
            severity=0.6,
            summary=f"Thesis invalidated by fundamental data: {holding.exit_reason}",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # Default: fundamental — we bailed but data didn't clearly specify.
    return ClassifyResult(
        primary=FailureTaxonomy.THESIS_INVALIDATED_FUNDAMENTAL,
        severity=0.5,
        summary="Fundamental data contradicted thesis",
        holding_id=holding_id,
        cycle_id=cycle_id,
    )


def _classify_persona_failure(
    holding: HoldingContext,
    **kwargs: str,
) -> ClassifyResult:
    """Check persona-specific failures when actual outcome is down."""

    holding_id: str = kwargs.get("holding_id", "")
    cycle_id: str = kwargs.get("cycle_id", "")

    # SIZING_OVERLEVERAGED — realized loss far exceeded expectation
    if (
        holding.realized_pnl_pct is not None
        and holding.expected_max_loss_pct is not None
        and abs(holding.realized_pnl_pct) > 2 * holding.expected_max_loss_pct
    ):
        return ClassifyResult(
            primary=FailureTaxonomy.SIZING_OVERLEVERAGED,
            severity=0.6,
            summary=(
                f"Realized loss {holding.realized_pnl_pct:.1f}% > 2x "
                f"expected {holding.expected_max_loss_pct:.1f}%"
            ),
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # EXECUTION_SLIPPAGE — significant fill slippage
    if holding.fill_slippage_pct is not None and holding.fill_slippage_pct > 1.0:
        return ClassifyResult(
            primary=FailureTaxonomy.EXECUTION_SLIPPAGE,
            severity=0.3,
            summary=f"Fill slippage {holding.fill_slippage_pct:.1f}%",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # MOAT_DRIFT_OVERESTIMATE — moat was rated high but thesis failed
    if holding.moat_strength is not None and holding.moat_strength > 0.7:
        return ClassifyResult(
            primary=FailureTaxonomy.MOAT_DRIFT_OVERESTIMATE,
            severity=0.5,
            summary=f"Moat scored {holding.moat_strength:.2f} but thesis failed",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # GROWTH_STALL_MISSED — revenue acceleration was positive but failed
    if holding.revenue_acceleration == "ACCELERATING":
        return ClassifyResult(
            primary=FailureTaxonomy.GROWTH_STALL_MISSED,
            severity=0.5,
            summary="Growth was rated ACCELERATING but thesis failed",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # FORENSICS_FLAG_IGNORED — forensics raised flags that were underweighted
    if holding.forensics_flags and len(holding.forensics_flags) > 0:
        return ClassifyResult(
            primary=FailureTaxonomy.FORENSICS_FLAG_IGNORED,
            severity=0.6,
            summary=(
                f"Forensics raised {len(holding.forensics_flags)} red flags "
                "that were underweighted"
            ),
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # INSIDER_SIGNAL_FALSE — insider buying was misleading
    if holding.insider_signal in ("CLUSTER_BUY", "CEO_BUY"):
        return ClassifyResult(
            primary=FailureTaxonomy.INSIDER_SIGNAL_FALSE,
            severity=0.4,
            summary=f"Insider signal was {holding.insider_signal} but thesis failed",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # SHORT_INTEREST_CORRECT — shorts were right
    if holding.short_anomaly == "SPIKE_UP":
        return ClassifyResult(
            primary=FailureTaxonomy.SHORT_INTEREST_CORRECT,
            severity=0.4,
            summary="Short interest spike was correct; shorts were right",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # CORRELATION_REGIME_SHIFT — high sector correlation treated as idiosyncratic
    if (
        holding.correlation_with_sector is not None
        and holding.correlation_with_sector > 0.8
    ):
        return ClassifyResult(
            primary=FailureTaxonomy.CORRELATION_REGIME_SHIFT,
            severity=0.4,
            summary=(
                f"Stock correlated {holding.correlation_with_sector:.2f} "
                "with sector but treated as idiosyncratic"
            ),
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # CATALYST_FALSE_POSITIVE — catalyst happened but market disagreed
    if holding.actual_outcome == "down" and holding.state in (
        "RESOLVED_DOWN",
        "RESOLVED_MIXED",
    ):
        return ClassifyResult(
            primary=FailureTaxonomy.CATALYST_FALSE_POSITIVE,
            severity=0.4,
            summary="Catalyst resolved but market disagreed",
            holding_id=holding_id,
            cycle_id=cycle_id,
        )

    # UNCLASSIFIED — fallback for down outcomes with no clear cause
    return ClassifyResult(
        primary=FailureTaxonomy.UNCLASSIFIED,
        severity=0.2,
        summary=f"Unresolved failure for {holding.ticker}",
        holding_id=holding_id,
        cycle_id=cycle_id,
    )
