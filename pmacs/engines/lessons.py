"""Lessons engine — extract structured lessons from resolved cycles.

Spec ref: Architecture.md §9 step 23, Agents.md §18.6

Extracts lessons from resolutions and writes them to both SQLite (OLTP)
and Qdrant (vector similarity search for episodic context injection).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pmacs.logsys.debug_log import log_debug


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


def write_lesson_to_qdrant(
    lesson: Lesson,
    qdrant_adapter: Any,
) -> bool:
    """Write a lesson to Qdrant with embedding (Architecture.md §8.7).

    Generates a 768-dim embedding from the lesson text and upserts into
    the ``lessons`` collection.  Gracefully no-ops if Qdrant is unavailable.

    Parameters
    ----------
    lesson : Lesson
        The lesson to embed and store.
    qdrant_adapter : QdrantAdapter
        The Qdrant adapter instance.

    Returns
    -------
    bool
        True if the upsert succeeded (or was attempted), False on error.
    """
    try:
        lesson_id = f"{lesson.cycle_id}_{lesson.ticker}_{lesson.lesson_type}"
        qdrant_adapter.upsert_with_embedding(
            collection="lessons",
            id=lesson_id,
            text=lesson.text,
            payload={
                "lesson_id": lesson_id,
                "ticker": lesson.ticker,
                "kind": lesson.lesson_type,
                "lesson_text": lesson.text,
                "cycle_id": lesson.cycle_id,
                "evidence_ids": ",".join(lesson.evidence_ids),
            },
        )
        return True
    except Exception as exc:
        log_debug(
            "LESSON_WRITE_FAILED",
            payload={"ticker": lesson.ticker, "error": str(exc)},
            level="WARN",
            error_code="LESSON_WRITE_FAILED",
            msg=f"Lesson Qdrant write failed for {lesson.ticker}: {exc}",
        )
        return False
