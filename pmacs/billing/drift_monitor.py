"""Drift monitor — detect estimate drift in per-persona output tokens.

PRD §6.3: Every 100 calls per persona, compare observed p90 vs configured
PERSONA_EXPECTED_OUTPUT_TOKENS. Warn if drift > 20%.
"""

from __future__ import annotations

from pmacs.logsys import log_debug
from pmacs.schemas.billing import PERSONA_EXPECTED_OUTPUT_TOKENS


def check_estimate_drift(persona: str, duckdb_adapter) -> None:
    """Check if persona's actual output tokens have drifted from estimates.

    Queries last 100 calls for the persona, computes p90 of completion_tokens,
    compares against PERSONA_EXPECTED_OUTPUT_TOKENS. Logs ESTIMATE_DRIFT if
    observed p90 exceeds configured by > 20%.

    This is lightweight — only call periodically (e.g., every 100 calls per persona).
    """
    configured = PERSONA_EXPECTED_OUTPUT_TOKENS.get(persona)
    if configured is None:
        return

    rows = duckdb_adapter.execute(
        "SELECT completion_tokens FROM api_usage "
        "WHERE persona = ? ORDER BY called_at DESC LIMIT 100",
        [persona],
    )
    if not rows or len(rows) < 20:
        return  # Not enough data yet

    tokens = sorted(r["completion_tokens"] for r in rows)
    p90_index = int(len(tokens) * 0.9)
    observed_p90 = tokens[min(p90_index, len(tokens) - 1)]

    drift_ratio = observed_p90 / configured if configured > 0 else 0
    if drift_ratio > 1.20:
        log_debug(
            "ESTIMATE_DRIFT",
            payload={
                "persona": persona,
                "configured": configured,
                "observed_p90": observed_p90,
                "delta_pct": round((drift_ratio - 1.0) * 100, 1),
            },
            level="WARN",
            error_code="ESTIMATE_DRIFT",
            msg=f"Estimate drift: {persona} p90={observed_p90} vs configured={configured} ({(drift_ratio-1)*100:.0f}% over)",
        )
