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
    ):
        self.dp = dp
        self.historical_n = historical_n
        self.rolling_brier = rolling_brier

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


def _mature_disagree(signals: list[ArbitrationSignal]) -> bool:
    """Check if any two mature sources have opposing dominant directions.

    Agreement check: if any mature source has p_up > 0.5 and another has
    p_down > 0.5 -> disagreement.
    """
    has_up = any(s.p_up > 0.5 for s in signals)
    has_down = any(s.p_down > 0.5 for s in signals)
    return has_up and has_down


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

    # 2. No mature sources -> bootstrap logic
    if not mature:
        if immature and _all_agree_direction(immature):
            # Equal weight average for immature
            n = len(immature)
            p_up = sum(s.p_up for s in immature) / n
            p_flat = sum(s.p_flat for s in immature) / n
            p_down = sum(s.p_down for s in immature) / n

            weights = [
                PersonaWeight(
                    persona=s.persona,
                    weight=1.0 / n,
                    brier_score=s.rolling_brier,
                    calibration_count=s.historical_n,
                )
                for s in immature
            ]

            return Arbitrated(
                ticker=ticker,
                cycle_id=cycle_id,
                p_up=p_up,
                p_flat=p_flat,
                p_down=p_down,
                persona_outputs=[s.dp for s in immature],
                persona_weights=weights,
                agreement_score=1.0 if _all_agree_direction(immature) else 0.0,
                matured_sources_used=0,
                decision=ArbitrationDecision.PROCEED_BOOTSTRAP_LOW_CONFIDENCE,
            )
        else:
            return Arbitrated(
                ticker=ticker,
                cycle_id=cycle_id,
                p_up=1.0 / 3,
                p_flat=1.0 / 3,
                p_down=1.0 / 3,
                persona_outputs=[s.dp for s in immature],
                matured_sources_used=0,
                decision=ArbitrationDecision.ABORT_NO_MATURE_SOURCES,
                abort_reason="NO_MATURE_SOURCES",
            )

    # 8. Agreement check on mature sources
    if _mature_disagree(mature):
        return Arbitrated(
            ticker=ticker,
            cycle_id=cycle_id,
            p_up=1.0 / 3,
            p_flat=1.0 / 3,
            p_down=1.0 / 3,
            persona_outputs=[s.dp for s in mature],
            matured_sources_used=len(mature),
            decision=ArbitrationDecision.ABORT_DISAGREEMENT,
            abort_reason="MATURE_SOURCES_DISAGREE",
        )

    # 3. Compute Brier-inverse weights for mature sources
    raw_weights: list[float] = []
    for s in mature:
        w = 1.0 / (s.rolling_brier + WEIGHT_EPSILON)

        # 4. MacroRegime weight multiplier
        if s.persona == PersonaName.MACRO_REGIME:
            w *= MACRO_REGIME_WEIGHT_MULTIPLIER

        # 5. Extreme-probability dampening (anti-injection, Agents.md §19.2)
        if s.has_extreme_prob:
            w *= EXTREME_PROB_DAMPENING_FACTOR

        raw_weights.append(w)

    # 6. Normalize weights to sum to 1.0
    total_w = sum(raw_weights)
    if total_w == 0:
        # All weights zeroed out — equal weighting fallback
        norm_weights = [1.0 / len(mature)] * len(mature)
    else:
        norm_weights = [w / total_w for w in raw_weights]

    # 7. Weighted average of probability vectors
    p_up = sum(s.p_up * w for s, w in zip(mature, norm_weights))
    p_flat = sum(s.p_flat * w for s, w in zip(mature, norm_weights))
    p_down = sum(s.p_down * w for s, w in zip(mature, norm_weights))

    # Build persona weights list
    persona_weights = [
        PersonaWeight(
            persona=s.persona,
            weight=w,
            brier_score=s.rolling_brier,
            calibration_count=s.historical_n,
        )
        for s, w in zip(mature, norm_weights)
    ]

    # Compute agreement score (1.0 = perfect agreement, 0.0 = max disagreement)
    directions = [_dominant_direction(s.p_up, s.p_flat, s.p_down) for s in mature]
    unique_dirs = set(directions)
    agreement = 1.0 if len(unique_dirs) == 1 else 0.5

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
