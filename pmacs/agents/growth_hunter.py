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
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="growth_hunter",
            grammar_name="growth_hunter",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import GrowthHunterOutput
        return GrowthHunterOutput

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """GrowthHunter drift fixes (deepseek-v4-flash on openrouter).

        - ``revenue_acceleration``, ``gross_margin_trend``, ``growth_durability``
          are Literal enums; LLM emits lowercase
          (``"accelerating"``, ``"stable"``). Case-insensitive matcher maps
          them to canonical uppercase.
        - Top-level ``evidence_ids`` may be empty — padded.
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        parsed, fixes = self._normalize_literal_enums(parsed, model_cls)
        all_fixes.extend(fixes)

        parsed, fixes = self._ensure_min_evidence_ids(parsed, model_cls)
        all_fixes.extend(fixes)

        self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

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

        # Build structured evidence text with real financial numbers
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
