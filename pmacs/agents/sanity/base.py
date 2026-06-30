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
    # Populated by the evidence_ids normalization step. Each entry is
    # ``{"field": "<path>", "from": "<hallucinated_id>", "to": "normalized-fallback-NNN"}``.
    # Empty when no hallucinated IDs were detected. The orchestrator surfaces
    # these in the cycle's audit chain so the operator can see exactly which
    # LLM-hallucinated citations were silently substituted (they preserve
    # the persona's real signal but the citation was not real).
    normalized_citations: tuple[dict, ...] = ()


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

        # Common check: evidence_ids reference real packets.
        # Hallucinated IDs (e.g. ``finnhub_ONDS_earnings_history`` for a packet
        # the system never fetched) are STRIPPED and replaced with a synthetic
        # ``normalized-fallback-NNN`` ID. Without this, every cycle whose LLM
        # hallucinates a single citation aborts the persona, falls back to the
        # safe-default simulation output, and the Crucible sees zero signal
        # (ONDS 3-cycle audit Jun 30 produced 239-char Crucible-abort stubs
        # for exactly this reason). The persona's *real* signal (probabilities,
        # reasoning, key_signal) is preserved; only the citation is normalized.
        # Synthetic ``normalized-fallback-XXX`` IDs are accepted as-is (they are
        # the system's own bookkeeping, not LLM-hallucinated).
        #
        # Returns: tuple of (updated_output, normalized_citations).
        # The caller (validate) propagates normalized_citations into the
        # returned SanityResult so the orchestrator can audit-log them.
        evidence_ids = output.get("evidence_ids", [])
        normalized_citations: list[dict] = []
        if evidence_ids:
            known_ids: set[str] = set()
            for packet in evidence:
                for ev in getattr(packet, "evidence", []):
                    ev_id = getattr(ev, "id", None)
                    if ev_id is not None:
                        known_ids.add(ev_id)

            cleaned_ids: list[str] = []
            fallback_counter = 1
            for eid in evidence_ids:
                if eid in known_ids:
                    cleaned_ids.append(eid)
                    continue
                if eid.startswith("normalized-fallback-"):
                    cleaned_ids.append(eid)
                    continue
                # Hallucinated ID — substitute a synthetic one. Audit-log
                # the swap so the operator can see which citations were
                # silently dropped.
                synthetic = f"normalized-fallback-{fallback_counter:03d}"
                fallback_counter += 1
                cleaned_ids.append(synthetic)
                normalized_citations.append({
                    "field": "evidence_ids",
                    "from": eid,
                    "to": synthetic,
                })
            output["evidence_ids"] = cleaned_ids
            # Mutate the caller's dict in place too, so the parsed output the
            # orchestrator stores in PersonaOutput.raw_output carries the
            # normalized citations (not the hallucinated ones).
            if isinstance(output, dict) and "evidence_ids" in output:
                output["evidence_ids"] = cleaned_ids

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
        persona_result = self._persona_checks(output, evidence)
        if normalized_citations:
            return SanityResult(
                passed=persona_result.passed,
                reason=persona_result.reason,
                normalized_citations=tuple(normalized_citations),
            )
        return persona_result

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        """Override in subclasses for persona-specific validation."""
        return SanityResult(passed=True)
