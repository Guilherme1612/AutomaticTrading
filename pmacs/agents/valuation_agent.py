"""ValuationAgent persona runner — forward-valuation scenario assumptions.

Post-arbitration ONLY (Agents.md §13b, Architecture.md §9.4b). Emits bull/base/bear
ASSUMPTIONS (revenue growth path, margin trajectory, EBITDA margin at horizon,
acquisition impact, exit EV/EBITDA multiple) consumed by the deterministic
ForwardValuationEngine. Does NOT enter Arbitration, does NOT amend conviction,
and does NOT emit a price number (Five Non-Negotiable #2 — LLMs never math).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


_PROMPT_DIR = Path(__file__).parent / "prompts"


class ValuationAgentRunner(PersonaRunner):
    """Runner for the ValuationAgent persona.

    Produces ValuationAgentOutput with bull/base/bear scenario assumptions and
    per-scenario probability-of-occurrence. Never produces a price target — the
    ForwardValuationEngine computes the price from these assumptions.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="valuation_agent",
            grammar_name="valuation_agent",
            temperature=0.2,
            max_tokens=4096,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import ValuationAgentOutput
        return ValuationAgentOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.valuation_agent import ValuationAgentSanity
        return ValuationAgentSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        prompt_path = _PROMPT_DIR / "valuation_agent.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        system_prompt = system_prompt.replace("{today_date}", today)

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
            f"Respond with valid JSON matching the ValuationAgentOutput schema."
        )

    @staticmethod
    def _format_evidence(evidence: list[EvidencePacket]) -> str:
        from pmacs.agents.base import PersonaRunner
        return PersonaRunner.format_evidence_for_prompt(evidence)
