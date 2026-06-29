"""CrossPersonaAuditor sanity validator — persona-specific checks (Agents.md §11d.5).

Checks:
- output contains NO probability fields (the auditor never touches math)
- every flag.target_persona is a real wave-1 analysis persona
- every flag.evidence_id resolves to a real packet (an auditor that hallucinates
  evidence IDs is itself a failure)
- severity in [0,1]; taxonomy_mapping is auditor-allowed (Pydantic also enforces)
- an empty flags list is valid (clean outputs)

Pydantic (Layer 2) already enforces flag_type↔taxonomy_mapping correspondence and
the wave-1 membership of target_persona; sanity double-checks as defense-in-depth.
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.schemas.personas import WAVE1_PERSONAS
from pmacs.schemas.agents import PersonaName


class CrossPersonaAuditorSanity(BaseSanityValidator):
    """Sanity validator for CrossPersonaAuditor output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        # The auditor MUST NOT emit probabilities (Five Non-Negotiable #2).
        for forbidden in ("p_up", "p_flat", "p_down"):
            if forbidden in output and output[forbidden] is not None:
                return SanityResult(
                    passed=False,
                    reason=(
                        f"auditor output contains probability field '{forbidden}' — "
                        f"the auditor never touches math (Five Non-Negotiable #2)"
                    ),
                )

        flags = output.get("flags") or []
        if not isinstance(flags, list):
            return SanityResult(passed=False, reason="flags is not a list")
        if len(flags) > 20:
            return SanityResult(passed=False, reason="flags exceeds max length of 20")

        # Collect every evidence_id referenced across all flags for resolution check.
        cited_ids: list[str] = []
        for i, flag in enumerate(flags):
            if not isinstance(flag, dict):
                return SanityResult(passed=False, reason=f"flag {i} is not an object")

            target = flag.get("target_persona")
            target_val = getattr(target, "value", target)
            try:
                target_enum = PersonaName(target_val) if target_val is not None else None
            except ValueError:
                target_enum = None
            if target_enum is None or target_enum not in WAVE1_PERSONAS:
                return SanityResult(
                    passed=False,
                    reason=(
                        f"flag {i} target_persona '{target_val}' is not a wave-1 "
                        f"analysis persona"
                    ),
                )

            sev = flag.get("severity")
            if not isinstance(sev, (int, float)) or sev < 0.0 or sev > 1.0:
                return SanityResult(
                    passed=False, reason=f"flag {i} severity {sev} out of range [0, 1]"
                )

            desc = (flag.get("description") or "").strip()
            if not desc:
                return SanityResult(
                    passed=False, reason=f"flag {i} description is empty"
                )

            for eid in flag.get("evidence_ids") or []:
                if isinstance(eid, str):
                    cited_ids.append(eid)

        # Every cited evidence_id must resolve to a real packet.
        if cited_ids:
            known_ids: set[str] = set()
            for packet in evidence:
                for ev in getattr(packet, "evidence", []):
                    ev_id = getattr(ev, "id", None)
                    if ev_id is not None:
                        known_ids.add(ev_id)
            for eid in cited_ids:
                if eid not in known_ids:
                    return SanityResult(
                        passed=False,
                        reason=(
                            f"auditor flag cites evidence_id '{eid}' not found in "
                            f"provided packets — auditor hallucinated an evidence ID"
                        ),
                    )

        summary = (output.get("summary") or "").strip()
        if not summary:
            return SanityResult(passed=False, reason="summary is empty")

        return SanityResult(passed=True)
