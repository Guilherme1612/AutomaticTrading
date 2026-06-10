"""Crucible persona runner — adversarial thesis attacker (Agents.md §14).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §12
"""

from __future__ import annotations

from pathlib import Path

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class CrucibleRunner(PersonaRunner):
    """Adversarial thesis attacker persona.

    Finds flaws in investment theses: logical holes, citation gaps,
    counterarguments, overlooked risks, and base rate neglect.
    """

    def __init__(self) -> None:
        super().__init__(
            persona_name="crucible",
            grammar_name="crucible",
            temperature=0.1,  # Lower than other personas
            max_tokens=768,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import CrucibleOutput
        return CrucibleOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.crucible import CrucibleSanity
        return CrucibleSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "crucible.md"
        template = template_path.read_text(encoding="utf-8")

        evidence_text = self.format_evidence_for_prompt(evidence)

        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"

        from datetime import date
        today = date.today().isoformat()

        return template.replace(
            "{evidence}", evidence_text
        ).replace(
            "{episodic_context}", context_block
        ).replace(
            "{today_date}", today
        )
