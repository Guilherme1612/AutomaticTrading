"""Base sanity validator for persona outputs (Agents.md §3).

Three-layer contract: Grammar → Pydantic → Sanity.
Subclasses add persona-specific checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SanityResult:
    """Result of sanity validation."""
    passed: bool
    reason: str | None = None


PROB_SUM_TOLERANCE = 0.05


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
        # Common check: reasoning must be non-empty if present
        # Not all personas have a reasoning field (e.g. Crucible, MemoWriter)
        reasoning = output.get("reasoning", None)
        if reasoning is not None and not reasoning.strip():
            return SanityResult(passed=False, reason="reasoning field is empty")

        # Common check: key_signal must be non-empty and contain a quantitative element
        key_signal = output.get("key_signal", None)
        if key_signal is not None:
            if not key_signal.strip():
                return SanityResult(passed=False, reason="key_signal field is empty")
            # Must contain at least one number, percentage, or currency symbol
            if not re.search(r"[\d%$€£]", key_signal):
                return SanityResult(
                    passed=False,
                    reason="key_signal must contain at least one quantitative element (number, %, $)",
                )

        # Common check: analysis must be non-empty and contain a specific number
        analysis = output.get("analysis", None)
        if analysis is not None:
            if not analysis.strip():
                return SanityResult(passed=False, reason="analysis field is empty")
            if not re.search(r"\d", analysis):
                return SanityResult(
                    passed=False,
                    reason="analysis must include at least one specific number from evidence",
                )

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

        # Common check: probability sum ≈ 1.0 (defense-in-depth)
        p_keys = ("p_up", "p_flat", "p_down")
        if all(k in output for k in p_keys):
            total = output["p_up"] + output["p_flat"] + output["p_down"]
            if abs(total - 1.0) > PROB_SUM_TOLERANCE:
                return SanityResult(
                    passed=False,
                    reason=f"probabilities sum to {total:.4f}, expected ~1.0 (tolerance {PROB_SUM_TOLERANCE})",
                )

        # Persona-specific checks
        return self._persona_checks(output, evidence)

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        """Override in subclasses for persona-specific validation."""
        return SanityResult(passed=True)
