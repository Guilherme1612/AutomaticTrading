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

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """ValuationAgent drift fixes (deepseek-v4-flash on openrouter).

        - Each scenario's ``rationale`` (max_length=800) frequently exceeds.
        - Top-level ``evidence_ids`` may be empty — padded.
        - Literal enums (margin_trajectory, acquisition_confidence) normalized.
        - Out-of-envelope numeric assumptions (growth > ceiling, margin < floor)
          clamped to the schema bound as a safety net — preferred over a 3-retry
          abort that drops the whole forward valuation. Each clamp is audit-logged.
          NOTE: the envelope itself was widened (hypergrowth/pre-profit) so the
          common case now passes without clamping; this catches true outliers.
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        parsed, fixes = self._normalize_literal_enums(parsed, model_cls)
        all_fixes.extend(fixes)

        parsed, fixes = self._truncate_string_fields(parsed, model_cls)
        all_fixes.extend(fixes)

        parsed, fixes = self._clamp_numeric_fields(parsed, model_cls)
        all_fixes.extend(fixes)

        parsed, fixes = self._ensure_min_evidence_ids(parsed, model_cls)
        all_fixes.extend(fixes)

        self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

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
