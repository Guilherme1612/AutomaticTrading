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
        - **Missing ``vix_regime`` field** — deepseek sometimes omits the
          field entirely. Pydantic raises ``ValidationError`` on
          required-field missing. Fix synthesizes a safe default
          (``"MODERATE"`` — middle of the LOW/MODERATE/ELEVATED/CRISIS
          range, the honest "I don't know" answer) so the persona
          survives. Cycle 1 ONDS Jun 30 surfaced this — without the fix,
          the persona aborts at attempt 3 and the macro read is lost.
        - Top-level ``evidence_ids`` may be empty — padded.
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        parsed, fixes = self._normalize_literal_enums(parsed, model_cls)
        all_fixes.extend(fixes)

        parsed, fixes = self._ensure_min_evidence_ids(parsed, model_cls)
        all_fixes.extend(fixes)

        # Inject vix_regime if missing. Deepseek on openrouter has
        # dropped the field in cycle 1 ONDS Jun 30. Pydantic rejects
        # missing required Literal fields; pre-validate normalization
        # keeps the persona alive by emitting the safest middle-of-range
        # value (MODERATE) so downstream consumers see an honest
        # "I-don't-know-but-not-extreme" read.
        if "vix_regime" not in parsed or not parsed.get("vix_regime"):
            parsed["vix_regime"] = "MODERATE"
            all_fixes.append({
                "field": "vix_regime",
                "from": None,
                "to": "MODERATE",
                "reason": (
                    "LLM omitted required vix_regime field. Synthesized "
                    "MODERATE (middle of LOW/MODERATE/ELEVATED/CRISIS) "
                    "as the safest I-don't-know default. Pydantic would "
                    "otherwise reject the missing required Literal."
                ),
            })

        # Inject regime_reasoning if missing. Cycle 2 ONDS Jun 30 surfaced
        # the LLM dropping the field entirely when there is no strong macro
        # signal in the evidence. Synthesize a placeholder that explicitly
        # states "no strong macro read" so the audit chain can see the
        # gap (better than a safe-default which hides the drift).
        if "regime_reasoning" not in parsed or not parsed.get("regime_reasoning"):
            regime = parsed.get("regime", "UNCERTAIN")
            parsed["regime_reasoning"] = (
                f"No specific macro signal in the provided evidence; "
                f"regime classified as {regime} on best-effort basis. "
                f"(regime_reasoning field was missing in LLM output; "
                f"synthesized by pre-validate.)"
            )
            all_fixes.append({
                "field": "regime_reasoning",
                "from": None,
                "to": "(synthesized)",
                "reason": (
                    "LLM omitted required regime_reasoning field. "
                    "Synthesized a placeholder that surfaces the gap to "
                    "the audit chain rather than swallowing it via "
                    "safe-default."
                ),
            })

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
