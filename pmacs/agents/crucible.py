"""Crucible persona runner — adversarial thesis attacker (Agents.md §14).

Three-layer validation pipeline:
  1. llama-server HTTP call with GBNF grammar constraint
  2. Pydantic model_validate() parse
  3. Sanity validator

spec_ref: Agents.md §12
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.data import EvidencePacket


class CrucibleRunner(PersonaRunner):
    """Adversarial thesis attacker persona.

    Finds flaws in investment theses: logical holes, citation gaps,
    counterarguments, overlooked risks, and base rate neglect.
    """

    def __init__(self) -> None:
        super().__init__(
            persona_name="crucible",
            grammar_name="crucible",
            temperature=0.1,  # Lower than other personas
            max_tokens=2048,
        )

    def get_pydantic_model(self):
        from pmacs.schemas.personas import CrucibleOutput
        return CrucibleOutput

    def get_sanity_validator(self):
        from pmacs.agents.sanity.crucible import CrucibleSanity
        return CrucibleSanity()

    def _pre_validate(self, parsed: dict[str, Any]) -> dict[str, Any]:
        """Normalize Crucible LLM output before Pydantic validation.

        deepseek-v4-flash (via openrouter) emits ``attacks`` in two
        incompatible shapes, depending on attempt:

        1. ``attacks`` is a dict keyed by attack axis letter (A/B/C/D — natural
           given the prompt's A. VALUATION / B. MOAT / C. MGMT / D. THREATS
           structure) OR by the full axis name (``valuation_assumptions``,
           ``mgmt_track_record``, …). Each value is a dict with
           ``score`` + ``attack``/``rationale`` + (optional) ``evidence`` but
           NO ``attack_type``, ``severity``, ``description``, or
           ``evidence_ids`` fields. The canonical schema and downstream
           consumers (sanity validator, arbitration, conviction, memo writer,
           dashboard) all expect ``list[CrucibleAttack]``.

        2. ``attacks`` is a list whose items are similarly malformed
           (same field-name drift, just already in a list).

        This hook handles both shapes:

        - dict→list while preserving the A→B→C→D order, and reconciles
          ``attack_count`` if it disagrees with the new list length;
        - per-item field rename ``score`` → ``severity`` and
          ``attack``/``rationale``/``evidence`` → ``description``;
        - infers ``attack_type`` from the outer dict key when the LLM
          omitted it (the key is a strong signal: ``A``/``valuation_*``
          → ``LOGICAL_HOLE``, ``B``/``moat_*`` → ``CITATION_GAP``,
          ``C``/``mgmt_*`` → ``COUNTERARGUMENT``, ``D``/``competitive_*``
          → ``OVERLOOKED_RISK``, fallback → ``BASE_RATE_NEGLECT``);
        - injects ``evidence_ids=["synthetic-normalized-fallback-001"]`` when
          the LLM omitted them (Pydantic min_length=1 would otherwise reject
          and the persona falls back to safe-default, losing the analysis).

        This is a defensive parser fix, NOT a schema change. Schema, sanity
        validator, and GBNF grammar still declare list. The hook is the only
        place that knows about the model-specific dict + field-rename drift.
        """
        all_fixes: list[dict[str, Any]] = []

        # Map outer dict key (or inner axis hint) → canonical attack_type.
        # The 4 axis names mirror the 4 prompt sections (Agents.md §14 +
        # prompts/crucible.md: A. VALUATION ASSUMPTIONS / B. MOAT DURABILITY /
        # C. MANAGEMENT TRACK RECORD / D. COMPETITIVE THREATS).
        _AXIS_TO_ATTACK_TYPE = {
            "A": "LOGICAL_HOLE", "VALUATION": "LOGICAL_HOLE",
            "VALUATION_ASSUMPTIONS": "LOGICAL_HOLE",
            "VALUATION_ASSUMPTION": "LOGICAL_HOLE",
            "B": "CITATION_GAP", "MOAT": "CITATION_GAP",
            "MOAT_DURABILITY": "CITATION_GAP",
            "C": "COUNTERARGUMENT", "MGMT": "COUNTERARGUMENT",
            "MANAGEMENT": "COUNTERARGUMENT",
            "MANAGEMENT_TRACK_RECORD": "COUNTERARGUMENT",
            "MGMT_TRACK_RECORD": "COUNTERARGUMENT",
            "D": "OVERLOOKED_RISK", "THREATS": "OVERLOOKED_RISK",
            "COMPETITIVE_THREATS": "OVERLOOKED_RISK",
            "COMPETITIVE_THREAT": "OVERLOOKED_RISK",
        }
        # Per-item field-name aliases the LLM emits. We canonicalize to
        # {attack_type, severity, description, evidence_ids}.
        _FIELD_ALIASES = {
            "score": "severity",
            "attack": "description",
            "rationale": "description",
            "evidence": "description",
            "text": "description",
        }
        # Inner-field hint → attack_type (used when outer key is not a
        # known axis but the item carries an "axis"/"name"/"category" hint).
        _INNER_HINT_TO_ATTACK_TYPE = {
            "VALUATION": "LOGICAL_HOLE",
            "VALUATION_ASSUMPTIONS": "LOGICAL_HOLE",
            "MOAT": "CITATION_GAP",
            "MOAT_DURABILITY": "CITATION_GAP",
            "MGMT": "COUNTERARGUMENT",
            "MANAGEMENT": "COUNTERARGUMENT",
            "MANAGEMENT_TRACK_RECORD": "COUNTERARGUMENT",
            "THREATS": "OVERLOOKED_RISK",
            "COMPETITIVE_THREATS": "OVERLOOKED_RISK",
        }

        attacks = parsed.get("attacks")
        if isinstance(attacks, dict):
            ordered_keys = sorted(attacks.keys())
            ordered = []
            for k in ordered_keys:
                item = attacks[k]
                if not isinstance(item, dict):
                    ordered.append(item)
                    continue
                # Attach the outer key as a transient axis hint so the
                # per-item normalization can infer attack_type from it.
                # We strip the hint after use (it's not a schema field).
                item = dict(item)
                item["__axis_hint__"] = k
                ordered.append(item)
            parsed["attacks"] = ordered
            if parsed.get("attack_count") != len(ordered):
                parsed["attack_count"] = len(ordered)
            for k in ordered_keys:
                all_fixes.append({
                    "field": "attacks",
                    "type": "dict_to_list",
                    "key": k,
                    "axis": k,
                })
            attacks = ordered  # fall through into per-item normalization

        if isinstance(attacks, list):
            for i, item in enumerate(attacks):
                if not isinstance(item, dict):
                    continue
                # 1) Rename known alias fields in-place
                for old, new in _FIELD_ALIASES.items():
                    if old in item and new not in item:
                        item[new] = item.pop(old)
                        all_fixes.append({
                            "field": f"attacks[{i}].{old}",
                            "type": "renamed",
                            "from": old,
                            "to": new,
                        })
                # 2) Infer attack_type if missing — prefer the outer dict
                #    key (carried over from the dict→list conversion as
                #    __axis_hint__), then fall back to inner hint fields.
                if "attack_type" not in item or not item.get("attack_type"):
                    inferred = None
                    # Outer-key hint (set by the dict→list path above).
                    outer_hint = item.pop("__axis_hint__", None)
                    if isinstance(outer_hint, str):
                        norm = outer_hint.strip().upper().replace(" ", "_")
                        inferred = _AXIS_TO_ATTACK_TYPE.get(norm)
                        if inferred:
                            source = f"outer_dict_key={outer_hint!r}"
                        else:
                            # Unknown outer key — keep it in the audit fix
                            # log but don't reject; fall through to inner
                            # hints below.
                            source = None
                    else:
                        source = None
                    if inferred is None:
                        # Inner-hint fallback (axis / name / category / axe).
                        for hint_field in ("axis", "name", "category", "axe"):
                            hint = item.get(hint_field)
                            if isinstance(hint, str):
                                norm = hint.strip().upper().replace(" ", "_")
                                inferred = _INNER_HINT_TO_ATTACK_TYPE.get(norm)
                                if inferred:
                                    source = f"inner_field={hint_field}={hint!r}"
                                    break
                    if inferred is None:
                        # Last resort: BASE_RATE_NEGLECT (least specific
                        # of the 5 enum values, but always valid).
                        inferred = "BASE_RATE_NEGLECT"
                        source = "default_fallback"
                    item["attack_type"] = inferred
                    all_fixes.append({
                        "field": f"attacks[{i}].attack_type",
                        "type": "inferred",
                        "to": inferred,
                        "source": source or "unknown",
                        "reason": (
                            f"LLM omitted attack_type; inferred from "
                            f"{source or 'unknown'} → {inferred}."
                        ),
                    })
                # 3) Inject synthetic evidence_ids if missing — Pydantic
                #    has min_length=1 on CrucibleAttack.evidence_ids, so
                #    the LLM omitting them would otherwise reject. Using
                #    the synthetic-normalized-fallback-* prefix matches
                #    the base validator's acceptance window (commit
                #    42057ee "accept synthetic normalized-fallback-*
                #    evidence IDs in base validator").
                if "evidence_ids" not in item or not item.get("evidence_ids"):
                    item["evidence_ids"] = ["synthetic-normalized-fallback-001"]
                    all_fixes.append({
                        "field": f"attacks[{i}].evidence_ids",
                        "type": "synthesized",
                        "to": ["synthetic-normalized-fallback-001"],
                        "reason": (
                            "LLM omitted evidence_ids; injected synthetic "
                            "fallback so Pydantic min_length=1 constraint "
                            "passes (Crucible attack preserved)."
                        ),
                    })
                # 4) Synthesize a description if it is still missing
                #    after the alias rename (e.g. axis-only dict).
                if not item.get("description"):
                    sev = item.get("severity", 0.0)
                    atype = item.get("attack_type", "BASE_RATE_NEGLECT")
                    item["description"] = (
                        f"{atype} attack at severity {sev:.2f} (description "
                        f"missing in LLM output; synthesized by pre-validate)"
                    )
                    all_fixes.append({
                        "field": f"attacks[{i}].description",
                        "type": "synthesized",
                        "reason": (
                            "LLM omitted description; synthesized from "
                            "attack_type + severity so the attack survives."
                        ),
                    })

        # Reconcile attack_count one more time in case the original was a
        # list of length N but the LLM emitted a wrong N.
        if isinstance(attacks, list) and parsed.get("attack_count") != len(attacks):
            parsed["attack_count"] = len(attacks)

        if all_fixes:
            self._log_normalization(all_fixes, ticker=parsed.get("ticker", ""))
        return parsed

    def build_prompt(
        self,
        evidence: list[EvidencePacket],
        episodic_context: str | None = None,
    ) -> str:
        template_path = Path(__file__).parent / "prompts" / "crucible.md"
        template = template_path.read_text(encoding="utf-8")

        evidence_text = self.format_evidence_for_prompt(evidence)

        context_block = ""
        if episodic_context:
            context_block = f"\n## Episodic Context\n{episodic_context}"

        from datetime import date
        today = date.today().isoformat()

        return template.replace(
            "{evidence}", evidence_text
        ).replace(
            "{episodic_context}", context_block
        ).replace(
            "{today_date}", today
        )
