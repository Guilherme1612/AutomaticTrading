"""ShortInterest persona runner — short interest anomaly detection (Agents.md §10).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §10
"""

from __future__ import annotations

from pathlib import Path

from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class ShortInterestRunner(PersonaRunner):
    """Short interest analyst persona.

    Detects short interest anomalies: spikes, sustained high levels,
    and changes in short positioning.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="short_interest",
            grammar_name="short_interest",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import ShortInterestOutput
        return ShortInterestOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.short_interest import ShortInterestSanity
        return ShortInterestSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "short_interest.md"
        template = template_path.read_text(encoding="utf-8")

        evidence_text = self.format_evidence_for_prompt(evidence)

        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        return template.replace(
            "{today_date}", today
        ).replace(
            "{evidence}", evidence_text
        ).replace(
            "{episodic_context}", context_block
        )
