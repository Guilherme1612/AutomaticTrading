"""Episodic context builder — inject macro regime, failures, track record, and lessons into persona runs.

spec_ref: Architecture.md §1.13, Agents.md §17
"""
from __future__ import annotations

import hashlib
from typing import Any


def build_context_brief(
    persona: str,
    ticker: str,
    regime: str = "UNCERTAIN",
    regime_confidence: float = 0.0,
    recent_failures: list[dict] | None = None,
    persona_brier: float | None = None,
    persona_cycle_count: int = 0,
    recent_lessons: list[str] | None = None,
    affinity_data: dict[str, Any] | None = None,
    fde_history: list[dict] | None = None,
) -> str:
    """Build a ~200-word context brief for a persona run.

    Injects macro regime, recent failures, track record, lessons,
    persona-ticker affinity from DuckDB, and FDE failure history.

    Parameters
    ----------
    persona : str
        Target persona name.
    ticker : str
        Ticker being analysed.
    regime : str
        Current macro regime label.
    regime_confidence : float
        Confidence in the regime classification [0, 1].
    recent_failures : list[dict] | None
        Recent FailedAssumption entries (``taxonomy``, ``summary`` keys).
    persona_brier : float | None
        This persona's average Brier score on *ticker*.
    persona_cycle_count : int
        Number of cycles this persona has run on *ticker*.
    recent_lessons : list[str] | None
        Recent lesson texts relevant to the ticker.
    affinity_data : dict | None
        Row from ``persona_ticker_affinity`` with ``avg_brier`` and
        ``cycle_count`` keys.  When provided, overrides
        *persona_brier* / *persona_cycle_count*.
    fde_history : list[dict] | None
        Recent FDE failure classifications with ``taxonomy`` and
        ``severity`` keys.

    Returns
    -------
    str
        Truncated to ≤200 words.
    """
    sections: list[str] = []

    # --- Macro regime (always present) ---
    sections.append(
        f"MACRO CONTEXT: Current regime is {regime} (confidence {regime_confidence:.0%})."
    )

    # --- Recent failures (from holding resolution) ---
    if recent_failures:
        failure_strs = [
            f"{f.get('taxonomy', 'UNKNOWN')}: {f.get('summary', '')}" for f in recent_failures[:3]
        ]
        sections.append("RECENT FAILURES: " + "; ".join(failure_strs))

    # --- Persona-ticker affinity (DuckDB persona_ticker_affinity) ---
    # If affinity_data provided, prefer it over raw brier/cycle_count.
    eff_brier: float | None = persona_brier
    eff_cycles: int = persona_cycle_count
    if affinity_data is not None:
        eff_brier = affinity_data.get("avg_brier", persona_brier)
        eff_cycles = int(affinity_data.get("cycle_count", persona_cycle_count))

    if eff_brier is not None and eff_cycles >= 5:
        sections.append(
            f"YOUR TRACK RECORD ON {ticker}: avg Brier {eff_brier:.3f} "
            f"over {eff_cycles} cycles."
        )

    # --- FDE failure history ---
    if fde_history:
        fde_strs = [
            f"{h.get('taxonomy', 'UNKNOWN')} (sev {h.get('severity', 0.0):.1f})"
            for h in fde_history[:3]
        ]
        sections.append("FAILURE PATTERNS: " + "; ".join(fde_strs))

    # --- Lessons ---
    if recent_lessons:
        sections.append("RELEVANT PAST LESSONS: " + "; ".join(recent_lessons[:2]))

    brief = " ".join(sections)
    words = brief.split()
    if len(words) > 200:
        brief = " ".join(words[:200]) + "..."

    return brief


def inject_and_log(
    persona: str,
    ticker: str,
    cycle_id: str,
    **kwargs: Any,
) -> tuple[str, str]:
    """Build context brief and log audit event.

    Returns
    -------
    tuple[str, str]
        ``(brief, content_hash)`` — the brief text and its SHA-256 hex
        digest for audit chain verification.
    """
    brief = build_context_brief(persona=persona, ticker=ticker, **kwargs)
    content_hash = hashlib.sha256(brief.encode()).hexdigest()

    # Lazy import to avoid circular dependency at module level.
    from pmacs.logsys.debug_log import log_debug

    log_debug(
        "episodic_context_injected",
        payload={
            "content_hash": content_hash,
            "persona": persona,
            "ticker": ticker,
            "cycle_id": cycle_id,
            "word_count": len(brief.split()),
        },
        level="INFO",
        cycle_id=cycle_id,
    )

    return brief, content_hash
