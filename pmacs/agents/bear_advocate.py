"""BearAdvocate persona runner — second-wave bear-side advocate (Agents.md §11c).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

Wave-2: runs AFTER the 7 wave-1 personas have committed (frozen outputs, §14.4)
and BEFORE Arbitration, entering the pool as a normal voter. Mirror of
BullAdvocate — argues the bear case the consensus under-weighted.

spec_ref: Agents.md §11c
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class BearAdvocateRunner(PersonaRunner):
    """Bear-side advocate persona.

    Argues the bear case the wave-1 consensus under-weighted. Emits a
    DirectionalProbability plus the wave-1 persona it is pushing against.
    Advocacy is not fabrication — if the evidence supports the bull case it
    emits a near-uniform distribution.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="bear_advocate",
            grammar_name="bear_advocate",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )
        self._peer_outputs_text: str = ""

    def set_peer_outputs(self, persona_outputs: list[Any]) -> None:
        """Inject the frozen wave-1 persona outputs so build_prompt can show them."""
        self._peer_outputs_text = _render_peer_outputs(persona_outputs)

    def get_pydantic_model(self):
        from pmacs.schemas.personas import BearAdvocateOutput
        return BearAdvocateOutput

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """BearAdvocate drift fixes (deepseek-v4-flash on openrouter).

        - ``reasoning`` (max_length=600) and ``strongest_bull_counterpoint``
          (max_length=300) frequently exceed their limits.
        - Top-level ``evidence_ids`` may be empty — padded.
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        parsed, fixes = self._truncate_string_fields(parsed, model_cls)
        all_fixes.extend(fixes)

        parsed, fixes = self._ensure_min_evidence_ids(parsed, model_cls)
        all_fixes.extend(fixes)

        self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

    def get_sanity_validator(self):
        from pmacs.agents.sanity.bear_advocate import BearAdvocateSanity
        return BearAdvocateSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "bear_advocate.md"
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
    """Render wave-1 persona outputs as a compact, agent-readable block."""
    if not persona_outputs:
        return ""

    lines: list[str] = []
    for po in persona_outputs:
        persona = getattr(po, "persona", None)
        name = getattr(persona, "value", str(persona) if persona else "?")
        pu = getattr(po, "p_up", None)
        pf = getattr(po, "p_flat", None)
        pd = getattr(po, "p_down", None)

        excerpt = ""
        raw = getattr(po, "raw_output", None)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    excerpt = (
                        parsed.get("reasoning")
                        or parsed.get("key_signal")
                        or parsed.get("analysis")
                        or parsed.get("growth_durability_reasoning")
                        or parsed.get("signal_reasoning")
                        or ""
                    )
            except (json.JSONDecodeError, TypeError):
                excerpt = ""

        prob_str = (
            f"p_up={pu:.3f} p_flat={pf:.3f} p_down={pd:.3f}"
            if all(v is not None for v in (pu, pf, pd))
            else "(probabilities unavailable)"
        )
        lines.append(f"- {name}: {prob_str}")
        if excerpt:
            lines.append(f"    reasoning: {str(excerpt)[:300]}")

    return "\n".join(lines) if lines else ""
