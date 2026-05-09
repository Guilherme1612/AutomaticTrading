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
            max_tokens=1024,
        )

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
