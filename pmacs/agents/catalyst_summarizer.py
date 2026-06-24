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
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="catalyst_summarizer",
            grammar_name="catalyst_summarizer",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import CatalystSummarizerOutput
        return CatalystSummarizerOutput

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """CatalystSummarizer drift fixes (deepseek-v4-flash on openrouter).

        - ``catalysts[i].thesis_impact`` is sometimes omitted (LLM leaves
          out the field); inject ``"NEUTRAL"`` per CatalystEntry schema.
        - ``catalysts[i].catalyst_type`` may be a value not in the Literal
          enum (``"hyperscaler_deal"``, ``"analyst_upgrade"``,
          ``"index_inclusion"``); the case-insensitive matcher in
          ``_normalize_literal_enums`` will fall back to the first member.
        - ``catalysts[i].description`` may exceed max_length=400 — truncated.
        - Top-level ``evidence_ids`` may be empty — padded.
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        # 1) Inject thesis_impact="NEUTRAL" if missing on any catalyst entry
        catalysts = parsed.get("catalysts", [])
        if isinstance(catalysts, list):
            for i, c in enumerate(catalysts):
                if isinstance(c, dict) and "thesis_impact" not in c:
                    c["thesis_impact"] = "NEUTRAL"
                    all_fixes.append({
                        "field": f"catalysts[{i}].thesis_impact",
                        "type": "missing_injected",
                        "before": None,
                        "after": "NEUTRAL",
                    })

        # 2) Normalize Literal enums (case-insensitive, default fallback)
        parsed, fixes = self._normalize_literal_enums(parsed, model_cls)
        all_fixes.extend(fixes)

        # 3) Truncate any string fields that exceed schema max_length
        parsed, fixes = self._truncate_string_fields(parsed, model_cls)
        all_fixes.extend(fixes)

        # 4) Pad empty evidence_ids at top + nested level
        parsed, fixes = self._ensure_min_evidence_ids(parsed, model_cls)
        all_fixes.extend(fixes)

        self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

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
            f"Respond with valid JSON matching the CatalystSummarizerOutput schema."
        )

    @staticmethod
    def _format_evidence(evidence: list[EvidencePacket]) -> str:
        from pmacs.agents.base import PersonaRunner
        return PersonaRunner.format_evidence_for_prompt(evidence)
