"""CrossPersonaAuditor persona runner — second-wave synthesis auditor (Agents.md §11d).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

Wave-2: runs AFTER the 7 wave-1 personas have committed (frozen outputs, §14.4).
This is the ONLY agent that checks whether each persona's *conclusion follows
from the evidence it cited*. It NEVER produces a directional probability — it
emits structured AuditorFlags only (Five Non-Negotiable #2: LLMs never math).
The orchestrator consumes flags deterministically: weight caps, Crucible-brief
injection, FDE writes (Agents.md §11d.6).

spec_ref: Agents.md §11d
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.agents._peer_render import (
    _PERSONA_NARRATIVE_FIELDS,
    _UNIVERSAL_NARRATIVE_FIELDS,
    _aggregate_nested_narrative,
    extract_narrative,
)
from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


# Re-export for backward compatibility with existing imports
# (tests/unit/test_auditor_flags.py imports these symbols from this module).
__all__ = [
    "CrossPersonaAuditorRunner",
    "_PERSONA_NARRATIVE_FIELDS",
    "_UNIVERSAL_NARRATIVE_FIELDS",
    "_aggregate_nested_narrative",
    "_render_peer_outputs",
]


class CrossPersonaAuditorRunner(PersonaRunner):
    """Cross-persona audit layer.

    Audits wave-1 persona outputs for reasoning integrity (citation gaps,
    unsupported conclusions, conflicting conclusions, number misuse,
    hallucinated evidence). Emits an AuditorOutput (flags list, summary) — no
    probabilities.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="cross_persona_auditor",
            grammar_name="cross_persona_auditor",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )
        self._peer_outputs_text: str = ""

    def set_peer_outputs(self, persona_outputs: list[Any]) -> None:
        """Inject the frozen wave-1 persona outputs to audit."""
        self._peer_outputs_text = _render_peer_outputs(persona_outputs)

    def get_pydantic_model(self):
        from pmacs.schemas.personas import AuditorOutput
        return AuditorOutput

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """CrossPersonaAuditor drift fixes (deepseek-v4-flash on openrouter).

        - ``flags[i].description`` may exceed max_length; truncated.
        - ``flags[i].cycle_id`` may be emitted as int by the LLM; coerce to str.
        - Top-level ``summary`` (max_length=600) may exceed; truncated.
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        # Coerce flags[i].cycle_id to str if int
        flags = parsed.get("flags", [])
        if isinstance(flags, list):
            for i, f in enumerate(flags):
                if isinstance(f, dict) and "cycle_id" in f:
                    if not isinstance(f["cycle_id"], str):
                        old_val = f["cycle_id"]
                        f["cycle_id"] = str(f["cycle_id"])
                        all_fixes.append({
                            "field": f"flags[{i}].cycle_id",
                            "type": "type_coerced",
                            "before": type(old_val).__name__,
                            "after": "str",
                        })

        parsed, fixes = self._truncate_string_fields(parsed, model_cls)
        all_fixes.extend(fixes)

        self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

    def get_sanity_validator(self):
        from pmacs.agents.sanity.cross_persona_auditor import CrossPersonaAuditorSanity
        return CrossPersonaAuditorSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "cross_persona_auditor.md"
        template = template_path.read_text(encoding="utf-8")

        evidence_text = self.format_evidence_for_prompt(evidence)
        ticker = evidence[0].ticker if evidence else ""

        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"

        peer_block = self._peer_outputs_text or "(no wave-1 persona outputs available)"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        return (
            template
            .replace("{today_date}", today)
            .replace("{ticker}", ticker)
            .replace("{peer_outputs}", peer_block)
            .replace("{evidence}", evidence_text)
            .replace("{episodic_context}", context_block)
        )


def _render_peer_outputs(persona_outputs: list[Any]) -> str:
    """Render wave-1 persona outputs for auditing.

    The auditor needs each persona's name, probabilities, reasoning, AND the
    evidence_ids it cited — so it can check whether the conclusion follows from
    those cited packets and whether two personas conflict on the same packet.
    """
    if not persona_outputs:
        return ""

    lines: list[str] = []
    for po in persona_outputs:
        persona = getattr(po, "persona", None)
        name = getattr(persona, "value", str(persona) if persona else "?")

        body: dict[str, Any] = {}
        raw = getattr(po, "raw_output", None)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
            except (json.JSONDecodeError, TypeError):
                body = {}

        pu = body.get("p_up", getattr(po, "p_up", None))
        pf = body.get("p_flat", getattr(po, "p_flat", None))
        pd = body.get("p_down", getattr(po, "p_down", None))
        cited = body.get("evidence_ids", []) or []
        # Shared helper walks the persona-specific cascade, falling back to
        # the universal cascade. Returns "" when no narrative matches.
        persona_key = name if isinstance(name, str) else ""
        reasoning = extract_narrative(persona_key, body)

        prob_str = (
            f"p_up={pu:.3f} p_flat={pf:.3f} p_down={pd:.3f}"
            if all(isinstance(v, (int, float)) for v in (pu, pf, pd))
            else "(probabilities unavailable)"
        )
        lines.append(f"### {name}")
        lines.append(f"  {prob_str}")
        lines.append(f"  cited evidence_ids: {cited}")
        if reasoning:
            lines.append(f"  reasoning: {str(reasoning)[:500]}")

    return "\n".join(lines) if lines else ""
