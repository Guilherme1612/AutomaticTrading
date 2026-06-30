"""Forensics persona runner — forensic accounting analysis (Agents.md §11).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §11
"""

from __future__ import annotations

from pathlib import Path

from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class ForensicsRunner(PersonaRunner):
    """Forensic accounting analyst persona.

    Detects red flags in financial statements: revenue quality,
    earnings manipulation, cash flow divergence, and more.
    """

    def __init__(
        self,
        cycle_id: str = "",
        audit_writer: Any | None = None,
        simulation_mode: bool = False,
    ) -> None:
        super().__init__(
            persona_name="forensics",
            grammar_name="forensics",
            temperature=0.2,
            max_tokens=5120,
            cycle_id=cycle_id,
            audit_writer=audit_writer,
            simulation_mode=simulation_mode,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import ForensicsOutput
        return ForensicsOutput

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """Forensics drift fixes (deepseek-v4-flash on openrouter, ONDS Jun 29).

        - Top-level ``evidence_ids`` may be empty — padded.
        - **CLEAN + N red_flags contradiction**: deepseek emits both
          ``overall_accounting_quality="CLEAN"`` AND a non-empty
          ``red_flags`` list. Sanity rejects this combination
          (``sanity/forensics.py``); without coercion, every attempt
          aborts. Resolved by bumping quality to ``MINOR_CONCERNS`` when
          the contradiction is detected — preserves LLM signal
          (severities + descriptions) while making the JSON internally
          consistent.
        """
        all_fixes: list[dict[str, Any]] = []
        model_cls = self.get_pydantic_model()

        parsed, fixes = self._ensure_min_evidence_ids(parsed, model_cls)
        all_fixes.extend(fixes)

        # Coerce the CLEAN + red_flags contradiction (ONDS 3-cycle audit
        # Jun 29). Fix is logged so the audit chain captures the drift and
        # resolution — operators can review which cycles the LLM emitted
        # both fields for.
        quality = parsed.get("overall_accounting_quality")
        red_flags = parsed.get("red_flags") or []
        if quality == "CLEAN" and len(red_flags) > 0:
            parsed["overall_accounting_quality"] = "MINOR_CONCERNS"
            all_fixes.append({
                "field": "overall_accounting_quality",
                "from": "CLEAN",
                "to": "MINOR_CONCERNS",
                "reason": (
                    f"LLM emitted CLEAN quality but listed {len(red_flags)} "
                    f"red flags — contradiction resolved by bumping to "
                    f"MINOR_CONCERNS (sanity/forensics.py would otherwise "
                    f"reject the combination)"
                ),
            })

        self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

    def get_sanity_validator(self):
        from pmacs.agents.sanity.forensics import ForensicsSanity
        return ForensicsSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "forensics.md"
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
