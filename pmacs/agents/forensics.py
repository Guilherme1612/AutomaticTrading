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
        """Forensics drift fixes (deepseek-v4-flash on openrouter, ONDS Jun 29/30).

        - Top-level ``evidence_ids`` may be empty — padded.
        - **CLEAN + N red_flags contradiction**: deepseek emits both
          ``overall_accounting_quality="CLEAN"`` AND a non-empty
          ``red_flags`` list. Sanity rejects this combination
          (``sanity/forensics.py``); without coercion, every attempt
          aborts. Resolved by bumping quality to ``MINOR_CONCERNS`` when
          the contradiction is detected — preserves LLM signal
          (severities + descriptions) while making the JSON internally
          consistent.
        - **Unknown red_flag.category literal** (e.g. ``GUIDANCE_CREDIBILITY``,
          ``AR_QUALITY``, ``CUSTOMER_CONCENTRATION``) — deepseek invents
          categories outside the spec's 8-value literal. Resolved by
          folding them into the closest existing category. Pydantic would
          otherwise reject every attempt and the persona falls back to
          safe-default, losing the entire analysis. Cycle 1 ONDS Jun 30
          (post-restart): abort at attempt 3 because the LLM emitted
          ``category: GUIDANCE_CREDIBILITY`` which is not in
          ``RedFlag.category``. Fix maps to the closest in-enum value
          (``EARNINGS_QUALITY`` for guidance-credibility, ``CASH_FLOW_DIVERGENCE``
          for AR/cash timing, ``RELATED_PARTY`` for concentration).
        - **Unknown overall_accounting_quality literal** (e.g. ``POOR``,
          ``GOOD``, ``BAD``) — deepseek invents non-spec quality levels.
          Resolved by mapping to the closest in-enum value based on the
          actual red_flag severity list (so a high-severity case maps to
          ``MATERIAL_CONCERNS``/``SEVERE_RISK`` rather than ``CLEAN``).
        - **Red flag missing required ``description`` field** — deepseek
          sometimes emits ``{"category": ..., "severity": ...,
          "evidence_ids": [...]}`` without the spec-required description
          text. Pydantic rejects on missing-field. Fix synthesizes a
          description from category + severity so the analyst signal
          survives.
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

        # Coerce unknown red_flag.category literals into the closest
        # in-enum value. Cycle 1 ONDS Jun 30 surfaced this when deepseek
        # emitted ``category: GUIDANCE_CREDIBILITY`` which Pydantic
        # rejected with a literal_error. Pydantic-level rejection aborts
        # the persona after 3 attempts; pre-validate normalization keeps
        # the cycle alive.
        _CATEGORY_ALIASES = {
            "GUIDANCE_CREDIBILITY": "EARNINGS_QUALITY",
            "GUIDANCE": "EARNINGS_QUALITY",
            "AR_QUALITY": "CASH_FLOW_DIVERGENCE",
            "AR": "CASH_FLOW_DIVERGENCE",
            "RECEIVABLES": "DSO_DPO_ANOMALY",
            "CUSTOMER_CONCENTRATION": "RELATED_PARTY",
            "CONCENTRATION": "RELATED_PARTY",
            "GOVERNANCE": "AUDITOR_FLAGS",
            "TAX": "EARNINGS_QUALITY",
            "INVENTORY": "MARGIN_ANOMALY",
            "DEBT": "CASH_FLOW_DIVERGENCE",
            "LIQUIDITY": "CASH_FLOW_DIVERGENCE",
            # Jun 30 SOLO-ONDS drift (LLM_PARSE_FAILED attempt 1/2/3):
            # deepseek invented ACCRUAL_RATE / SHORT_INTEREST /
            # DEBT_QUALITY_ASSESSMENT — none in the 8-value enum. Map to
            # the closest spec category. Without this the persona aborts
            # at attempt 3 and the cycle falls back to a 1-bullet stub.
            "ACCRUAL_RATE": "CASH_FLOW_DIVERGENCE",
            "ACCRUALS_QUALITY": "CASH_FLOW_DIVERGENCE",
            "ACCRUAL_QUALITY": "CASH_FLOW_DIVERGENCE",
            "ACCRUAL": "CASH_FLOW_DIVERGENCE",
            "SHORT_INTEREST": "DSO_DPO_ANOMALY",
            "DEBT_QUALITY_ASSESSMENT": "CASH_FLOW_DIVERGENCE",
            "DEBT_QUALITY": "CASH_FLOW_DIVERGENCE",
            "WORKING_CAPITAL": "DSO_DPO_ANOMALY",
            "CASH_BURN": "CASH_FLOW_DIVERGENCE",
            "REVENUE_RECOGNITION": "REVENUE_QUALITY",
            "REVENUE_RECOG": "REVENUE_QUALITY",
            "INTANGIBLES": "GOODWILL_RISK",
            "IMPAIRMENT": "GOODWILL_RISK",
            "AUDITOR_OPINION": "AUDITOR_FLAGS",
            "AUDITOR_CHANGE": "AUDITOR_FLAGS",
        }
        if isinstance(red_flags, list):
            for i, rf in enumerate(red_flags):
                if not isinstance(rf, dict):
                    continue
                cat = rf.get("category")
                if isinstance(cat, str) and cat in _CATEGORY_ALIASES:
                    new_cat = _CATEGORY_ALIASES[cat]
                    rf["category"] = new_cat
                    all_fixes.append({
                        "field": f"red_flags[{i}].category",
                        "from": cat,
                        "to": new_cat,
                        "reason": (
                            f"LLM emitted category '{cat}' which is not in "
                            f"the RedFlag literal enum. Folded to closest "
                            f"spec value '{new_cat}' to keep the analysis "
                            f"alive (Pydantic would otherwise reject every "
                            f"attempt)."
                        ),
                    })
                # Synthesize missing description (Cycle 1 ONDS Jun 30:
                # deepseek emitted ``{"category": ..., "severity": ...,
                # "evidence_ids": [...]}`` with no description, Pydantic
                # rejected on Field required).
                if "description" not in rf or not rf.get("description"):
                    sev = rf.get("severity", 0.0)
                    cat = rf.get("category", "RED_FLAG")
                    rf["description"] = (
                        f"{cat} flagged at severity {sev:.2f} (description "
                        f"missing in LLM output; synthesized by pre-validate)"
                    )
                    all_fixes.append({
                        "field": f"red_flags[{i}].description",
                        "from": None,
                        "to": rf["description"][:80] + "...",
                        "reason": (
                            "LLM omitted the required description field; "
                            "synthesized from category + severity."
                        ),
                    })

        # Coerce unknown overall_accounting_quality literals. Map to the
        # closest in-enum value by considering the actual red_flag severity
        # distribution (so POOR + high-severity flags maps to SEVERE_RISK
        # rather than CLEAN).
        #
        # Cycle 1 ONDS Jun 30 also surfaced ``"POOR — Aggressive
        # accounting..."`` (the LLM concatenates a sentence onto the
        # enum literal). We match the prefix (case-insensitive) and strip
        # the trailing reasoning text so Pydantic gets a clean enum value.
        _QUALITY_ALIASES = {
            # Negative (escalate) terms
            "POOR": "MATERIAL_CONCERNS",  # overridden below if severity warrants
            "BAD": "MATERIAL_CONCERNS",
            "TERRIBLE": "SEVERE_RISK",
            "CRITICAL": "SEVERE_RISK",
            "NEGATIVE": "MATERIAL_CONCERNS",
            "WEAK": "MATERIAL_CONCERNS",
            "DISTRESSED": "SEVERE_RISK",
            "FRAUDULENT": "SEVERE_RISK",
            "RED": "MATERIAL_CONCERNS",
            # Jun 30 SOLO-ONDS drift: LLM emitted VERY_POOR which
            # is not in the 5-value enum. SEVERE_RISK is the closest
            # (and most honest) fold.
            "VERY_POOR": "SEVERE_RISK",
            "VERY_BAD": "SEVERE_RISK",
            "BREAKING": "SEVERE_RISK",
            "FATAL": "SEVERE_RISK",
            # Quality-direction terms (low = bad accounting)
            "LOW": "MATERIAL_CONCERNS",  # overridden to SEVERE_RISK if reasoning has "severe"
            "MODERATE": "MINOR_CONCERNS",
            "MEDIUM": "MINOR_CONCERNS",
            "AVERAGE": "MINOR_CONCERNS",
            "FAIR": "MINOR_CONCERNS",
            "OK": "MINOR_CONCERNS",
            "ACCEPTABLE": "MINOR_CONCERNS",
            "ADEQUATE": "MINOR_CONCERNS",
            "MIXED": "MINOR_CONCERNS",
            # Positive terms
            "GOOD": "MINOR_CONCERNS",
            "GREAT": "CLEAN",
            "EXCELLENT": "CLEAN",
            "STRONG": "CLEAN",
            "HIGH": "CLEAN",
            "ROBUST": "CLEAN",
            "SOUND": "CLEAN",
            "HEALTHY": "CLEAN",
            "PRISTINE": "CLEAN",
            "GOLD": "CLEAN",
        }
        quality = parsed.get("overall_accounting_quality")
        if isinstance(quality, str):
            # Strip leading enum-like word if the LLM concatenated
            # reasoning (e.g. "POOR — Aggressive accounting"). Match
            # case-insensitive prefix against the alias map.
            quality_head = quality.strip().split(" ", 1)[0].rstrip(":—-,").upper()
            if quality_head in _QUALITY_ALIASES or quality_head in {
                "CLEAN", "MINOR_CONCERNS", "MATERIAL_CONCERNS",
                "SEVERE_RISK", "INSUFFICIENT_DATA",
            }:
                # Normalize via alias if not already a canonical enum value
                if quality_head in _QUALITY_ALIASES:
                    new_quality = _QUALITY_ALIASES[quality_head]
                    # If the LLM emits a strongly-negative descriptor
                    # (POOR/BAD/TERRIBLE/CRITICAL/LOW/WEAK/DISTRESSED/
                    # FRAUDULENT/RED) but red_flags all have severity >= 0.7,
                    # escalate to SEVERE_RISK so the verdict is honest.
                    if quality_head in (
                        "POOR", "BAD", "TERRIBLE", "CRITICAL",
                        "LOW", "WEAK", "DISTRESSED", "FRAUDULENT",
                        "RED", "NEGATIVE",
                    ) and red_flags:
                        max_sev = max(
                            (rf.get("severity", 0.0) for rf in red_flags
                             if isinstance(rf, dict)),
                            default=0.0,
                        )
                        if max_sev >= 0.7:
                            new_quality = "SEVERE_RISK"
                    parsed["overall_accounting_quality"] = new_quality
                    all_fixes.append({
                        "field": "overall_accounting_quality",
                        "from": quality,
                        "to": new_quality,
                        "reason": (
                            f"LLM emitted overall_accounting_quality={quality!r} "
                            f"which is not in the ForensicsOutput literal enum "
                            f"(head token '{quality_head}' matched an alias). "
                            f"Folded to closest spec value '{new_quality}' so "
                            f"the analysis survives (Pydantic literal_error "
                            f"would otherwise reject every attempt)."
                        ),
                    })
                elif quality_head != quality:
                    # Was already a valid enum literal but had trailing text
                    parsed["overall_accounting_quality"] = quality_head
                    all_fixes.append({
                        "field": "overall_accounting_quality",
                        "from": quality,
                        "to": quality_head,
                        "reason": (
                            f"LLM concatenated reasoning text onto a valid "
                            f"enum literal ({quality_head}). Stripped trailing "
                            f"text so Pydantic accepts the value."
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
