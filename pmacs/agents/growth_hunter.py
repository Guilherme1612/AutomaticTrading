"""GrowthHunter persona runner — growth equity analysis (Agents.md §8).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §8
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class GrowthHunterRunner(PersonaRunner):
    """Growth equity analyst persona.

    Assesses revenue trajectory, TAM penetration, unit economics,
    and growth durability for a given ticker.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
    ) -> None:
        super().__init__(
            persona_name="growth_hunter",
            grammar_name="growth_hunter",
            temperature=0.2,
            max_tokens=512,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import GrowthHunterOutput
        return GrowthHunterOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.growth_hunter import GrowthHunterSanity
        return GrowthHunterSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        prompt_path = (
            __file__.replace(".py", "")
            .rsplit("/", 1)[0]
            .replace("/agents", "/agents/prompts/growth_hunter.md")
        )
        # Use Path-based loading for the prompt template
        from pathlib import Path

        template_path = Path(__file__).parent / "prompts" / "growth_hunter.md"
        template = template_path.read_text(encoding="utf-8")

        # Build evidence text
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
