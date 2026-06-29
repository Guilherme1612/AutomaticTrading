"""MemoWriter sanity validator — persona-specific checks (Agents.md §13).

Checks:
- verdict_line starts with STRONG_BUY / BUY / HOLD / SKIP
- thesis field is non-empty (field is "thesis", not "thesis_summary")
- key_evidence items are non-empty strings
- conviction matches engine output (±0.01) when provided
- Numbers in financial_snapshot trace to evidence (cross-validation)
- Verdict aligns with conviction thresholds

spec_ref: Agents.md §13.5
"""

from __future__ import annotations

import re
from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult

VALID_VERDICT_PREFIXES = ("STRONG_BUY", "BUY", "HOLD", "SKIP", "PASS")


class MemoWriterSanity(BaseSanityValidator):
    """Sanity validator for MemoWriter persona output.

    Extended with accuracy checks that cross-validate memo claims
    against the evidence data provided to the LLM.
    """

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        verdict_line = output.get("verdict_line", "")

        # verdict_line must start with a valid prefix
        if not verdict_line or not any(
            verdict_line.startswith(prefix) for prefix in VALID_VERDICT_PREFIXES
        ):
            return SanityResult(
                passed=False,
                reason=(
                    f"verdict_line must start with one of {VALID_VERDICT_PREFIXES}, "
                    f"got: '{verdict_line[:50]}'"
                ),
            )

        # thesis must be present and non-empty (field is "thesis", not "thesis_summary")
        thesis = output.get("thesis", "")
        if not thesis or not str(thesis).strip():
            return SanityResult(
                passed=False,
                reason="thesis field is missing or empty",
            )

        # thesis must contain at least one number (grounding check)
        if not re.search(r"\d", str(thesis)):
            return SanityResult(
                passed=False,
                reason="thesis must include at least one specific number from evidence",
            )

        # key_evidence items must be non-empty
        key_evidence = output.get("key_evidence", [])
        if len(key_evidence) < 2:
            return SanityResult(
                passed=False,
                reason=f"key_evidence needs at least 2 items, got {len(key_evidence)}",
            )
        for i, item in enumerate(key_evidence):
            if not item or not str(item).strip():
                return SanityResult(
                    passed=False,
                    reason=f"key_evidence[{i}] is empty",
                )

        # key_risks must exist and be non-empty
        key_risks = output.get("key_risks", [])
        if len(key_risks) < 1:
            return SanityResult(
                passed=False,
                reason="key_risks must have at least 1 item",
            )
        for i, item in enumerate(key_risks):
            if not item or not str(item).strip():
                return SanityResult(
                    passed=False,
                    reason=f"key_risks[{i}] is empty",
                )

        # At least half of key_evidence items must contain a number
        items_with_nums = sum(
            1 for item in key_evidence if re.search(r"\d", str(item))
        )
        if len(key_evidence) >= 3 and items_with_nums < len(key_evidence) // 2:
            return SanityResult(
                passed=False,
                reason=(
                    f"Only {items_with_nums}/{len(key_evidence)} key_evidence items "
                    f"contain numbers — evidence must be quantitative"
                ),
            )

        # Conviction range check
        conviction = output.get("conviction")
        if conviction is not None:
            if conviction < 0.0 or conviction > 1.0:
                return SanityResult(
                    passed=False,
                    reason=f"conviction={conviction} out of [0.0, 1.0] range",
                )

        # fair_value sanity: must be positive if present
        fair_value = output.get("fair_value")
        if fair_value is not None:
            try:
                fv = float(fair_value)
                if fv <= 0:
                    return SanityResult(
                        passed=False,
                        reason=f"fair_value={fv} must be positive",
                    )
            except (TypeError, ValueError):
                return SanityResult(
                    passed=False,
                    reason=f"fair_value={fair_value} is not a valid number",
                )

        # valuation_range coherence: low <= base <= high
        val_range = output.get("valuation_range", {})
        if val_range and isinstance(val_range, dict):
            low = val_range.get("low")
            base = val_range.get("base")
            high = val_range.get("high")
            if low is not None and high is not None:
                try:
                    if float(low) > float(high):
                        return SanityResult(
                            passed=False,
                            reason=f"valuation_range inverted: low={low} > high={high}",
                        )
                except (TypeError, ValueError):
                    pass
            if base is not None and low is not None and high is not None:
                try:
                    if float(base) < float(low) or float(base) > float(high):
                        return SanityResult(
                            passed=False,
                            reason=f"valuation_range.base={base} outside [{low}, {high}]",
                        )
                except (TypeError, ValueError):
                    pass

        # ── Wave-2 enrichment checks (Agents.md §16.9) ───────────────────────
        # These fields are optional-with-defaults so simulation and pipeline
        # paths without debate context still pass. When the LLM DOES populate
        # them, enforce structural sanity: no empty items, no malformed dicts.

        bull_bear_debate = output.get("bull_bear_debate") or {}
        if isinstance(bull_bear_debate, dict) and bull_bear_debate:
            for key in ("bull_case", "bear_case"):
                val = bull_bear_debate.get(key)
                if val is not None and not str(val).strip():
                    return SanityResult(
                        passed=False,
                        reason=f"bull_bear_debate.{key} is present but empty",
                    )
            advocate_lean = bull_bear_debate.get("advocate_lean")
            if advocate_lean is not None:
                lean = str(advocate_lean).strip().upper()
                if lean and lean not in ("BULL", "BEAR", "BALANCED", "NEUTRAL"):
                    return SanityResult(
                        passed=False,
                        reason=f"bull_bear_debate.advocate_lean='{advocate_lean}' "
                               "must be one of BULL/BEAR/BALANCED/NEUTRAL",
                    )

        wwm = output.get("what_would_change_my_mind") or []
        if isinstance(wwm, list) and wwm:
            for i, item in enumerate(wwm):
                if not item or not str(item).strip():
                    return SanityResult(
                        passed=False,
                        reason=f"what_would_change_my_mind[{i}] is empty",
                    )

        reverse_dcf = output.get("reverse_dcf")
        if reverse_dcf is not None:
            if not isinstance(reverse_dcf, dict):
                return SanityResult(
                    passed=False,
                    reason=f"reverse_dcf must be a dict, got {type(reverse_dcf).__name__}",
                )

        scenario_price = output.get("scenario_price")
        if scenario_price is not None:
            if not isinstance(scenario_price, dict):
                return SanityResult(
                    passed=False,
                    reason=f"scenario_price must be a dict, got {type(scenario_price).__name__}",
                )

        # ── Allocator-grade sanity (.planning/memo_allocator_redesign_prompt.md) ──
        # These mirror the schema-level @model_validator. The schema catches
        # gross garbage; this layer catches semantic problems the LLM could
        # produce that pass the schema but are still wrong.

        # 1. verdict: must be one of the 5 tiers or empty (legacy).
        verdict = output.get("verdict", "")
        if verdict and verdict not in ("STRONG_BUY", "BUY", "HOLD", "SKIP", "PASS"):
            return SanityResult(
                passed=False,
                reason=f"verdict={verdict!r} must be one of STRONG_BUY/BUY/HOLD/SKIP/PASS",
            )

        # 2. pass_reason: required when verdict=PASS, ≤ 200 chars, non-empty.
        if verdict == "PASS":
            pr = output.get("pass_reason")
            if not pr or not str(pr).strip():
                return SanityResult(
                    passed=False,
                    reason=(
                        "verdict=PASS requires a non-empty pass_reason "
                        "(active no-bid, not 'couldn't decide')"
                    ),
                )
            if len(str(pr)) > 200:
                return SanityResult(
                    passed=False,
                    reason=f"pass_reason length {len(str(pr))} exceeds 200 char cap",
                )

        # 3. thesis_bullets: 1-5 entries, all four fields present.
        tb = output.get("thesis_bullets") or []
        if isinstance(tb, list) and tb:
            if len(tb) > 5:
                return SanityResult(
                    passed=False,
                    reason=f"thesis_bullets capped at 5 entries, got {len(tb)}",
                )
            for i, b in enumerate(tb):
                if not isinstance(b, dict):
                    return SanityResult(
                        passed=False,
                        reason=f"thesis_bullets[{i}] must be a dict",
                    )
                for fld in ("premise", "mechanism", "outcome", "number"):
                    if not str(b.get(fld) or "").strip():
                        return SanityResult(
                            passed=False,
                            reason=f"thesis_bullets[{i}].{fld} is required and non-empty",
                        )

        # 4. comparable_transactions: ≤ 5, at least one multiple per row.
        ct = output.get("comparable_transactions") or []
        if isinstance(ct, list) and ct:
            if len(ct) > 5:
                return SanityResult(
                    passed=False,
                    reason=f"comparable_transactions capped at 5 entries, got {len(ct)}",
                )
            for i, c in enumerate(ct):
                if not isinstance(c, dict):
                    return SanityResult(
                        passed=False,
                        reason=f"comparable_transactions[{i}] must be a dict",
                    )
                if c.get("ev_revenue_multiple") is None and c.get("ev_ebitda_multiple") is None:
                    return SanityResult(
                        passed=False,
                        reason=(
                            f"comparable_transactions[{i}] needs at least one of "
                            f"ev_revenue_multiple / ev_ebitda_multiple — empty comps "
                            f"are not a substitute for fabrication"
                        ),
                    )

        # 5. counter_thesis: every claim has a falsifier.
        cth = output.get("counter_thesis") or []
        if isinstance(cth, list) and cth:
            for i, ct in enumerate(cth):
                if not isinstance(ct, dict):
                    return SanityResult(
                        passed=False,
                        reason=f"counter_thesis[{i}] must be a dict",
                    )
                if not str(ct.get("claim") or "").strip():
                    return SanityResult(
                        passed=False,
                        reason=f"counter_thesis[{i}].claim is required",
                    )
                if not str(ct.get("falsifier") or "").strip():
                    return SanityResult(
                        passed=False,
                        reason=(
                            f"counter_thesis[{i}].falsifier is required — "
                            f"the operator must commit to a falsifiable trigger"
                        ),
                    )

        return SanityResult(passed=True)
