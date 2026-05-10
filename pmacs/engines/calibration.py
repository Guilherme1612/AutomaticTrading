"""Calibration engine — Brier score computation and persona weight refitting.

Architecture.md §1.8: Both audit and debug logging required.
Architecture.md §1.11 / §16.5: cycle_id required on audit-emitting functions.
"""
from __future__ import annotations

from dataclasses import dataclass

from pmacs.logsys import log_debug


@dataclass
class CalibrationResult:
    persona: str
    old_weight: float
    new_weight: float
    brier_before: float
    brier_after: float
    samples_used: int


def compute_brier(
    p_up: float,
    p_flat: float,
    p_down: float,
    actual: str,
    cycle_id: str = "",
) -> float:
    """Compute Brier score for a single three-outcome prediction.

    Parameters
    ----------
    p_up, p_flat, p_down : float
        Forecast probabilities (should sum to ~1.0).
    actual : str
        One of ``"up"``, ``"flat"``, ``"down"``.
    cycle_id : str
        Cycle ID for debug traceability (Architecture.md §5.2).

    Returns
    -------
    float
        Brier score in [0, 2].  Lower is better; 0 = perfect.
    """
    o = {"up": [1, 0, 0], "flat": [0, 1, 0], "down": [0, 0, 1]}
    actual_vec = o.get(actual, [0.33, 0.34, 0.33])
    forecast = [p_up, p_flat, p_down]
    result = sum((f - a) ** 2 for f, a in zip(forecast, actual_vec))

    # Debug event — Brier computation detail (Architecture.md §1.8)
    log_debug(
        "BRIER_COMPUTED",
        payload={
            "p_up": p_up,
            "p_flat": p_flat,
            "p_down": p_down,
            "actual": actual,
            "brier": result,
        },
        level="DEBUG",
        cycle_id=cycle_id,
    )

    return result


def refit_persona_weights(
    persona_briers: dict[str, float],
    current_weights: dict[str, float],
    min_samples: int = 20,
    cycle_id: str = "",
) -> dict[str, float]:
    """Refit persona arbitration weights based on Brier scores.

    Weight = ``1 / (brier + epsilon)``.  Renormalized to sum to 1.0.
    Only refits personas with >= *min_samples* (enforced by caller).

    Parameters
    ----------
    persona_briers : dict[str, float]
        Mapping of persona name -> recent average Brier score.
    current_weights : dict[str, float]
        Current arbitration weights (used as fallback).
    min_samples : int
        Minimum sample threshold — informational only in this function.
    cycle_id : str
        REQUIRED cycle ID (Architecture.md §16.5).

    Returns
    -------
    dict[str, float]
        New weights summing to 1.0.
    """
    WEIGHT_EPSILON = 0.05
    new_weights: dict[str, float] = {}
    for persona, brier in persona_briers.items():
        new_weights[persona] = 1.0 / (brier + WEIGHT_EPSILON)

    total = sum(new_weights.values())
    if total > 0:
        new_weights = {p: w / total for p, w in new_weights.items()}

    # Audit event — calibration refit (Architecture.md §1.8)
    log_debug(
        "CALIBRATION_REFIT",
        payload={
            "personas": list(persona_briers.keys()),
            "samples_used": min_samples,
            "new_weights": new_weights,
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Calibration refit: {len(persona_briers)} personas, min_samples={min_samples}",
    )

    return new_weights
