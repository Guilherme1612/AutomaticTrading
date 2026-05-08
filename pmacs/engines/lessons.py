"""Lessons engine — extract structured lessons from resolved cycles."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Lesson:
    ticker: str
    lesson_type: str  # "success_pattern", "failure_pattern", "override_lesson"
    text: str
    evidence_ids: list[str]
    cycle_id: str


def extract_lesson_from_resolution(
    ticker: str,
    thesis: str,
    verdict: str,
    actual_outcome: str,
    failure_taxonomy: str | None = None,
    cycle_id: str = "",
) -> Lesson | None:
    """Extract a lesson from a resolution.

    Returns ``None`` if no meaningful lesson can be extracted.

    Parameters
    ----------
    ticker : str
    thesis : str
        Original thesis text.
    verdict : str
        Final arbitration verdict.
    actual_outcome : str
        Resolved outcome (``"up"``, ``"flat"``, ``"down"``).
    failure_taxonomy : str | None
        FDE taxonomy code if a failure was diagnosed.
    cycle_id : str
        Originating cycle identifier.
    """
    if failure_taxonomy and failure_taxonomy != "UNCLASSIFIED":
        text = f"On {ticker}, failure type {failure_taxonomy}: thesis was '{thesis[:100]}'. "
        text += f"Verdict was {verdict}, actual outcome was {actual_outcome}."
        return Lesson(
            ticker=ticker,
            lesson_type="failure_pattern",
            text=text,
            evidence_ids=[],
            cycle_id=cycle_id,
        )

    if verdict in ("STRONG_BUY", "BUY") and actual_outcome == "up":
        text = f"On {ticker}, successful {verdict}: thesis was '{thesis[:100]}'. Pattern validated."
        return Lesson(
            ticker=ticker,
            lesson_type="success_pattern",
            text=text,
            evidence_ids=[],
            cycle_id=cycle_id,
        )

    return None
