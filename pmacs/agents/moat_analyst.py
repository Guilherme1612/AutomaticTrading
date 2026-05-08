"""MoatAnalyst persona runner — competitive moat assessment (Agents.md §7).

Evaluates a company's competitive moat across up to 6 moat dimensions,
assesses competitive entry risk, and produces directional probabilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


_PROMPT_DIR = Path(__file__).parent / "prompts"


class MoatAnalystRunner(PersonaRunner):
    """Runner for the MoatAnalyst persona.

    Produces MoatAnalystOutput with moat components, competitive entry risk,
    and directional probabilities.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
    ) -> None:
        super().__init__(
            persona_name="moat_analyst",
            grammar_name="moat_analyst",
            temperature=0.2,
            max_tokens=1024,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import MoatAnalystOutput
        return MoatAnalystOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.moat_analyst import MoatAnalystSanity
        return MoatAnalystSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        prompt_path = _PROMPT_DIR / "moat_analyst.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")

        ticker = evidence[0].ticker if evidence else "UNKNOWN"
        evidence_text = self._format_evidence(evidence)
        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"

        return (
            f"{system_prompt}\n\n"
            f"## Ticker: {ticker}\n\n"
            f"## Evidence\n{evidence_text}\n"
            f"{context_block}\n\n"
            f"Respond with valid JSON matching the MoatAnalystOutput schema."
        )

    @staticmethod
    def _format_evidence(evidence: list[EvidencePacket]) -> str:
        lines: list[str] = []
        for packet in evidence:
            for ev in packet.evidence:
                lines.append(
                    f"- [{ev.id}] ({ev.source.value}/{ev.type.value}) {ev.title or 'untitled'}"
                )
        return "\n".join(lines) if lines else "No evidence provided."
