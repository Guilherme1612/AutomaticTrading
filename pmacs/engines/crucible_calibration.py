"""Crucible calibration — adjust severity multiplier based on false-severity rate."""
from __future__ import annotations


def compute_severity_multiplier(
    current_multiplier: float,
    recent_false_severity_rate: float,
    learning_rate: float = 0.1,
) -> float:
    """Adjust Crucible severity multiplier.

    If the false-severity rate is high (Crucible often flags high severity
    but the thesis survives), the multiplier is reduced so the Crucible
    becomes less aggressive.  Clamped to [0.5, 2.0].

    Parameters
    ----------
    current_multiplier : float
        Existing multiplier value.
    recent_false_severity_rate : float
        Fraction of recent Crucible attacks where severity was flagged
        but the thesis survived regardless.  Range [0, 1].
    learning_rate : float
        Step size for adjustment.

    Returns
    -------
    float
        New multiplier in [0.5, 2.0].
    """
    adjustment = learning_rate * (0.5 - recent_false_severity_rate)
    return max(0.5, min(2.0, current_multiplier + adjustment))
