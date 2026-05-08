"""Calibration engine — Brier score computation and persona weight refitting."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CalibrationResult:
    persona: str
    old_weight: float
    new_weight: float
    brier_before: float
    brier_after: float
    samples_used: int


def compute_brier(p_up: float, p_flat: float, p_down: float, actual: str) -> float:
    """Compute Brier score for a single three-outcome prediction.

    Parameters
    ----------
    p_up, p_flat, p_down : float
        Forecast probabilities (should sum to ~1.0).
    actual : str
        One of ``"up"``, ``"flat"``, ``"down"``.

    Returns
    -------
    float
        Brier score in [0, 2].  Lower is better; 0 = perfect.
    """
    o = {"up": [1, 0, 0], "flat": [0, 1, 0], "down": [0, 0, 1]}
    actual_vec = o.get(actual, [0.33, 0.34, 0.33])
    forecast = [p_up, p_flat, p_down]
    return sum((f - a) ** 2 for f, a in zip(forecast, actual_vec))


def refit_persona_weights(
    persona_briers: dict[str, float],
    current_weights: dict[str, float],
    min_samples: int = 20,
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

    return new_weights
