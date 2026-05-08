"""MacroRegime sanity validator (Agents.md §3).

Persona-specific checks beyond the shared base:
- regime_confidence > 0.5 when regime is not UNCERTAIN
- p_up/p_flat/p_down are non-degenerate (not all equal)
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class MacroRegimeSanity(BaseSanityValidator):
    """Sanity validator for MacroRegime persona outputs."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        # regime_confidence > 0.5 when regime is not UNCERTAIN
        regime = output.get("regime", "")
        confidence = output.get("regime_confidence", 0.0)
        if regime != "UNCERTAIN" and confidence <= 0.5:
            return SanityResult(
                passed=False,
                reason=f"regime_confidence {confidence} <= 0.5 for non-UNCERTAIN regime '{regime}'",
            )

        # Non-degenerate probability distribution
        p_up = output.get("p_up", 0.0)
        p_flat = output.get("p_flat", 0.0)
        p_down = output.get("p_down", 0.0)
        if p_up == p_flat == p_down:
            return SanityResult(
                passed=False,
                reason="degenerate distribution: p_up == p_flat == p_down",
            )

        return SanityResult(passed=True)
