"""Base sanity validator for persona outputs (Agents.md §3).

Three-layer contract: Grammar → Pydantic → Sanity.
Subclasses add persona-specific checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SanityResult:
    """Result of sanity validation."""
    passed: bool
    reason: str | None = None


class BaseSanityValidator:
    """Abstract sanity validator with common checks.

    Subclasses override _persona_checks() to add persona-specific
    validation beyond the shared base checks.
    """

    def validate(self, output: dict[str, Any], evidence: list[Any]) -> SanityResult:
        """Run all sanity checks on a parsed persona output.

        Args:
            output: Dict from Pydantic model_dump() (already validated by Pydantic).
            evidence: List of EvidencePacket objects with .evidence[].id fields.

        Returns:
            SanityResult with passed=True if all checks pass.
        """
        # Common check: reasoning must be non-empty
        reasoning = output.get("reasoning", "")
        if not reasoning or not reasoning.strip():
            return SanityResult(passed=False, reason="reasoning field is empty")

        # Common check: evidence_ids reference real packets
        evidence_ids = output.get("evidence_ids", [])
        if evidence_ids:
            known_ids: set[str] = set()
            for packet in evidence:
                for ev in getattr(packet, "evidence", []):
                    ev_id = getattr(ev, "id", None)
                    if ev_id is not None:
                        known_ids.add(ev_id)

            for eid in evidence_ids:
                if eid not in known_ids:
                    return SanityResult(
                        passed=False,
                        reason=f"evidence_id '{eid}' not found in provided packets",
                    )

        # Persona-specific checks
        return self._persona_checks(output, evidence)

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        """Override in subclasses for persona-specific validation."""
        return SanityResult(passed=True)
