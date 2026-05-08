"""Crucible adversarial inner state machine — Agents.md §16.

Two rewrite cycles maximum, 90 s budget per cycle, severity-based routing:
  - < 0.3  → DONE   (thesis strong)
  - 0.3–0.6 → REWRITE (second attempt)
  - >= 0.6  → ABORT  (NO_TRADE)

Budget exceeded at any point → ABORT (NO_TRADE).

spec_ref: Agents.md §16
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class CrucibleLoopState(str, Enum):
    """Inner state machine states for the Crucible adversarial loop."""

    INITIAL = "INITIAL"
    CYCLE_1 = "CYCLE_1"
    REWRITE = "REWRITE"
    CYCLE_2 = "CYCLE_2"
    DONE = "DONE"
    ABORT = "ABORT"


@dataclass
class CrucibleLoopResult:
    """Result of the Crucible adversarial loop."""

    final_state: CrucibleLoopState
    final_severity: float
    cycles_used: int
    reason: str
    outputs: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants (Agents.md §16)
# ---------------------------------------------------------------------------

MAX_CYCLES: int = 2
BUDGET_PER_CYCLE_S: float = 90.0
SEVERITY_THRESHOLD_SKIP: float = 0.6
SEVERITY_THRESHOLD_REWRITE: float = 0.3


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def run_crucible_loop(
    run_crucible_fn: Callable[[list, int], Any],
    evidence: list,
    budget_total_s: float = MAX_CYCLES * BUDGET_PER_CYCLE_S,
) -> CrucibleLoopResult:
    """Run the Crucible adversarial loop.

    Parameters
    ----------
    run_crucible_fn : callable
        ``(evidence, cycle_number) -> output`` where *output* is a
        dict-like with a ``severity`` field, or ``None`` on budget
        exhaust.
    evidence : list
        Evidence packets to pass to the crucible runner.
    budget_total_s : float
        Total wall-clock budget in seconds (default 180 s = 2 x 90 s).

    Returns
    -------
    CrucibleLoopResult
    """
    start_time = time.time()
    outputs: list[Any] = []

    # ------------------------------------------------------------------
    # Cycle 1
    # ------------------------------------------------------------------
    output_1 = run_crucible_fn(evidence, 1)

    if output_1 is None or (time.time() - start_time) > budget_total_s:
        return CrucibleLoopResult(
            final_state=CrucibleLoopState.ABORT,
            final_severity=1.0,
            cycles_used=1,
            reason="Budget exceeded in cycle 1",
            outputs=outputs,
        )

    severity_1 = _extract_severity(output_1)
    outputs.append(output_1)

    # --- Severity routing after cycle 1 ---
    if severity_1 < SEVERITY_THRESHOLD_REWRITE:
        return CrucibleLoopResult(
            final_state=CrucibleLoopState.DONE,
            final_severity=severity_1,
            cycles_used=1,
            reason=f"Low severity ({severity_1:.2f}); thesis strong",
            outputs=outputs,
        )

    if severity_1 >= SEVERITY_THRESHOLD_SKIP:
        return CrucibleLoopResult(
            final_state=CrucibleLoopState.ABORT,
            final_severity=severity_1,
            cycles_used=1,
            reason=f"High severity ({severity_1:.2f}) in cycle 1; NO_TRADE",
            outputs=outputs,
        )

    # ------------------------------------------------------------------
    # Cycle 2 (rewrite) — only reached for 0.3 <= severity < 0.6
    # ------------------------------------------------------------------
    remaining_budget = budget_total_s - (time.time() - start_time)
    if remaining_budget <= 0:
        return CrucibleLoopResult(
            final_state=CrucibleLoopState.ABORT,
            final_severity=severity_1,
            cycles_used=1,
            reason="Budget exhausted before cycle 2",
            outputs=outputs,
        )

    output_2 = run_crucible_fn(evidence, 2)

    if output_2 is None:
        return CrucibleLoopResult(
            final_state=CrucibleLoopState.ABORT,
            final_severity=severity_1,
            cycles_used=2,
            reason="Budget exceeded in cycle 2",
            outputs=outputs,
        )

    severity_2 = _extract_severity(output_2)
    outputs.append(output_2)

    if severity_2 < SEVERITY_THRESHOLD_SKIP:
        return CrucibleLoopResult(
            final_state=CrucibleLoopState.DONE,
            final_severity=severity_2,
            cycles_used=2,
            reason=f"Severity reduced to {severity_2:.2f} after rewrite; thesis survived",
            outputs=outputs,
        )

    return CrucibleLoopResult(
        final_state=CrucibleLoopState.ABORT,
        final_severity=severity_2,
        cycles_used=2,
        reason=f"Severity still high ({severity_2:.2f}) after rewrite; NO_TRADE",
        outputs=outputs,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_severity(output: Any) -> float:
    """Extract severity from a dict-like or attribute-based output."""
    if isinstance(output, dict):
        return float(output.get("severity", 1.0))
    return float(getattr(output, "severity", 1.0))
