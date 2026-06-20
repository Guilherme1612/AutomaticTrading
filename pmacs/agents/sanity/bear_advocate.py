"""BearAdvocate sanity validator — persona-specific checks (Agents.md §11c.5).

Mirror of BullAdvocateSanity. Checks:
- target_persona is a real wave-1 analysis persona
- reasoning references the target persona's thesis
- distribution is non-degenerate *unless* reasoning concedes the bull case
- strongest_bull_counterpoint is non-empty
- evidence_ids resolve (shared base check)
"""

from __future__ import annotations

from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.schemas.personas import WAVE1_PERSONAS
from pmacs.schemas.agents import PersonaName

_TARGET_TOKENS = {
    PersonaName.MACRO_REGIME: ("macro_regime", "macro regime", "macro"),
    PersonaName.CATALYST_SUMMARIZER: ("catalyst_summarizer", "catalyst", "catalysts"),
    PersonaName.MOAT_ANALYST: ("moat_analyst", "moat"),
    PersonaName.GROWTH_HUNTER: ("growth_hunter", "growth"),
    PersonaName.INSIDER_ACTIVITY: ("insider_activity", "insider"),
    PersonaName.SHORT_INTEREST: ("short_interest", "short"),
    PersonaName.FORENSICS: ("forensics", "forensic"),
}


class BearAdvocateSanity(BaseSanityValidator):
    """Sanity validator for BearAdvocate persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        target = output.get("target_persona")
        if target is None:
            return SanityResult(passed=False, reason="target_persona is required")
        target_val = getattr(target, "value", target)
        try:
            target_enum = PersonaName(target_val)
        except ValueError:
            return SanityResult(
                passed=False, reason=f"target_persona '{target_val}' is not a PersonaName"
            )
        if target_enum not in WAVE1_PERSONAS:
            return SanityResult(
                passed=False,
                reason=f"target_persona must be a wave-1 analysis persona, got {target_val}",
            )

        reasoning = (output.get("reasoning") or "").strip().lower()
        if not reasoning:
            return SanityResult(passed=False, reason="reasoning is empty")

        tokens = _TARGET_TOKENS.get(target_enum, ())
        if not any(tok in reasoning for tok in tokens):
            return SanityResult(
                passed=False,
                reason=(
                    f"reasoning does not reference the target persona "
                    f"({target_val}); advocacy must engage the named persona"
                ),
            )

        counter = (output.get("strongest_bull_counterpoint") or "").strip()
        if not counter:
            return SanityResult(
                passed=False, reason="strongest_bull_counterpoint is empty (no strawmanning)"
            )

        p_up = output.get("p_up", 0.0)
        p_flat = output.get("p_flat", 0.0)
        p_down = output.get("p_down", 0.0)
        concedes = "bull case" in reasoning or "supports the bull" in reasoning or "bull case is stronger" in reasoning
        if p_up == p_flat == p_down and not concedes:
            return SanityResult(
                passed=False,
                reason="degenerate distribution without an explicit bull-case concession",
            )
        if p_up > p_down and not concedes:
            return SanityResult(
                passed=False,
                reason="bear advocate emitted p_up > p_down without conceding the bull case",
            )

        return SanityResult(passed=True)
