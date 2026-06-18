"""Arbitration engine — Brier-inverse weighted combination of persona signals.

Spec ref: Architecture.md §9.1, Agents.md §5.6, Agents.md §19.2

Steps:
  1. Separate mature (historical_n >= 30) and immature sources
  2. If no mature sources:
     - If all immature agree on direction -> PROCEED_BOOTSTRAP_LOW_CONFIDENCE
     - If immature disagree -> ABORT_NO_MATURE_SOURCES
  3. Compute weights: w_i = 1 / (rolling_brier + WEIGHT_EPSILON) for mature sources
  4. Apply MacroRegime 0.5x multiplier to its weight
  5. Apply extreme-probability dampening: if p_up > 0.9 or p_down > 0.9, cap
     weight at 0.5x
  6. Normalize weights to sum to 1.0
  7. Weighted average of probability vectors
  8. Check agreement: if mature sources disagree strongly -> ABORT_DISAGREEMENT
"""

from __future__ import annotations

import math

from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.arbitration import (
    Arbitrated,
    ArbitrationDecision,
    PersonaWeight,
)

# ---------------------------------------------------------------------------
# Constants (Architecture.md §9.1)
# ---------------------------------------------------------------------------

UNINFORMED_3STATE_BRIER = 0.667
WEIGHT_EPSILON = 0.05
MIN_HISTORICAL_N_FOR_MATURE = 30
MACRO_REGIME_WEIGHT_MULTIPLIER = 0.5
EXTREME_PROB_DAMPENING_THRESHOLD = 0.9
EXTREME_PROB_DAMPENING_FACTOR = 0.5
CATALYST_SUMMARIZER_WEIGHT_MULTIPLIER = 0.5
FORENSICS_MATERIAL_CONCERNS_MULTIPLIER = 1.5
DATALESS_PERSONA_WEIGHT_MULTIPLIER = 0.0


# ---------------------------------------------------------------------------
# Signal wrapper — extends DirectionalProbability with arbitration metadata
# ---------------------------------------------------------------------------


class ArbitrationSignal:
    """Wraps a DirectionalProbability with maturity / Brier metadata.

    The existing DirectionalProbability schema (agents.py) does not have
    historical_n or rolling_brier fields. We carry those alongside the
    signal for arbitration purposes.
    """

    def __init__(
        self,
        dp: DirectionalProbability,
        *,
        historical_n: int = 0,
        rolling_brier: float = UNINFORMED_3STATE_BRIER,
        quality_tag: str | None = None,
    ):
        self.dp = dp
        self.historical_n = historical_n
        self.rolling_brier = rolling_brier
        self.quality_tag = (quality_tag or "").upper()

    @property
    def persona(self) -> PersonaName:
        return self.dp.persona

    @property
    def ticker(self) -> str:
        return self.dp.ticker

    @property
    def p_up(self) -> float:
        return self.dp.p_up

    @property
    def p_flat(self) -> float:
        return self.dp.p_flat

    @property
    def p_down(self) -> float:
        return self.dp.p_down

    @property
    def is_mature(self) -> bool:
        return self.historical_n >= MIN_HISTORICAL_N_FOR_MATURE

    @property
    def has_extreme_prob(self) -> bool:
        return (
            self.p_up > EXTREME_PROB_DAMPENING_THRESHOLD
            or self.p_down > EXTREME_PROB_DAMPENING_THRESHOLD
        )

    @property
    def is_dataless(self) -> bool:
        """True when the persona explicitly reported no usable data.

        Agents.md §10/§9: ShortInterest and InsiderActivity must emit
        INSUFFICIENT_DATA / NO_SIGNAL when they lack data. Arbitration
        should ignore those signals rather than let near-uniform probabilities
        anchor conviction.
        """
        if self.persona not in (
            PersonaName.SHORT_INTEREST,
            PersonaName.INSIDER_ACTIVITY,
        ):
            return False
        return self.quality_tag in ("INSUFFICIENT_DATA", "NO_SIGNAL")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dominant_direction(p_up: float, p_flat: float, p_down: float) -> str:
    """Return the dominant direction: 'up', 'flat', or 'down'."""
    if p_up >= p_flat and p_up >= p_down:
        return "up"
    if p_down >= p_flat and p_down >= p_up:
        return "down"
    return "flat"


def _all_agree_direction(signals: list[ArbitrationSignal]) -> bool:
    """Check if all signals agree on dominant direction."""
    if len(signals) <= 1:
        return True
    directions = {
        _dominant_direction(s.p_up, s.p_flat, s.p_down) for s in signals
    }
    return len(directions) == 1


def _majority_direction(
    signals: list[ArbitrationSignal],
) -> tuple[bool, str, list[ArbitrationSignal]]:
    """Check if a majority (>=60%) of signals agree on dominant direction.

    Returns (has_majority, majority_dir, majority_signals).
    Uses 60% threshold: with 7 personas, requires 5+ to agree.
    Bootstrap proceeds on majority to avoid unanimous-abort from 1-2 flat outliers.
    """
    if not signals:
        return False, "flat", []
    if len(signals) == 1:
        d = _dominant_direction(signals[0].p_up, signals[0].p_flat, signals[0].p_down)
        return True, d, signals

    from collections import Counter
    dir_map = {
        s: _dominant_direction(s.p_up, s.p_flat, s.p_down) for s in signals
    }
    counts = Counter(dir_map.values())
    # Deterministic tie-breaking: prefer "up" > "flat" > "down"
    _tie_order = {"up": 0, "flat": 1, "down": 2}
    majority_dir = max(counts, key=lambda d: (counts[d], -_tie_order.get(d, 99)))
    majority_count = counts[majority_dir]
    threshold = max(2, math.ceil(len(signals) * 0.60))  # true 60% ceiling, min 2
    if majority_count >= threshold:
        majority_signals = [s for s in signals if dir_map[s] == majority_dir]
        return True, majority_dir, majority_signals
    return False, majority_dir, []


def _mature_disagree(signals: list[ArbitrationSignal]) -> bool:
    """Check if any two mature sources have opposing dominant directions.

    Agreement check: if any mature source has p_up > 0.5 and another has
    p_down > 0.5 -> disagreement.
    """
    has_up = any(s.p_up > 0.5 for s in signals)
    has_down = any(s.p_down > 0.5 for s in signals)
    return has_up and has_down


def disagreement_severity(signals: list[ArbitrationSignal]) -> float:
    """Compute disagreement severity across mature sources (Architecture.md §9.1).

    Returns 0.0 (full agreement) to 1.0 (maximum disagreement).
    Uses variance of directional probabilities across sources.
    """
    if len(signals) < 2:
        return 0.0

    # Compute variance of p_up across sources
    p_ups = [s.p_up for s in signals]
    mean_up = sum(p_ups) / len(p_ups)
    variance = sum((p - mean_up) ** 2 for p in p_ups) / len(p_ups)

    # Normalize to 0-1 range (max variance for binary split = 0.25)
    severity = min(1.0, variance / 0.25)
    return severity


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def arbitrate(
    signals: list[ArbitrationSignal],
    *,
    cycle_id: str = "",
) -> Arbitrated:
    """Combine persona signals using Brier-inverse weighting.

    Args:
        signals: List of ArbitrationSignal (DirectionalProbability + metadata).
        cycle_id: Current cycle identifier.

    Returns:
        Arbitrated with combined probabilities, weights, and decision.
    """
    if not signals:
        log_debug(
            "ARBITRATION_NO_SIGNALS",
            payload={"ticker": "", "decision": "ABORT_NO_MATURE_SOURCES"},
            cycle_id=cycle_id,
            msg="No signals provided to arbitration",
        )
        return Arbitrated(
            ticker="",
            cycle_id=cycle_id,
            p_up=1.0 / 3,
            p_flat=1.0 / 3,
            p_down=1.0 / 3,
            decision=ArbitrationDecision.ABORT_NO_MATURE_SOURCES,
            abort_reason="NO_SIGNALS",
            matured_sources_used=0,
        )

    ticker = signals[0].ticker

    # 1. Separate mature and immature sources
    mature = [s for s in signals if s.is_mature]
    immature = [s for s in signals if not s.is_mature]

    # 1a. Exclude dataless signals from decision-making. They remain in audit
    # output with zero weight so the operator can see they fired but did not
    # contribute to conviction.
    usable_immature = [s for s in immature if not s.is_dataless]
    usable_mature = [s for s in mature if not s.is_dataless]

    # 2. No mature sources -> bootstrap logic
    if not usable_mature:
        has_majority, majority_dir, majority_signals = _majority_direction(usable_immature)
        if usable_immature and has_majority:
            # Average only majority-agreeing personas to keep signal clean
            use = majority_signals
            n = len(use)
            p_up = sum(s.p_up for s in use) / n
            p_flat = sum(s.p_flat for s in use) / n
            p_down = sum(s.p_down for s in use) / n

            # Include all immature in output for audit, but weight only majority
            weights = [
                PersonaWeight(
                    persona=s.persona,
                    weight=1.0 / n if s in use else 0.0,
                    brier_score=s.rolling_brier,
                    calibration_count=s.historical_n,
                )
                for s in immature
            ]

            agreement_score = len(majority_signals) / len(immature)

            log_debug(
                "ARBITRATION_BOOTSTRAP_MAJORITY",
                payload={
                    "ticker": ticker,
                    "majority_dir": majority_dir,
                    "majority_count": len(majority_signals),
                    "total_immature": len(immature),
                    "agreement_score": round(agreement_score, 3),
                    "p_up": round(p_up, 4),
                    "p_flat": round(p_flat, 4),
                    "p_down": round(p_down, 4),
                },
                cycle_id=cycle_id,
                msg=f"Bootstrap majority ({len(majority_signals)}/{len(immature)}) on {majority_dir}",
            )

            return Arbitrated(
                ticker=ticker,
                cycle_id=cycle_id,
                p_up=p_up,
                p_flat=p_flat,
                p_down=p_down,
                persona_outputs=[s.dp for s in immature],
                persona_weights=weights,
                agreement_score=agreement_score,
                matured_sources_used=0,
                decision=ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE,
            )
        else:
            log_debug(
                "ARBITRATION_NO_MATURE_DISAGREE",
                payload={"ticker": ticker, "immature_count": len(immature)},
                cycle_id=cycle_id,
                msg="Immature sources have no majority direction, no mature sources",
            )
            return Arbitrated(
                ticker=ticker,
                cycle_id=cycle_id,
                p_up=1.0 / 3,
                p_flat=1.0 / 3,
                p_down=1.0 / 3,
                persona_outputs=[s.dp for s in immature],
                matured_sources_used=0,
                decision=ArbitrationDecision.ABORT_NO_MATURE_SOURCES,
                abort_reason="NO_MAJORITY_DIRECTION",
            )

    # 8. Agreement check on usable mature sources
    if _mature_disagree(usable_mature):
        log_debug(
            "ARBITRATION_MATURE_DISAGREEMENT",
            payload={
                "ticker": ticker,
                "mature_count": len(usable_mature),
                "severity": round(disagreement_severity(usable_mature), 3),
            },
            cycle_id=cycle_id,
            error_code="ARBITRATION_DISAGREEMENT",
            level="WARN",
            msg="Mature sources disagree on direction",
        )
        return Arbitrated(
            ticker=ticker,
            cycle_id=cycle_id,
            p_up=1.0 / 3,
            p_flat=1.0 / 3,
            p_down=1.0 / 3,
            persona_outputs=[s.dp for s in mature],
            matured_sources_used=len(usable_mature),
            decision=ArbitrationDecision.ABORT_DISAGREEMENT,
            abort_reason="MATURE_SOURCES_DISAGREE",
        )

    # 3. Compute Brier-inverse weights for usable mature sources
    raw_weights: list[float] = []
    applied_multipliers: dict[str, float] = {}
    for s in usable_mature:
        w = 1.0 / (s.rolling_brier + WEIGHT_EPSILON)

        # 4. MacroRegime weight multiplier
        if s.persona == PersonaName.MACRO_REGIME:
            w *= MACRO_REGIME_WEIGHT_MULTIPLIER
            applied_multipliers[s.persona.value] = MACRO_REGIME_WEIGHT_MULTIPLIER

        # 5. Extreme-probability dampening (anti-injection, Agents.md §19.2)
        if s.has_extreme_prob:
            w *= EXTREME_PROB_DAMPENING_FACTOR
            applied_multipliers[s.persona.value] = applied_multipliers.get(
                s.persona.value, 1.0
            ) * EXTREME_PROB_DAMPENING_FACTOR

        # IMP-1: CatalystSummarizer perma-bull dampening
        if s.persona == PersonaName.CATALYST_SUMMARIZER:
            w *= CATALYST_SUMMARIZER_WEIGHT_MULTIPLIER
            applied_multipliers[s.persona.value] = CATALYST_SUMMARIZER_WEIGHT_MULTIPLIER

        # IMP-3: Forensics boost when material accounting concerns are flagged
        if (
            s.persona == PersonaName.FORENSICS
            and s.quality_tag in ("MATERIAL_CONCERNS", "SEVERE_RISK")
        ):
            w *= FORENSICS_MATERIAL_CONCERNS_MULTIPLIER
            applied_multipliers[s.persona.value] = FORENSICS_MATERIAL_CONCERNS_MULTIPLIER

        raw_weights.append(w)

    # 6. Normalize weights to sum to 1.0
    total_w = sum(raw_weights)
    if total_w == 0:
        # All weights zeroed out — log WARN and fall back to equal weighting
        log_debug(
            "ARBITRATION_WEIGHT_COLLAPSE",
            payload={"ticker": ticker, "mature_count": len(usable_mature)},
            cycle_id=cycle_id,
            level="WARN",
            error_code="ARBITRATION_WEIGHT_COLLAPSE",
            msg="All Brier-inverse weights zeroed — falling back to equal weighting",
        )
        norm_weights = [1.0 / len(usable_mature)] * len(usable_mature)
    else:
        norm_weights = [w / total_w for w in raw_weights]

    # 7. Weighted average of probability vectors
    p_up = sum(s.p_up * w for s, w in zip(usable_mature, norm_weights))
    p_flat = sum(s.p_flat * w for s, w in zip(usable_mature, norm_weights))
    p_down = sum(s.p_down * w for s, w in zip(usable_mature, norm_weights))

    # Re-normalize to correct floating-point drift (sum may deviate from 1.0)
    _prob_total = p_up + p_flat + p_down
    if abs(_prob_total - 1.0) > 0.01:
        log_debug(
            "ARBITRATION_PROB_DRIFT",
            payload={"prob_sum": round(_prob_total, 4), "p_up": round(p_up, 3),
                     "p_flat": round(p_flat, 3), "p_down": round(p_down, 3)},
            level="WARN",
            error_code="PROBABILITY_SUM_DRIFT",
            cycle_id="",
            msg=f"Probability sum {_prob_total:.4f} deviates from 1.0 — upstream bug?",
        )
    if _prob_total > 0:
        p_up /= _prob_total
        p_flat /= _prob_total
        p_down /= _prob_total

    # Build persona weights list (include dataless mature signals with zero weight)
    weight_by_persona = {
        s.persona.value: w for s, w in zip(usable_mature, norm_weights)
    }
    persona_weights = [
        PersonaWeight(
            persona=s.persona,
            weight=weight_by_persona.get(s.persona.value, 0.0),
            brier_score=s.rolling_brier,
            calibration_count=s.historical_n,
            weight_multiplier=applied_multipliers.get(
                s.persona.value,
                DATALESS_PERSONA_WEIGHT_MULTIPLIER if s.is_dataless else 1.0,
            ),
        )
        for s in mature
    ]

    # Compute agreement score as fraction of usable mature sources agreeing with plurality direction
    directions = [
        _dominant_direction(s.p_up, s.p_flat, s.p_down)
        for s in usable_mature
    ]
    if directions:
        plurality_dir = max(set(directions), key=directions.count)
        agreement = directions.count(plurality_dir) / len(directions)
    else:
        agreement = 0.0

    log_debug(
        "ARBITRATION_COMPLETE",
        payload={
            "ticker": ticker,
            "p_up": round(p_up, 4),
            "p_flat": round(p_flat, 4),
            "p_down": round(p_down, 4),
            "matured_sources_used": len(usable_mature),
            "dataless_sources": sum(1 for s in mature if s.is_dataless),
            "agreement": round(agreement, 3),
            "applied_multipliers": applied_multipliers,
        },
        cycle_id=cycle_id,
        msg="Arbitration completed successfully",
    )

    return Arbitrated(
        ticker=ticker,
        cycle_id=cycle_id,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        persona_outputs=[s.dp for s in mature],
        persona_weights=persona_weights,
        agreement_score=agreement,
        matured_sources_used=len(mature),
        decision=ArbitrationDecision.PROCEED,
    )
