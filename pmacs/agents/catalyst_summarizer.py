"""CatalystSummarizer persona runner — catalyst inventory and assessment (Agents.md §6).

Identifies and evaluates catalysts (earnings, FDA decisions, product launches, etc.)
for a specific ticker and assesses their net directional impact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


_PROMPT_DIR = Path(__file__).parent / "prompts"


class CatalystSummarizerRunner(PersonaRunner):
    """Runner for the CatalystSummarizer persona.

    Produces CatalystSummarizerOutput with catalyst entries, net outlook,
    and directional probabilities.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
    ) -> None:
        super().__init__(
            persona_name="catalyst_summarizer",
            grammar_name="catalyst_summarizer",
            temperature=0.2,
            max_tokens=1024,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import CatalystSummarizerOutput
        return CatalystSummarizerOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.catalyst_summarizer import CatalystSummarizerSanity
        return CatalystSummarizerSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        prompt_path = _PROMPT_DIR / "catalyst_summarizer.md"
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
            f"Respond with valid JSON matching the CatalystSummarizerOutput schema."
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
