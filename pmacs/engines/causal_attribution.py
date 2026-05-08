"""Causal attribution engine — credit/blame apportionment per persona."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AttributionResult:
    persona: str
    credit: float  # positive = contributed to correct decision
    reason: str


def attribute_resolution(
    verdict: str,
    actual_outcome: str,
    persona_outputs: dict[str, dict],
) -> list[AttributionResult]:
    """Attribute credit/blame to each persona based on their prediction.

    Parameters
    ----------
    verdict : str
        Final arbitration verdict (``"STRONG_BUY"``, ``"BUY"``, ``"HOLD"``, ``"SKIP"``).
    actual_outcome : str
        Resolved outcome (``"up"``, ``"flat"``, ``"down"``).
    persona_outputs : dict[str, dict]
        ``persona -> {p_up, p_flat, p_down}``

    Returns
    -------
    list[AttributionResult]
        One entry per persona with credit score and explanation.
    """
    results: list[AttributionResult] = []
    for persona, output in persona_outputs.items():
        p_up = output.get("p_up", 0.33)
        p_down = output.get("p_down", 0.33)

        if actual_outcome == "up":
            credit = p_up - p_down  # positive if predicted up
        elif actual_outcome == "down":
            credit = p_down - p_up  # positive if predicted down
        else:
            credit = 1.0 - abs(p_up - p_down)  # positive if predicted flat

        results.append(
            AttributionResult(
                persona=persona,
                credit=round(credit, 4),
                reason=f"Predicted p_up={p_up:.2f}, p_down={p_down:.2f}, actual={actual_outcome}",
            )
        )

    return results
