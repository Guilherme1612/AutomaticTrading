"""ShortInterest sanity validator — persona-specific checks (Agents.md §10).

Checks:
- short_pct_float between 0 and 100 (if provided)
- days_to_cover between 0 and 100 (if provided)
- INSUFFICIENT_DATA implies near-uniform probabilities (±0.1 of 0.33)
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class ShortInterestSanity(BaseSanityValidator):
    """Sanity validator for ShortInterest persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        # short_pct_float range
        spf = output.get("short_pct_float")
        if spf is not None:
            if spf < 0 or spf > 100:
                return SanityResult(
                    passed=False,
                    reason=f"short_pct_float {spf} out of range [0, 100]",
                )

        # days_to_cover range
        dtc = output.get("days_to_cover")
        if dtc is not None:
            if dtc < 0 or dtc > 100:
                return SanityResult(
                    passed=False,
                    reason=f"days_to_cover {dtc} out of range [0, 100]",
                )

        # INSUFFICIENT_DATA implies near-uniform probabilities
        anomaly = output.get("anomaly", "")
        if anomaly == "INSUFFICIENT_DATA":
            p_up = output.get("p_up", 0.0)
            p_flat = output.get("p_flat", 0.0)
            p_down = output.get("p_down", 0.0)
            uniform_min = 0.23  # 0.33 - 0.10
            uniform_max = 0.43  # 0.33 + 0.10
            for label, val in [("p_up", p_up), ("p_flat", p_flat), ("p_down", p_down)]:
                if val < uniform_min or val > uniform_max:
                    return SanityResult(
                        passed=False,
                        reason=f"INSUFFICIENT_DATA but {label}={val} outside near-uniform range [{uniform_min}, {uniform_max}]",
                    )

        return SanityResult(passed=True)
