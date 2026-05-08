"""MoatAnalyst sanity validator (Agents.md §3).

Persona-specific checks:
- moat_strength within 0.15 of component average
- no duplicate moat_types
- if competitive_entry_risk HIGH, moat_strength < 0.7
- Non-degenerate probability distribution
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class MoatAnalystSanity(BaseSanityValidator):
    """Sanity validator for MoatAnalyst persona outputs."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        components = output.get("moat_components", [])

        # No duplicate moat types
        types = [c.get("moat_type") for c in components]
        if len(types) != len(set(types)):
            return SanityResult(
                passed=False,
                reason=f"duplicate moat_type found: {types}",
            )

        # moat_strength within 0.15 of component average
        if components:
            strengths = [c.get("strength", 0.0) for c in components]
            avg = sum(strengths) / len(strengths)
            moat_strength = output.get("moat_strength", 0.0)
            if abs(moat_strength - avg) > 0.15:
                return SanityResult(
                    passed=False,
                    reason=(
                        f"moat_strength ({moat_strength}) is more than 0.15 from "
                        f"component average ({avg:.3f})"
                    ),
                )

        # High competitive entry risk implies lower moat strength
        risk = output.get("competitive_entry_risk", "")
        moat_strength = output.get("moat_strength", 0.0)
        if risk == "HIGH" and moat_strength >= 0.7:
            return SanityResult(
                passed=False,
                reason=f"competitive_entry_risk HIGH but moat_strength {moat_strength} >= 0.7",
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
