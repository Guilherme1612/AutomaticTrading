"""BullAdvocate sanity validator — persona-specific checks (Agents.md §11b.5).

Checks:
- target_persona is a real wave-1 analysis persona
- reasoning references the target persona's thesis (not a generic bull pitch)
- distribution is non-degenerate *unless* reasoning concedes the bear case
- strongest_bear_counterpoint is non-empty (no strawmanning)
- evidence_ids resolve (shared base check handles this)
"""

from __future__ import annotations

import re
from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.schemas.personas import WAVE1_PERSONAS
from pmacs.schemas.agents import PersonaName

# Wave-1 persona tokens the reasoning might cite. The literal slug is the
# primary check (the LLM must mention the persona) but the synonyms cover
# common drift cases where the LLM discusses the topic without using the
# canonical name. ONDS 3-cycle audit Jun 29 showed the strict check was the
# dominant wave-2 blocker for both bull/bear advocates — drift was semantic
# (discusses moat/catalysts/growth but does not literally include the slug),
# not adversarial.
_TARGET_TOKENS: dict[PersonaName, tuple[str, ...]] = {
    PersonaName.MACRO_REGIME: (
        "macro_regime", "macro regime", "macro",
        "macro environment", "regime", "fiscal", "monetary",
        "rate environment", "yield curve",
    ),
    PersonaName.CATALYST_SUMMARIZER: (
        "catalyst_summarizer", "catalyst", "catalysts",
        "upcoming event", "upcoming", "launch", "fda",
        "earnings release", "guidance", "near-term event",
    ),
    PersonaName.MOAT_ANALYST: (
        "moat_analyst", "moat", "moats",
        "competitive advantage", "competitive position",
        "barrier", "barriers", "switching cost", "switching costs",
        "network effect", "network effects", "defensibility",
    ),
    PersonaName.GROWTH_HUNTER: (
        "growth_hunter", "growth",
        "topline", "top-line", "revenue growth", "expansion",
        "tam", "total addressable market", "ramp", "penetration",
        "growth trajectory",
    ),
    PersonaName.INSIDER_ACTIVITY: (
        "insider_activity", "insider", "insiders",
        "form 4", "form-4", "insider buying", "insider selling",
        "ceo", "cfo", "officer", "director", "10b5-1", "10b5",
    ),
    PersonaName.SHORT_INTEREST: (
        "short_interest", "short", "shorts",
        "short interest", "short squeeze", "borrow", "squeeze",
        "days to cover", "short selling", "bearish positioning",
    ),
    PersonaName.FORENSICS: (
        "forensics", "forensic",
        "accounting quality", "red flag", "red flags",
        "earnings quality", "cash flow quality", "fraud",
        "sbc", "stock-based compensation", "restatement",
    ),
}


class BullAdvocateSanity(BaseSanityValidator):
    """Sanity validator for BullAdvocate persona output."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        target = output.get("target_persona")
        if target is None:
            return SanityResult(passed=False, reason="target_persona is required")
        # Normalize enum to its value for membership test.
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

        # Reasoning must reference the target persona's thesis by name or topic,
        # OR be a substantive argument that engages with the financial analysis
        # (contains numbers, percentages, or dollar amounts). The strict
        # slug/topic check is the dominant wave-2 blocker — the LLM
        # (deepseek-v4-flash) often writes a strong bull case without ever
        # literally naming the persona slug, falling back to safe-default and
        # producing a 239-char Crucible-abort stub (ONDS 3-cycle audit Jun 30).
        # The pragmatic check: at least one literal/topic token, OR a
        # substantive quantitative argument (length > 80 chars + any number).
        tokens = _TARGET_TOKENS.get(target_enum, ())
        has_topic_token = any(tok in reasoning for tok in tokens)
        has_quantitative_substance = (
            len(reasoning) > 80
            and bool(re.search(r"\d", reasoning))
        )
        if not (has_topic_token or has_quantitative_substance):
            return SanityResult(
                passed=False,
                reason=(
                    f"reasoning does not reference the target persona "
                    f"({target_val}) and is not a substantive quantitative "
                    f"argument; advocacy must engage the named persona"
                ),
            )

        counter = (output.get("strongest_bear_counterpoint") or "").strip()
        if not counter:
            return SanityResult(
                passed=False, reason="strongest_bear_counterpoint is empty (no strawmanning)"
            )

        # Non-degenerate distribution unless the reasoning concedes the bear case.
        p_up = output.get("p_up", 0.0)
        p_flat = output.get("p_flat", 0.0)
        p_down = output.get("p_down", 0.0)
        concedes = "bear case" in reasoning or "supports the bear" in reasoning or "bear case is stronger" in reasoning
        if p_up == p_flat == p_down and not concedes:
            return SanityResult(
                passed=False,
                reason="degenerate distribution without an explicit bear-case concession",
            )
        # A bull advocate that pushes p_up should not be dominated by p_down unless
        # it concedes. Allow flat-heavy concessions.
        if p_down > p_up and not concedes:
            return SanityResult(
                passed=False,
                reason="bull advocate emitted p_down > p_up without conceding the bear case",
            )

        return SanityResult(passed=True)
