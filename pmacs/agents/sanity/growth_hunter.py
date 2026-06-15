"""GrowthHunter sanity validator — persona-specific checks (Agents.md §8).

Checks:
- revenue_yoy_pct range: -100 to 2000 (if provided; hyper-growth stocks can exceed 500%)
- gross_margin_pct range: -50 to 100 (if provided)
- evidence_ids resolve to provided packets
- growth_durability_reasoning non-empty
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class GrowthHunterSanity(BaseSanityValidator):
    """Sanity validator for GrowthHunter persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        # revenue_yoy_pct range
        rev = output.get("revenue_yoy_pct")
        if rev is not None:
            if rev < -100 or rev > 2000:
                return SanityResult(
                    passed=False,
                    reason=f"revenue_yoy_pct {rev} out of range [-100, 2000]",
                )

        # gross_margin_pct range
        gm = output.get("gross_margin_pct")
        if gm is not None:
            if gm < -50 or gm > 100:
                return SanityResult(
                    passed=False,
                    reason=f"gross_margin_pct {gm} out of range [-50, 100]",
                )

        # growth_durability_reasoning non-empty
        dur_reason = output.get("growth_durability_reasoning", "")
        if not dur_reason or not dur_reason.strip():
            return SanityResult(
                passed=False,
                reason="growth_durability_reasoning is empty",
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
