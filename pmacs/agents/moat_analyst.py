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
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="moat_analyst",
            grammar_name="moat_analyst",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import MoatAnalystOutput
        return MoatAnalystOutput

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """MoatAnalyst drift fixes (deepseek-v4-flash on openrouter).

        - ``moat_components[].type`` → ``moat_type`` (LLM uses natural-language
          "type" because the prompt labels moat dimensions; canonical schema
          uses ``moat_type``).
        - ``moat_components[].evidence_ids`` may be empty when LLM omits it
          (Pydantic min_length=1 violated).
        - Top-level ``evidence_ids`` may also be empty.

        All fixes are recorded via _log_normalization (cycle-scoped audit).
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        # 1) Rename "type" → "moat_type" inside moat_components (nested scope)
        parsed, fixes = self._rename_keys(
            parsed, {"type": "moat_type"}, scope="any"
        )
        all_fixes.extend(fixes)

        # 2) Pad empty evidence_ids at top + nested level
        parsed, fixes = self._ensure_min_evidence_ids(parsed, model_cls)
        all_fixes.extend(fixes)

        self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

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
            f"Respond with valid JSON matching the MoatAnalystOutput schema."
        )

    @staticmethod
    def _format_evidence(evidence: list[EvidencePacket]) -> str:
        from pmacs.agents.base import PersonaRunner
        return PersonaRunner.format_evidence_for_prompt(evidence)
