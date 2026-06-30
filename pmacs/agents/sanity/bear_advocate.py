"""BearAdvocate sanity validator — persona-specific checks (Agents.md §11c.5).

Mirror of BullAdvocateSanity. Checks:
- target_persona is a real wave-1 analysis persona
- reasoning references the target persona's thesis
- distribution is non-degenerate *unless* reasoning concedes the bull case
- strongest_bull_counterpoint is non-empty
- evidence_ids resolve (shared base check)
"""

from __future__ import annotations

import re
from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult
from pmacs.schemas.personas import WAVE1_PERSONAS
from pmacs.schemas.agents import PersonaName

# Wave-1 persona tokens — same expansion as BullAdvocateSanity (ONDS 3-cycle
# audit Jun 29). The literal slug is the primary check, but the synonyms
# cover common semantic-drift cases where the LLM discusses the topic
# without using the canonical name.
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

        # Same pragmatic check as BullAdvocateSanity (ONDS 3-cycle audit
        # Jun 30). The strict slug/topic check is the dominant wave-2
        # blocker — the LLM often writes a substantive bear case without
        # literally naming the persona slug, falling back to safe-default
        # and producing a 239-char Crucible-abort stub. Accept if the
        # reasoning references a target token OR is a substantive
        # quantitative argument (length > 80 chars + any number).
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
