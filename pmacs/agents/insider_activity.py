"""InsiderActivity persona runner — insider trading pattern analysis (Agents.md §9).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §9
"""

from __future__ import annotations

from pathlib import Path

from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class InsiderActivityRunner(PersonaRunner):
    """Insider activity analyst persona.

    Detects meaningful insider trading patterns from Form 4 filings.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
    ) -> None:
        super().__init__(
            persona_name="insider_activity",
            grammar_name="insider_activity",
            temperature=0.2,
            max_tokens=1024,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import InsiderActivityOutput
        return InsiderActivityOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.insider_activity import InsiderActivitySanity
        return InsiderActivitySanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "insider_activity.md"
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
