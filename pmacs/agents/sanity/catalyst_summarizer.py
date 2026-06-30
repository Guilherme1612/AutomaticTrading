"""CatalystSummarizer sanity validator (Agents.md §3).

Persona-specific checks:
- catalyst count <= 10
- expected_date is in the future for PENDING catalysts
- Non-degenerate probability distribution
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class CatalystSummarizerSanity(BaseSanityValidator):
    """Sanity validator for CatalystSummarizer persona outputs."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        catalysts = output.get("catalysts", [])

        # Catalyst count limit
        if len(catalysts) > 10:
            return SanityResult(
                passed=False,
                reason=f"catalyst count {len(catalysts)} exceeds maximum of 10",
            )

        # Validate catalyst evidence_ids reference real packets. Per the new
        # base.py policy (ONDS 3-cycle audit Jun 30 round 2), hallucinated
        # citations are STRIPPED in-place and replaced with a synthetic
        # ``normalized-fallback-NNN`` ID rather than aborting the persona.
        # The persona's real signal (catalyst text, expected_date, status,
        # thesis_impact, probabilities) is preserved so the Crucible and
        # memo writer see actual research, not safe-default stubs.
        known_ids: set[str] = set()
        for packet in evidence:
            for ev in getattr(packet, "evidence", []):
                ev_id = getattr(ev, "id", None)
                if ev_id is not None:
                    known_ids.add(ev_id)

        normalized_citations: list[dict] = []
        for i, cat in enumerate(catalysts):
            cat_evidence_ids = cat.get("evidence_ids", [])
            if not cat_evidence_ids:
                continue
            cleaned: list[str] = []
            fb_counter = 1
            for eid in cat_evidence_ids:
                if eid in known_ids:
                    cleaned.append(eid)
                    continue
                if eid.startswith("normalized-fallback-"):
                    cleaned.append(eid)
                    continue
                synthetic = f"normalized-fallback-{fb_counter:03d}"
                fb_counter += 1
                cleaned.append(synthetic)
                normalized_citations.append({
                    "field": f"catalysts[{i}].evidence_ids",
                    "from": eid,
                    "to": synthetic,
                })
            cat["evidence_ids"] = cleaned

        # expected_date should be in the future for PENDING catalysts
        now = datetime.now(timezone.utc)
        for i, cat in enumerate(catalysts):
            if cat.get("status") == "PENDING" and cat.get("expected_date"):
                try:
                    expected = datetime.fromisoformat(cat["expected_date"])
                    if expected.tzinfo is None:
                        expected = expected.replace(tzinfo=timezone.utc)
                    if expected < now:
                        return SanityResult(
                            passed=False,
                            reason=f"catalyst[{i}] PENDING with expected_date in the past: {cat['expected_date']}",
                        )
                except (ValueError, TypeError):
                    pass  # Malformed date — let it through, Pydantic handles format

        # Non-degenerate probability distribution
        p_up = output.get("p_up", 0.0)
        p_flat = output.get("p_flat", 0.0)
        p_down = output.get("p_down", 0.0)
        if p_up == p_flat == p_down:
            return SanityResult(
                passed=False,
                reason="degenerate distribution: p_up == p_flat == p_down",
            )

        if normalized_citations:
            return SanityResult(
                passed=True,
                normalized_citations=tuple(normalized_citations),
            )
        return SanityResult(passed=True)
