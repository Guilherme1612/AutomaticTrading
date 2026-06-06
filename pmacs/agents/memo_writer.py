"""MemoWriter persona runner — operator-facing memo synthesis (Agents.md §15).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §13
"""

from __future__ import annotations

from pathlib import Path

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class MemoWriterRunner(PersonaRunner):
    """Operator-facing memo writer persona.

    Synthesizes outputs from 7 analysts, Crucible, arbitration,
    conviction, and verdict into a readable investment memo.
    """

    def __init__(self) -> None:
        super().__init__(
            persona_name="memo_writer",
            grammar_name="memo_writer",
            temperature=0.3,  # Highest temperature: analysis=0.2, Crucible=0.1
            max_tokens=5120,
        )
        self._analytical_context: str = ""

    def set_analytical_context(
        self,
        *,
        arbitrated: object | None = None,
        verdict: object | None = None,
        conviction_score: float | None = None,
        crucible_severity: float | None = None,
        persona_outputs: list | None = None,
    ) -> None:
        """Inject the full analytical synthesis so the memo has real numbers.

        Called by the orchestrator before run() so build_prompt can include
        per-persona signals, arbitration probabilities, and verdict.
        """
        lines: list[str] = []

        if arbitrated is not None:
            p_up = getattr(arbitrated, "p_up", None)
            p_flat = getattr(arbitrated, "p_flat", None)
            p_down = getattr(arbitrated, "p_down", None)
            decision = getattr(arbitrated, "decision", None)
            lines.append("## Arbitration Result")
            if p_up is not None and p_flat is not None and p_down is not None:
                lines.append(
                    f"p_up={p_up:.3f}  p_flat={p_flat:.3f}  p_down={p_down:.3f}"
                    f"  decision={getattr(decision, 'value', decision)}"
                )
            else:
                lines.append(f"decision={getattr(decision, 'value', decision)} (probabilities unavailable)")

        if conviction_score is not None:
            verdict_str = getattr(verdict, "value", str(verdict)) if verdict else "?"
            lines.append(f"\n## Conviction")
            lines.append(f"score={conviction_score:.4f}  verdict={verdict_str}")

        if crucible_severity is not None:
            lines.append(f"\n## Crucible")
            lines.append(f"severity={crucible_severity:.3f}")

        if persona_outputs:
            lines.append("\n## Persona Signals")
            for dp in persona_outputs:
                persona = getattr(dp, "persona", "?")
                name = getattr(persona, "value", str(persona))
                pu = getattr(dp, "p_up", 0.0)
                pf = getattr(dp, "p_flat", 0.0)
                pd = getattr(dp, "p_down", 0.0)
                direction = "UP" if pu > pd and pu > pf else ("DOWN" if pd > pu and pd > pf else "FLAT")
                lines.append(
                    f"  {name:22s}: p_up={pu:.3f} p_flat={pf:.3f} p_down={pd:.3f}  → {direction}"
                )

        self._analytical_context = "\n".join(lines)

    def get_pydantic_model(self):
        from pmacs.schemas.personas import MemoWriterOutput
        return MemoWriterOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.memo_writer import MemoWriterSanity
        return MemoWriterSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "memo_writer.md"
        template = template_path.read_text(encoding="utf-8")

        evidence_text = self.format_evidence_for_prompt(evidence)

        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"
        if self._analytical_context:
            context_block += f"\n{self._analytical_context}"

        return template.replace(
            "{evidence}", evidence_text
        ).replace(
            "{episodic_context}", context_block
        )
