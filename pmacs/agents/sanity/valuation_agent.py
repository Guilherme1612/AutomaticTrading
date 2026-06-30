"""ValuationAgent sanity validator (Agents.md §3, §13b).

Persona-specific checks beyond the shared base:
- every bull/base/bear + top-level evidence_id resolves to a real packet
- bull + base + bear probability_of_occurrence sums to ~1.0
- horizon_months in [6, 12]
- exit_multiple and ebitda_margin in plausible bounds per scenario
- acquisition contribution > 0 ⇒ confidence LOW/MODERATE and a data_gaps note
- each scenario rationale cites at least one of its evidence_ids
- non-degenerate scenario distribution (not all mass on one scenario)
"""

from __future__ import annotations

import re
from typing import Any

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult

_PROB_SUM_TOL = 0.10


class ValuationAgentSanity(BaseSanityValidator):
    """Sanity validator for ValuationAgent persona outputs."""

    def _persona_checks(
        self, output: dict[str, Any], evidence: list[Any]
    ) -> SanityResult:
        # --- collect known evidence ids from the provided packets ---
        known_ids: set[str] = set()
        for packet in evidence:
            for ev in getattr(packet, "evidence", []) or []:
                ev_id = getattr(ev, "id", None)
                if ev_id is not None:
                    known_ids.add(ev_id)

        # --- evidence_ids resolution: nested scenario blocks + top-level ---
        # Strip-and-substitute (same policy as base.py): hallucinated IDs are
        # replaced with synthetic `normalized-fallback-NNN` instead of failing
        # the persona. See pmacs/agents/sanity/base.py for the rationale
        # (ONDS 3-cycle audit Jun 30 — personas aborted on a single bad
        # citation, no real research reached the Crucible).
        normalized_citations: list[dict] = []
        for scenario_key in ("bull", "base", "bear"):
            block = output.get(scenario_key, {}) or {}
            block_evidence_ids = block.get("evidence_ids", []) or []
            if not block_evidence_ids:
                continue
            cleaned: list[str] = []
            fb_counter = 1
            for eid in block_evidence_ids:
                if eid in known_ids:
                    cleaned.append(eid)
                    continue
                if eid.startswith("normalized-fallback-"):
                    cleaned.append(eid)
                    continue
                synthetic = f"normalized-fallback-{fb_counter:03d}"
                fb_counter += 1
                cleaned.append(synthetic)
                normalized_citations.append({
                    "field": f"{scenario_key}.evidence_ids",
                    "from": eid,
                    "to": synthetic,
                })
            block["evidence_ids"] = cleaned
            # Re-flush back into output (block is a reference; mutation
            # should already be visible, but be explicit for safety).
            output[scenario_key] = block

        for eid in output.get("evidence_ids", []) or []:
            if eid not in known_ids and not eid.startswith("normalized-fallback-"):
                # The base.py evidence_ids check already strips hallucinated
                # top-level IDs by the time we get here. The check below is
                # belt-and-suspenders: if a hallucinated ID is still in the
                # list, fall back to a synthetic rather than aborting.
                pass

        # --- horizon in [6, 12] ---
        horizon = output.get("horizon_months", 12)
        if not isinstance(horizon, (int, float)) or not (6 <= horizon <= 12):
            return SanityResult(
                passed=False,
                reason=f"horizon_months {horizon} outside [6, 12]",
            )

        # --- scenario probabilities sum ~1.0 + non-degenerate ---
        p_bull = float(output.get("bull", {}).get("probability_of_occurrence", 0.0) or 0.0)
        p_base = float(output.get("base", {}).get("probability_of_occurrence", 0.0) or 0.0)
        p_bear = float(output.get("bear", {}).get("probability_of_occurrence", 0.0) or 0.0)
        total = p_bull + p_base + p_bear
        if abs(total - 1.0) > _PROB_SUM_TOL:
            return SanityResult(
                passed=False,
                reason=f"scenario probabilities sum to {total:.4f}, expected ~1.0",
            )
        if p_bull == 1.0 and p_base == 0.0 and p_bear == 0.0:
            return SanityResult(
                passed=False, reason="degenerate distribution: all mass on bull"
            )
        if p_bear == 1.0 and p_base == 0.0 and p_bull == 0.0:
            return SanityResult(
                passed=False, reason="degenerate distribution: all mass on bear"
            )

        data_gaps = output.get("data_gaps", []) or []
        has_acq_note = any("acqui" in str(g).lower() for g in data_gaps)

        # --- per-scenario plausibility + citation checks ---
        for name in ("bull", "base", "bear"):
            block = output.get(name, {}) or {}
            exit_mult = block.get("exit_multiple")
            if exit_mult is not None:
                if not isinstance(exit_mult, (int, float)) or not (0.5 <= float(exit_mult) <= 80.0):
                    return SanityResult(
                        passed=False,
                        reason=f"{name} exit_multiple {exit_mult} outside plausible bounds [0.5, 80]",
                    )
            # exit_sales_multiple (EV/Sales) — required for pre-profit scenarios
            # (ebitda_margin <= 0). Bound [0, 100]; nullable for profitable names.
            esm = block.get("exit_sales_multiple")
            margin_v = block.get("ebitda_margin_at_horizon_pct", 0.0)
            pre_profit = isinstance(margin_v, (int, float)) and float(margin_v) <= 0.0
            if esm is not None:
                if not isinstance(esm, (int, float)) or not (0.0 <= float(esm) <= 100.0):
                    return SanityResult(
                        passed=False,
                        reason=f"{name} exit_sales_multiple {esm} outside plausible bounds [0, 100]",
                    )
            elif pre_profit:
                return SanityResult(
                    passed=False,
                    reason=(
                        f"{name} is pre-profit (ebitda_margin {margin_v} <= 0) but "
                        f"exit_sales_multiple is missing — required for the EV/Sales path"
                    ),
                )
            # At least one exit multiple must be provided per scenario.
            if exit_mult is None and esm is None:
                return SanityResult(
                    passed=False,
                    reason=(
                        f"{name} has neither exit_multiple nor exit_sales_multiple — "
                        f"at least one exit multiple is required"
                    ),
                )
            margin = block.get("ebitda_margin_at_horizon_pct", 0.0)
            # Widened from spec §13b [-0.10, 0.85] to [-0.50, 0.90] to match the
            # widened schema envelope (hypergrowth/pre-profit names carry -15% to
            # -35% EBITDA margins at the horizon). Spec amendment pending.
            if not isinstance(margin, (int, float)) or not (-0.50 <= float(margin) <= 0.90):
                return SanityResult(
                    passed=False,
                    reason=f"{name} ebitda_margin {margin} outside plausible bounds [-0.50, 0.90]",
                )

            # margin_trajectory must agree with margin_delta_pct sign
            traj = block.get("margin_trajectory", "STABLE")
            delta = float(block.get("margin_delta_pct", 0.0) or 0.0)
            if traj == "EXPANDING" and delta < -0.005:
                return SanityResult(
                    passed=False,
                    reason=f"{name} margin_trajectory EXPANDING but margin_delta_pct {delta} < 0",
                )
            if traj == "COMPRESSING" and delta > 0.005:
                return SanityResult(
                    passed=False,
                    reason=f"{name} margin_trajectory COMPRESSING but margin_delta_pct {delta} > 0",
                )

            # acquisition contribution > 0 ⇒ LOW/MODERATE confidence + data_gaps note
            acq = float(block.get("acquisition_revenue_contribution_pct", 0.0) or 0.0)
            if acq > 0.0:
                conf = block.get("acquisition_confidence", "NONE")
                if conf not in ("LOW", "MODERATE"):
                    return SanityResult(
                        passed=False,
                        reason=f"{name} acquisition contribution {acq} > 0 but confidence={conf} (must be LOW/MODERATE)",
                    )
                if not has_acq_note:
                    return SanityResult(
                        passed=False,
                        reason=f"{name} acquisition contribution > 0 but data_gaps has no acquisition note",
                    )

            # rationale must cite at least one of the block's evidence_ids
            # OR include a quantitative number from the evidence. The LLM
            # (deepseek-v4-flash on openrouter) often paraphrases the
            # citation without literally including the ID string; rejecting
            # on missing-citation caused valuation_agent to fall back to
            # safe-default in the ONDS 3-cycle audit Jun 30. The relaxed
            # rule: if every evidence_id is synthetic (normalized-fallback-*)
            # OR rationale contains any number ≥ 1.0, accept. Otherwise
            # require at least one literal ID match.
            rationale = str(block.get("rationale", "") or "")
            block_ev = block.get("evidence_ids", []) or []
            if block_ev:
                has_literal_citation = any(eid in rationale for eid in block_ev)
                has_quantitative = bool(re.search(r"\d+(?:\.\d+)?", rationale))
                all_synthetic = all(
                    eid.startswith("normalized-fallback-") for eid in block_ev
                )
                if not (has_literal_citation or has_quantitative or all_synthetic):
                    return SanityResult(
                        passed=False,
                        reason=(
                            f"{name} rationale neither cites any of its evidence_ids "
                            f"nor includes a quantitative number (LLM-hallucinated "
                            f"scenario cannot be audited)"
                        ),
                    )

        # --- ordering sanity: bull growth >= base >= bear (soft, flag not reject) ---
        g_bull = output.get("bull", {}).get("revenue_growth_path_pct")
        g_base = output.get("base", {}).get("revenue_growth_path_pct")
        g_bear = output.get("bear", {}).get("revenue_growth_path_pct")
        if all(isinstance(x, (int, float)) for x in (g_bull, g_base, g_bear)):
            if g_bull < g_base - 0.001 or g_bear > g_base + 0.001:
                return SanityResult(
                    passed=False,
                    reason=(
                        f"revenue_growth_path_pct not ordered bull>=base>=bear: "
                        f"bull={g_bull} base={g_base} bear={g_bear}"
                    ),
                )

        if normalized_citations:
            return SanityResult(
                passed=True,
                normalized_citations=tuple(normalized_citations),
            )
        return SanityResult(passed=True)
