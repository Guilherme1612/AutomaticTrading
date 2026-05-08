"""Crucible sanity validator — persona-specific checks (Agents.md §14).

Checks:
- severity matches max attack severity (within 0.05)
- thesis_survives consistent with severity vs 0.6 threshold
- attack_count matches len(attacks)
- No duplicate attacks (same type + same description)
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class CrucibleSanity(BaseSanityValidator):
    """Sanity validator for Crucible persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        attacks = output.get("attacks", [])
        severity = output.get("severity", 0.0)
        attack_count = output.get("attack_count", 0)
        thesis_survives = output.get("thesis_survives", True)

        # severity must match max attack severity
        if attacks:
            max_sev = max(a.get("severity", 0.0) for a in attacks)
            if abs(max_sev - severity) > 0.05:
                return SanityResult(
                    passed=False,
                    reason=f"severity ({severity}) != max attack severity ({max_sev})",
                )

        # attack_count must match len(attacks)
        if attack_count != len(attacks):
            return SanityResult(
                passed=False,
                reason=f"attack_count ({attack_count}) != len(attacks) ({len(attacks)})",
            )

        # thesis_survives must be consistent with severity vs 0.6
        if thesis_survives and severity > 0.6:
            return SanityResult(
                passed=False,
                reason=f"thesis_survives=True but severity ({severity}) > 0.6",
            )
        if not thesis_survives and severity < 0.6:
            return SanityResult(
                passed=False,
                reason=f"thesis_survives=False but severity ({severity}) < 0.6",
            )

        # No duplicate attacks (same type + same description)
        seen: set[tuple[str, str]] = set()
        for attack in attacks:
            key = (attack.get("attack_type", ""), attack.get("description", ""))
            if key in seen:
                return SanityResult(
                    passed=False,
                    reason=f"duplicate attack: type={key[0]}, description={key[1][:50]}",
                )
            seen.add(key)

        return SanityResult(passed=True)
