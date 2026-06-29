"""MemoWriter persona runner — operator-facing memo synthesis (Agents.md §15).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §13
"""

from __future__ import annotations

from pathlib import Path

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class MemoWriterRunner(PersonaRunner):
    """Operator-facing memo writer persona.

    Synthesizes outputs from 7 analysts, Crucible, arbitration,
    conviction, and verdict into a readable investment memo.
    """

    def __init__(self) -> None:
        super().__init__(
            persona_name="memo_writer",
            grammar_name="memo_writer",
            temperature=0.3,  # Highest temperature: analysis=0.2, Crucible=0.1
            max_tokens=8192,
        )
        self._analytical_context: str = ""

    def set_analytical_context(
        self,
        *,
        arbitrated: object | None = None,
        verdict: object | None = None,
        conviction_score: float | None = None,
        crucible_severity: float | None = None,
        crucible_attacks: list | None = None,
        forensics_quality: str | None = None,
        persona_outputs: list | None = None,
        advocate_outputs: dict | None = None,
        auditor_flags: list | None = None,
        reverse_dcf: object | None = None,
        forward_valuation: object | None = None,
        scenario_price: object | None = None,
        data_quality_warnings: list[str] | None = None,
        memo_feedback: str | None = None,
        prior_memo_summary: dict | None = None,
        persona_weights: list | None = None,
        per_persona_calibration: dict | None = None,
    ) -> None:
        """Inject the full analytical synthesis so the memo has real numbers.

        Called by the orchestrator before run() so build_prompt can include
        per-persona signals, arbitration probabilities, and verdict. Wave-2
        debate/audit/valuation context (Agents.md §11b-§11d, §16.9) is injected
        here so the memo surfaces the bull/bear debate, auditor findings, the
        reverse-DCF growth gap, and the scenario-weighted expected price.

        ``data_quality_warnings`` is a list of strings (one per flagged metric)
        collected from ``_data_quality_warning`` fields in the evidence packets
        the analyst layer saw. Surfacing them here prevents the memo from citing
        flagged-anomalous numbers as facts (e.g. ONDS netProfitMarginTTM=251.9%
        which the source marked as "likely Finnhub data corruption").
        """
        lines: list[str] = []

        if arbitrated is not None:
            p_up = getattr(arbitrated, "p_up", None)
            p_flat = getattr(arbitrated, "p_flat", None)
            p_down = getattr(arbitrated, "p_down", None)
            decision = getattr(arbitrated, "decision", None)
            lines.append("## Arbitration Result")
            if p_up is not None and p_flat is not None and p_down is not None:
                lines.append(
                    f"p_up={p_up:.3f}  p_flat={p_flat:.3f}  p_down={p_down:.3f}"
                    f"  decision={getattr(decision, 'value', decision)}"
                )
            else:
                lines.append(f"decision={getattr(decision, 'value', decision)} (probabilities unavailable)")

        # ── Persona Arbitration Weights (Commit 4 — Tier 4) ───────────────────
        # Surfaces who drove the verdict and how reliable each persona is on THIS
        # ticker (per-persona DuckDB calibration). The operator can see, in the
        # memo, which personas were weighted highest and which are calibrated.
        if persona_weights:
            try:
                from pmacs.agents.sanity.memo_scorer import (
                    format_persona_weight_table,
                )
                _tbl = format_persona_weight_table(
                    persona_weights,
                    per_persona_calibration=per_persona_calibration,
                )
                if _tbl:
                    lines.append("\n## Persona Arbitration Weights")
                    lines.append(
                        "Sorted desc by arbitration weight. "
                        "'ticker_brier' = this persona's avg brier on THIS "
                        "ticker (DuckDB persona_ticker_affinity, n>=5). "
                        "Brier >0.25 → LOW CONFIDENCE on this ticker."
                    )
                    lines.append(_tbl)
            except Exception:
                # Best-effort: never abort memo rendering on a formatting hiccup.
                pass

        if conviction_score is not None:
            verdict_str = getattr(verdict, "value", str(verdict)) if verdict else "?"
            lines.append(f"\n## Conviction")
            lines.append(f"score={conviction_score:.4f}  verdict={verdict_str}")

        if crucible_severity is not None:
            lines.append(f"\n## Crucible")
            lines.append(f"severity={crucible_severity:.3f}")
            # FIX-3: Include crucible attack details so the memo reflects
            # what the adversarial layer found (agents don't see this).
            if crucible_attacks:
                lines.append("### Crucible Attacks (adversarial findings)")
                for i, attack in enumerate(crucible_attacks[:5], 1):
                    if isinstance(attack, dict):
                        label = attack.get("attack_type", attack.get("type", f"Attack {i}"))
                        detail = attack.get("reasoning", attack.get("detail", ""))
                        lines.append(f"  {i}. [{label}] {detail}")
                    elif isinstance(attack, str):
                        lines.append(f"  {i}. {attack}")
                lines.append("IMPORTANT: Address these attacks in key_risks. Fair value must account for crucible findings.")

        # IMP-3: Forensics fair value gate
        if forensics_quality and forensics_quality in ("MATERIAL_CONCERNS", "SEVERE_RISK"):
            lines.append(f"\n## Forensics Alert: {forensics_quality}")
            lines.append(
                "FAIR VALUE GATE: Forensics flagged material accounting concerns. "
                "You MUST clamp fair_value_estimate to the BEAR CASE only. "
                "Do NOT use bull-case or base-case valuations when earnings quality "
                "is questionable. Explain this constraint in key_risks."
            )

        if persona_outputs:
            lines.append("\n## Persona Signals")
            for dp in persona_outputs:
                persona = getattr(dp, "persona", "?")
                name = getattr(persona, "value", str(persona))
                pu = getattr(dp, "p_up", 0.0)
                pf = getattr(dp, "p_flat", 0.0)
                pd = getattr(dp, "p_down", 0.0)
                direction = "UP" if pu > pd and pu > pf else ("DOWN" if pd > pu and pd > pf else "FLAT")
                lines.append(
                    f"  {name:22s}: p_up={pu:.3f} p_flat={pf:.3f} p_down={pd:.3f}  → {direction}"
                )

        # ── Wave-2 debate + audit + valuation (Agents.md §16.9) ───────────────
        if advocate_outputs:
            lines.append("\n## Bull / Bear Advocate Debate")
            for pname, po in advocate_outputs.items():
                import json as _aj
                reasoning = ""
                raw = getattr(po, "raw_output", "")
                if raw:
                    try:
                        d = _aj.loads(raw)
                        reasoning = d.get("reasoning", "")
                        pu = d.get("p_up"); pf = d.get("p_flat"); pd = d.get("p_down")
                        tgt = d.get("target_persona", "")
                    except (ValueError, TypeError):
                        pu = pf = pd = None
                        tgt = ""
                else:
                    pu = pf = pd = None
                    tgt = ""
                prob = (
                    f"p_up={pu:.3f} p_flat={pf:.3f} p_down={pd:.3f}"
                    if all(isinstance(v, (int, float)) for v in (pu, pf, pd))
                    else "(probs unavailable)"
                )
                lines.append(f"  {pname} (vs {tgt}): {prob}")
                if reasoning:
                    lines.append(f"    {str(reasoning)[:280]}")

        if auditor_flags:
            lines.append("\n## Cross-Persona Auditor Flags")
            lines.append(
                "IMPORTANT: Address each flag in key_risks. A flag means a wave-1 "
                "persona's reasoning did not follow from its cited evidence."
            )
            for flag in auditor_flags[:8]:
                ftype = getattr(flag, "flag_type", "?")
                target = getattr(flag, "target_persona", None)
                target_val = getattr(target, "value", target)
                sev = getattr(flag, "severity", 0.0)
                desc = getattr(flag, "description", "")
                lines.append(f"  [{ftype} | {target_val} | sev={sev:.2f}] {desc}")

        if reverse_dcf is not None and getattr(reverse_dcf, "is_available", False):
            lines.append("\n## Reverse-DCF Valuation Anchor")
            lines.append(
                f"  Market-implied growth: {reverse_dcf.implied_growth_pct*100:.2f}%  "
                f"vs estimated: {reverse_dcf.assumed_growth_pct*100:.2f}%  "
                f"(gap {reverse_dcf.growth_gap_pct*100:+.2f}pp, lean={reverse_dcf.valuation_lean})"
            )
            if reverse_dcf.fair_value_usd is not None:
                lines.append(f"  Fair value at estimated growth: ${reverse_dcf.fair_value_usd:,.0f}")
        elif reverse_dcf is not None:
            notes = getattr(reverse_dcf, "notes", "") or "unavailable"
            lines.append(f"\n## Reverse-DCF Valuation Anchor\n  Unavailable ({notes}).")

        # ── Forward valuation (6-12mo, Architecture.md §9.4b) ──────────────────
        # The ValuationAgent's LLM-produced assumptions, priced by the deterministic
        # ForwardValuationEngine. The LLM never emits the price (§1.6). Surfaces the
        # bull/base/bear scenario prices + the agent's scenario-weighted expected
        # price + key base-case assumptions + data gaps. When unavailable, the memo
        # falls back to the reverse-DCF anchor above — never fabricates.
        if forward_valuation is not None and getattr(forward_valuation, "is_available", False):
            lines.append(f"\n## Forward Valuation ({forward_valuation.horizon_months}mo)")
            lines.append(
                f"  Bull=${forward_valuation.bull_price:,.2f}  "
                f"Base=${forward_valuation.base_price:,.2f}  "
                f"Bear=${forward_valuation.bear_price:,.2f}"
            )
            if forward_valuation.expected_price_usd is not None:
                lines.append(
                    f"  Scenario-weighted expected price: ${forward_valuation.expected_price_usd:,.2f}"
                )
            base_pt = (forward_valuation.scenario_points or {}).get("base")
            if base_pt is not None:
                g = base_pt.revenue_growth_path_pct
                m = base_pt.ebitda_margin_at_horizon_pct
                x = base_pt.exit_multiple
                xs = getattr(base_pt, "exit_sales_multiple", None)
                path = getattr(base_pt, "valuation_path", None)
                parts = []
                if g is not None:
                    parts.append(f"revenue growth {g*100:.1f}% to horizon")
                if m is not None:
                    parts.append(f"EBITDA margin {m*100:.1f}%")
                if path == "ev_sales" and xs is not None:
                    parts.append(f"exit EV/Sales {xs:.1f}x (pre-profit path)")
                elif x is not None:
                    parts.append(f"exit EV/EBITDA {x:.1f}x")
                elif xs is not None:
                    parts.append(f"exit EV/Sales {xs:.1f}x")
                if parts:
                    lines.append(f"  Base-case assumptions: {', '.join(parts)}")
            # ── The non-obvious reconciliation: model vs market vs Wall Street ──
            # Most memos state a fair value and stop. The operator's edge is seeing
            # the GAP between (a) the multiple the market pays today, (b) the
            # multiple the agent assumed at the horizon, and (c) the analyst PT —
            # and judging which view the evidence actually supports. Surface all
            # three explicitly so the memo reader can reconcile them.
            cur = getattr(forward_valuation, "current_price_usd", None)
            cur_ev_sales = getattr(forward_valuation, "current_ev_sales", None)
            pt_mean = getattr(forward_valuation, "analyst_target_mean_usd", None)
            base_px = forward_valuation.base_price
            gap_parts: list[str] = []
            if cur_ev_sales is not None:
                gap_parts.append(f"market pays {cur_ev_sales:.1f}x EV/Sales today")
                if base_pt is not None and getattr(base_pt, "valuation_path", None) == "ev_sales" and getattr(base_pt, "exit_sales_multiple", None) is not None:
                    gap_parts.append(
                        f"agent assumes {base_pt.exit_sales_multiple:.1f}x at horizon "
                        f"({(base_pt.exit_sales_multiple/cur_ev_sales - 1)*100:+.0f}% vs market)"
                    )
            if base_px and cur and cur > 0:
                gap_parts.append(f"model base {base_px/cur - 1:+.1%} vs current ${cur:,.2f}")
            if base_px and pt_mean and pt_mean > 0:
                gap_parts.append(f"model base {base_px/pt_mean - 1:+.1%} vs analyst PT ${pt_mean:,.2f}")
            if gap_parts:
                lines.append("  Reconciliation: " + "; ".join(gap_parts))
            # ── Tier 3 — forward valuation warnings (Commit 3) ───────────────
            # Three warnings can stack here (in priority order):
            #   1. forward_vs_reverse_dcf_warning: >50% gap vs reverse-DCF → LLM
            #      hallucination check
            #   2. agent_scenario_convergence_warning: includes ⚠ DISTRESS tag
            #      when base_price_underwater, plus LOW-CONFIDENCE FORWARD
            #      VALUATION when |p_bull - p_bear| < 0.10
            _dcf_warn = getattr(forward_valuation, "forward_vs_reverse_dcf_warning", "")
            if _dcf_warn:
                lines.append(f"  WARNING: {_dcf_warn}")
            _conv_warn = getattr(
                forward_valuation, "agent_scenario_convergence_warning", ""
            )
            if _conv_warn:
                # Multi-warning strings are joined with '; ' by the engine;
                # render verbatim so the operator sees the whole chain.
                for _w in _conv_warn.split("; "):
                    if _w.strip():
                        lines.append(f"  WARNING: {_w.strip()}")
        elif forward_valuation is not None:
            notes = getattr(forward_valuation, "notes", "") or "unavailable"
            lines.append(f"\n## Forward Valuation\n  Unavailable ({notes}).")

        if scenario_price is not None and getattr(scenario_price, "is_available", False):
            lines.append("\n## Scenario-Weighted Expected Price")
            lines.append(
                f"  E[price] = ${scenario_price.expected_price_usd:,.2f}  "
                f"(bull=${scenario_price.bull_price:,.2f}, base=${scenario_price.base_price:,.2f}, "
                f"bear=${scenario_price.bear_price:,.2f})"
            )
            if scenario_price.expected_return_pct is not None:
                lines.append(f"  Expected return vs current: {scenario_price.expected_return_pct:+.1f}%")

        # ── Data-quality warnings (post-cycle-1 audit Jun 24) ─────────────────
        # Each warning identifies a metric the source itself flagged as anomalous
        # (typically "likely Finnhub data corruption" or out-of-range values). The
        # memo MUST NOT cite flagged metrics as facts. This is the memo-side
        # counterpart to ``format_evidence_for_prompt`` which already warns the
        # analyst personas — the analyst layer never reaches the operator, but the
        # memo does, so we re-surface the warnings here.
        if data_quality_warnings:
            lines.append("\n## Data Quality Warnings (DO NOT cite as facts)")
            lines.append(
                "IMPORTANT: The following metrics in the evidence were flagged by "
                "their source as anomalous or unreliable. Do NOT cite these flagged "
                "values in THESIS, KEY EVIDENCE, financial_snapshot, or anywhere in "
                "the memo. State the data-quality concern explicitly if relevant, "
                "or cite an alternative source (EDGAR XBRL, Yahoo Finance), or omit "
                "the figure entirely."
            )
            for w in data_quality_warnings[:10]:
                lines.append(f"  - {w}")

        # ── Memo quality feedback (memo_scorer retry, Agents.md §13.5) ─────────
        # On a low-quality first draft, the orchestrator invokes score_memo() and
        # re-runs memo_writer.run() once with this block injected. The LLM sees
        # the per-dimension score + critical issues and rewrites accordingly. This
        # is the cost-capped 1-retry loop (operator directive; risk.toml budget).
        if memo_feedback:
            lines.append("\n## Memo Quality Feedback (fix these issues)")
            lines.append(
                "IMPORTANT: This is a re-run. The previous draft scored low. "
                "Address the issues below — do not regress on already-correct fields."
            )
            lines.append(memo_feedback)

        # ── Prior memo context (Commit 2 — Tier 2A, redundant injection) ──────
        # The persona brief (episodic_context) already carries the prior memo
        # summary, but the memo writer sometimes benefits from a second copy in
        # the analytical block so it can write a "what changed since last memo"
        # paragraph without having to cross-reference two sections. Best-effort:
        # missing fields are skipped; empty prior_memo_summary renders nothing.
        if prior_memo_summary:
            _ps = prior_memo_summary
            _any = any(_ps.get(k) for k in (
                "thesis", "verdict_line", "fair_value", "valuation_methodology",
                "key_evidence", "key_risks", "what_would_change_my_mind",
            )) or _ps.get("forward_expected_price_usd") is not None
            if _any:
                lines.append("\n## Prior Memo Context (last analysis)")
                if _ps.get("thesis"):
                    lines.append(f"  Thesis: {_ps['thesis'][:500]}")
                if _ps.get("verdict_line"):
                    lines.append(f"  Verdict line: {_ps['verdict_line']}")
                if _ps.get("fair_value"):
                    lines.append(f"  Fair value: {_ps['fair_value']}")
                if _ps.get("valuation_methodology"):
                    lines.append(
                        f"  Methodology: {_ps['valuation_methodology'][:300]}"
                    )
                if _ps.get("key_evidence"):
                    lines.append(
                        "  Evidence: "
                        + "; ".join(str(x)[:100] for x in _ps["key_evidence"][:5])
                    )
                if _ps.get("key_risks"):
                    lines.append(
                        "  Risks: "
                        + "; ".join(str(x)[:100] for x in _ps["key_risks"][:5])
                    )
                if _ps.get("what_would_change_my_mind"):
                    lines.append(
                        "  What would change my mind: "
                        + "; ".join(
                            str(x)[:100]
                            for x in _ps["what_would_change_my_mind"][:3]
                        )
                    )
                _pfep = _ps.get("forward_expected_price_usd")
                if isinstance(_pfep, (int, float)):
                    lines.append(
                        f"  Prior forward-expected price: ${float(_pfep):,.2f}"
                    )

        self._analytical_context = "\n".join(lines)

    def get_pydantic_model(self):
        from pmacs.schemas.personas import MemoWriterOutput
        return MemoWriterOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.memo_writer import MemoWriterSanity
        return MemoWriterSanity()

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "memo_writer.md"
        template = template_path.read_text(encoding="utf-8")

        evidence_text = self.format_evidence_for_prompt(evidence)

        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"
        if self._analytical_context:
            context_block += f"\n{self._analytical_context}"

        return template.replace(
            "{evidence}", evidence_text
        ).replace(
            "{episodic_context}", context_block
        )
