"""Forensics persona runner — forensic accounting analysis (Agents.md §11).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §11
"""

from __future__ import annotations

from pathlib import Path

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class ForensicsRunner(PersonaRunner):
    """Forensic accounting analyst persona.

    Detects red flags in financial statements: revenue quality,
    earnings manipulation, cash flow divergence, and more.
    """

    def __init__(self) -> None:
        super().__init__(
            persona_name="forensics",
            grammar_name="forensics",
            temperature=0.2,
            max_tokens=1024,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import ForensicsOutput
        return ForensicsOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.forensics import ForensicsSanity
        return ForensicsSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "forensics.md"
        template = template_path.read_text(encoding="utf-8")

        evidence_text = ""
        for packet in evidence:
            evidence_text += f"\n--- Evidence ({packet.ticker}) ---\n"
            for ev in getattr(packet, "evidence", []):
                evidence_text += f"[{getattr(ev, 'id', 'unknown')}] {getattr(ev, 'content', str(ev))}\n"

        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"

        return template.replace(
            "{evidence}", evidence_text
        ).replace(
            "{episodic_context}", context_block
        )
