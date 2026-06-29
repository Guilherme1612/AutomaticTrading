"""MacroRegime persona runner — macro regime classification (Agents.md §4).

Classifies the current macroeconomic environment into one of six regimes
and assesses impact on growth-tech equities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


_PROMPT_DIR = Path(__file__).parent / "prompts"


class MacroRegimeRunner(PersonaRunner):
    """Runner for the MacroRegime persona.

    Produces MacroRegimeOutput with regime classification, yield curve signal,
    VIX regime, sector rotation summary, and directional probabilities.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="macro_regime",
            grammar_name="macro_regime",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import MacroRegimeOutput
        return MacroRegimeOutput

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """MacroRegime drift fixes (deepseek-v4-flash on openrouter).

        - ``yield_curve_signal`` and ``vix_regime`` are Literal enums; the
          LLM emits lowercase (``"flat"``, ``"elevated"``) or unknown
          (``"NO_DATA"``, ``"UNCERTAIN"``). Case-insensitive enum matcher
          maps them to canonical uppercase; unknowns fall back to first
          enum member (NORMAL / LOW — safest defaults).
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
        from pmacs.agents.sanity.macro_regime import MacroRegimeSanity
        return MacroRegimeSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        prompt_path = _PROMPT_DIR / "macro_regime.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        system_prompt = system_prompt.replace("{today_date}", today)

        evidence_text = self._format_evidence(evidence)
        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"

        return (
            f"{system_prompt}\n\n"
            f"## Evidence\n{evidence_text}\n"
            f"{context_block}\n\n"
            f"Respond with valid JSON matching the MacroRegimeOutput schema."
        )

    @staticmethod
    def _format_evidence(evidence: list[EvidencePacket]) -> str:
        from pmacs.agents.base import PersonaRunner
        return PersonaRunner.format_evidence_for_prompt(evidence)
