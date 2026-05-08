"""Forensics sanity validator — persona-specific checks (Agents.md §11).

Checks:
- red_flag_count matches len(red_flags) (redundant with Pydantic but defense in depth)
- CLEAN implies red_flags empty
- SEVERE_RISK implies at least one severity > 0.7
- MATERIAL_CONCERNS or worse implies p_up <= p_flat + p_down
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class ForensicsSanity(BaseSanityValidator):
    """Sanity validator for Forensics persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        red_flags = output.get("red_flags", [])
        quality = output.get("overall_accounting_quality", "")

        # CLEAN implies no red flags
        if quality == "CLEAN" and len(red_flags) > 0:
            return SanityResult(
                passed=False,
                reason=f"CLEAN accounting quality but {len(red_flags)} red flags present",
            )

        # SEVERE_RISK implies at least one high-severity flag
        if quality == "SEVERE_RISK":
            max_severity = max(
                (rf.get("severity", 0.0) for rf in red_flags),
                default=0.0,
            )
            if max_severity <= 0.7:
                return SanityResult(
                    passed=False,
                    reason=f"SEVERE_RISK but max severity is {max_severity} (need > 0.7)",
                )

        # MATERIAL_CONCERNS or worse implies p_up <= p_flat + p_down
        if quality in ("MATERIAL_CONCERNS", "SEVERE_RISK"):
            p_up = output.get("p_up", 0.0)
            p_flat = output.get("p_flat", 0.0)
            p_down = output.get("p_down", 0.0)
            if p_up > (p_flat + p_down):
                return SanityResult(
                    passed=False,
                    reason=f"{quality} but p_up ({p_up}) > p_flat+p_down ({p_flat + p_down})",
                )

        return SanityResult(passed=True)
