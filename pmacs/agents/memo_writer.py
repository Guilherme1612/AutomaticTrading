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
        scenario_price: object | None = None,
    ) -> None:
        """Inject the full analytical synthesis so the memo has real numbers.

        Called by the orchestrator before run() so build_prompt can include
        per-persona signals, arbitration probabilities, and verdict. Wave-2
        debate/audit/valuation context (Agents.md §11b-§11d, §16.9) is injected
        here so the memo surfaces the bull/bear debate, auditor findings, the
        reverse-DCF growth gap, and the scenario-weighted expected price.
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

        if scenario_price is not None and getattr(scenario_price, "is_available", False):
            lines.append("\n## Scenario-Weighted Expected Price")
            lines.append(
                f"  E[price] = ${scenario_price.expected_price_usd:,.2f}  "
                f"(bull=${scenario_price.bull_price:,.2f}, base=${scenario_price.base_price:,.2f}, "
                f"bear=${scenario_price.bear_price:,.2f})"
            )
            if scenario_price.expected_return_pct is not None:
                lines.append(f"  Expected return vs current: {scenario_price.expected_return_pct:+.1f}%")

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
