"""Unit tests for wave-2 debate + audit personas (Agents.md §11b-§11d).

Covers:
- BullAdvocate / BearAdvocate Pydantic invariants (target_persona membership, prob sum)
- Advocate sanity validators (target engagement, no strawman, directional consistency)
- CrossPersonaAuditor Pydantic + sanity (flag_type↔taxonomy_mapping, no probability
  fields, hallucinated-evidence-ID rejection, wave-1 target membership)
- Runner contract: get_pydantic_model / get_sanity_validator / build_prompt + set_peer_outputs
- Property: advocate probs sum to 1.0; auditor output never contains probability fields
- Simulation outputs validate against the real Pydantic models
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pmacs.agents.bear_advocate import BearAdvocateRunner
from pmacs.agents.bull_advocate import BullAdvocateRunner
from pmacs.agents.cross_persona_auditor import CrossPersonaAuditorRunner
from pmacs.agents.sanity.bear_advocate import BearAdvocateSanity
from pmacs.agents.sanity.bull_advocate import BullAdvocateSanity
from pmacs.agents.sanity.cross_persona_auditor import CrossPersonaAuditorSanity
from pmacs.agents.simulation import make_simulation_output
from pmacs.schemas.agents import PersonaName
from pmacs.schemas.failure import AUDITOR_ALLOWED_TAXONOMY, FailureTaxonomy
from pmacs.schemas.personas import (
    AuditorFlag,
    AuditorOutput,
    BearAdvocateOutput,
    BullAdvocateOutput,
    WAVE1_PERSONAS,
)


# ── fixtures ──────────────────────────────────────────────────────────────

def _evidence(known_ids: list[str]) -> list:
    """Build minimal evidence packets exposing .evidence[].id for sanity checks."""
    return [
        SimpleNamespace(
            ticker="X",
            evidence=[SimpleNamespace(id=eid) for eid in known_ids],
        )
    ]


BULL_VALID = {
    "ticker": "X",
    "target_persona": "growth_hunter",
    "p_up": 0.50,
    "p_flat": 0.30,
    "p_down": 0.20,
    "reasoning": "The growth_hunter thesis under-prices the acceleration; revenue is inflecting per ev-1.",
    "strongest_bear_counterpoint": "Deceleration risk if the major contract churns.",
    "evidence_ids": ["ev-1"],
}

BEAR_VALID = {
    "ticker": "X",
    "target_persona": "moat_analyst",
    "p_up": 0.20,
    "p_flat": 0.30,
    "p_down": 0.50,
    "reasoning": "The moat_analyst overstates durability; competitor entry is imminent per ev-1.",
    "strongest_bull_counterpoint": "Switching costs may be higher than they appear.",
    "evidence_ids": ["ev-1"],
}


# ── Pydantic invariants ───────────────────────────────────────────────────

class TestAdvocatePydantic:
    def test_bull_valid_round_trips(self):
        m = BullAdvocateOutput.model_validate(BULL_VALID)
        assert m.target_persona == PersonaName.GROWTH_HUNTER
        assert abs(m.p_up + m.p_flat + m.p_down - 1.0) < 1e-9

    def test_bear_valid_round_trips(self):
        m = BearAdvocateOutput.model_validate(BEAR_VALID)
        assert m.target_persona == PersonaName.MOAT_ANALYST
        assert abs(m.p_up + m.p_flat + m.p_down - 1.0) < 1e-9

    def test_bull_target_must_be_wave1(self):
        bad = {**BULL_VALID, "target_persona": "crucible"}
        with pytest.raises(ValidationError):
            BullAdvocateOutput.model_validate(bad)

    def test_bear_target_must_be_wave1(self):
        bad = {**BEAR_VALID, "target_persona": "memo_writer"}
        with pytest.raises(ValidationError):
            BearAdvocateOutput.model_validate(bad)

    def test_bull_probs_must_sum_near_one(self):
        bad = {**BULL_VALID, "p_up": 0.5, "p_flat": 0.5, "p_down": 0.5}
        with pytest.raises(ValidationError):
            BullAdvocateOutput.model_validate(bad)

    def test_bull_evidence_ids_min_one(self):
        bad = {**BULL_VALID, "evidence_ids": []}
        with pytest.raises(ValidationError):
            BullAdvocateOutput.model_validate(bad)


# ── Advocate sanity ───────────────────────────────────────────────────────

class TestAdvocateSanity:
    def test_bull_valid_passes(self):
        out = BullAdvocateOutput.model_validate(BULL_VALID).model_dump()
        res = BullAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert res.passed, res.reason

    def test_bear_valid_passes(self):
        out = BearAdvocateOutput.model_validate(BEAR_VALID).model_dump()
        res = BearAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert res.passed, res.reason

    def test_bull_reasoning_must_engage_target(self):
        out = dict(BULL_VALID)
        out["reasoning"] = "Stocks generally go up over time; I am bullish."
        res = BullAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert not res.passed
        assert "target persona" in res.reason.lower()

    def test_bull_no_counterpoint_rejected(self):
        out = dict(BULL_VALID)
        out["strongest_bear_counterpoint"] = "  "
        res = BullAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert not res.passed

    def test_bull_p_down_dominates_without_concession_rejected(self):
        out = dict(BULL_VALID)
        out["p_up"], out["p_flat"], out["p_down"] = 0.20, 0.30, 0.50
        out["reasoning"] = "growth_hunter is too cautious; upside under-weighted per ev-1."
        res = BullAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert not res.passed
        assert "conceding" in res.reason.lower()

    def test_bull_concession_allows_bear_dominance(self):
        out = dict(BULL_VALID)
        out["p_up"], out["p_flat"], out["p_down"] = 0.30, 0.30, 0.40
        out["reasoning"] = (
            "Despite my bull mandate, the growth_hunter evidence supports the bear case here per ev-1."
        )
        res = BullAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert res.passed, res.reason

    def test_bear_p_up_dominates_without_concession_rejected(self):
        out = dict(BEAR_VALID)
        out["p_up"], out["p_flat"], out["p_down"] = 0.50, 0.30, 0.20
        out["reasoning"] = "moat_analyst overstates risk; downside under-weighted per ev-1."
        res = BearAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert not res.passed
        assert "conceding" in res.reason.lower()

    def test_bull_evidence_id_must_resolve(self):
        out = BullAdvocateOutput.model_validate(BULL_VALID).model_dump()
        # ev-1 NOT in provided evidence
        res = BullAdvocateSanity().validate(out, _evidence(["other-id"]))
        assert not res.passed
        assert "ev-1" in res.reason

    @pytest.mark.parametrize(
        "target,semantic_phrase",
        [
            # Each row: persona the bull advocates FOR, semantic phrase that
            # must now satisfy the persona-engagement check (ONDS 3-cycle
            # audit Jun 29: drift was semantic, not adversarial).
            ("moat_analyst", "the competitive advantage is widening per ev-1"),
            ("moat_analyst", "switching costs are durable per ev-1"),
            ("moat_analyst", "network effects deepen per ev-1"),
            ("growth_hunter", "topline expansion is accelerating per ev-1"),
            ("growth_hunter", "TAM penetration is still early per ev-1"),
            ("catalyst_summarizer", "the upcoming earnings release is a tailwind per ev-1"),
            ("short_interest", "short interest positioning is overcrowded per ev-1"),
            ("forensics", "the red flags listed are immaterial per ev-1"),
            ("insider_activity", "the insider buying pattern is informative per ev-1"),
            ("macro_regime", "the macro environment is supportive per ev-1"),
        ],
    )
    def test_bull_semantic_synonyms_engage_target(self, target, semantic_phrase):
        """Bull advocate reasoning must engage the target persona's topic — the
        ONDS 3-cycle audit (Jun 29) showed the strict literal-slug check was
        the dominant wave-2 blocker. Synonyms that engage the topic must now
        pass alongside the canonical slug."""
        out = dict(BULL_VALID)
        out["target_persona"] = target
        out["reasoning"] = semantic_phrase
        res = BullAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert res.passed, f"semantic phrase should pass for {target}: {res.reason}"

    @pytest.mark.parametrize(
        "target,semantic_phrase",
        [
            ("moat_analyst", "the competitive advantage is overstated per ev-1"),
            ("growth_hunter", "revenue growth is decelerating per ev-1"),
            ("catalyst_summarizer", "the upcoming launch is likely to disappoint per ev-1"),
            ("short_interest", "short interest is overcrowded per ev-1"),
            ("forensics", "red flags are material per ev-1"),
            ("insider_activity", "insider selling is informative per ev-1"),
            ("macro_regime", "the macro environment is hostile per ev-1"),
        ],
    )
    def test_bear_semantic_synonyms_engage_target(self, target, semantic_phrase):
        """Bear advocate mirror of bull synonym test (ONDS 3-cycle audit Jun 29)."""
        out = dict(BEAR_VALID)
        out["target_persona"] = target
        out["reasoning"] = semantic_phrase
        res = BearAdvocateSanity().validate(out, _evidence(["ev-1"]))
        assert res.passed, f"semantic phrase should pass for {target}: {res.reason}"


# ── Auditor Pydantic + sanity ─────────────────────────────────────────────

AUDITOR_FLAG_VALID = {
    "flag_type": "CITATION_GAP",
    "target_persona": "growth_hunter",
    "severity": 0.6,
    "description": "growth_hunter concludes durable growth but cites no evidence supporting durability.",
    "evidence_ids": ["ev-1"],
    "taxonomy_mapping": "CITATION_GAP",
}


class TestAuditorPydantic:
    def test_valid_output_round_trips(self):
        m = AuditorOutput.model_validate(
            {"ticker": "X", "flags": [AUDITOR_FLAG_VALID], "summary": "One flag found."}
        )
        assert len(m.flags) == 1
        assert m.flags[0].taxonomy_mapping == FailureTaxonomy.CITATION_GAP

    def test_empty_flags_valid(self):
        m = AuditorOutput.model_validate({"ticker": "X", "flags": [], "summary": "Clean."})
        assert m.flags == []

    def test_taxonomy_must_match_flag_type(self):
        bad = {**AUDITOR_FLAG_VALID, "taxonomy_mapping": "NUMBER_MISUSE"}
        with pytest.raises(ValidationError):
            AuditorFlag.model_validate(bad)

    def test_taxonomy_must_be_auditor_allowed(self):
        # THESIS_INVALIDATED_FUNDAMENTAL is a real outcome FailureTaxonomy member
        # but NOT in the auditor-allowed set.
        bad = {
            **AUDITOR_FLAG_VALID,
            "flag_type": "CITATION_GAP",
            "taxonomy_mapping": "THESIS_INVALIDATED_FUNDAMENTAL",
        }
        with pytest.raises(ValidationError):
            AuditorFlag.model_validate(bad)

    def test_flag_target_must_be_wave1(self):
        bad = {**AUDITOR_FLAG_VALID, "target_persona": "crucible"}
        with pytest.raises(ValidationError):
            AuditorFlag.model_validate(bad)

    def test_severity_range(self):
        bad = {**AUDITOR_FLAG_VALID, "severity": 1.5}
        with pytest.raises(ValidationError):
            AuditorFlag.model_validate(bad)

    def test_auditor_allowed_taxonomy_is_exactly_five(self):
        assert len(AUDITOR_ALLOWED_TAXONOMY) == 5


class TestAuditorSanity:
    def _out(self, flags):
        return {"ticker": "X", "flags": flags, "summary": "audit summary"}

    def test_clean_output_passes(self):
        res = CrossPersonaAuditorSanity().validate(self._out([]), _evidence(["ev-1"]))
        assert res.passed, res.reason

    def test_valid_flag_passes(self):
        flag = AuditorFlag.model_validate(AUDITOR_FLAG_VALID).model_dump()
        res = CrossPersonaAuditorSanity().validate(self._out([flag]), _evidence(["ev-1"]))
        assert res.passed, res.reason

    def test_hallucinated_evidence_id_rejected(self):
        flag = AuditorFlag.model_validate(AUDITOR_FLAG_VALID).model_dump()
        # ev-1 not in provided evidence
        res = CrossPersonaAuditorSanity().validate(self._out([flag]), _evidence(["other"]))
        assert not res.passed
        assert "hallucinated" in res.reason.lower() or "ev-1" in res.reason

    def test_probability_fields_rejected(self):
        out = self._out([])
        out["p_up"] = 0.5
        res = CrossPersonaAuditorSanity().validate(out, _evidence(["ev-1"]))
        assert not res.passed
        assert "probability" in res.reason.lower()

    def test_non_wave1_target_rejected(self):
        flag = dict(AUDITOR_FLAG_VALID)
        flag["target_persona"] = "crucible"
        res = CrossPersonaAuditorSanity().validate(self._out([flag]), _evidence(["ev-1"]))
        assert not res.passed
        assert "wave-1" in res.reason.lower()

    def test_empty_summary_rejected(self):
        out = {"ticker": "X", "flags": [], "summary": "  "}
        res = CrossPersonaAuditorSanity().validate(out, _evidence(["ev-1"]))
        assert not res.passed


# ── Runner contract ───────────────────────────────────────────────────────

class TestRunnerContract:
    def test_bull_runner_models(self):
        r = BullAdvocateRunner()
        from pmacs.schemas.personas import BullAdvocateOutput as M
        assert r.get_pydantic_model() is M
        assert isinstance(r.get_sanity_validator(), BullAdvocateSanity)
        assert r.persona_name == "bull_advocate"
        assert r.grammar_name == "bull_advocate"

    def test_bear_runner_models(self):
        r = BearAdvocateRunner()
        from pmacs.schemas.personas import BearAdvocateOutput as M
        assert r.get_pydantic_model() is M
        assert isinstance(r.get_sanity_validator(), BearAdvocateSanity)

    def test_auditor_runner_models(self):
        r = CrossPersonaAuditorRunner()
        from pmacs.schemas.personas import AuditorOutput as M
        assert r.get_pydantic_model() is M
        assert isinstance(r.get_sanity_validator(), CrossPersonaAuditorSanity)
        assert r.persona_name == "cross_persona_auditor"

    def test_bull_build_prompt_includes_peer_outputs(self):
        r = BullAdvocateRunner()
        peer = SimpleNamespace(
            persona=PersonaName.GROWTH_HUNTER,
            p_up=0.4, p_flat=0.4, p_down=0.2,
            raw_output='{"reasoning": "growth is decelerating"}',
        )
        r.set_peer_outputs([peer])
        evidence = [SimpleNamespace(ticker="X", evidence=[SimpleNamespace(id="ev-1")])]
        prompt = r.build_prompt(evidence)
        assert "growth is decelerating" in prompt
        assert "p_up=0.400" in prompt
        assert "{peer_outputs}" not in prompt

    def test_auditor_build_prompt_includes_cited_ids(self):
        r = CrossPersonaAuditorRunner()
        peer = SimpleNamespace(
            persona=PersonaName.MOAT_ANALYST,
            p_up=0.5, p_flat=0.3, p_down=0.2,
            raw_output='{"reasoning": "moat widening", "evidence_ids": ["ev-1"]}',
        )
        r.set_peer_outputs([peer])
        evidence = [SimpleNamespace(ticker="X", evidence=[SimpleNamespace(id="ev-1")])]
        prompt = r.build_prompt(evidence)
        assert "moat_analyst" in prompt
        assert "ev-1" in prompt


# ── Simulation outputs validate ───────────────────────────────────────────

class TestSimulationOutputs:
    @pytest.mark.parametrize(
        "persona,model_cls",
        [
            ("bull_advocate", BullAdvocateOutput),
            ("bear_advocate", BearAdvocateOutput),
            ("cross_persona_auditor", AuditorOutput),
        ],
    )
    def test_simulation_validates_and_passes_sanity(self, persona, model_cls):
        evidence = [SimpleNamespace(ticker="X", evidence=[SimpleNamespace(id="ev-1")])]
        sim = make_simulation_output(persona, model_cls, evidence, cycle_id="c1")
        assert sim is not None, f"no simulation generator for {persona}"
        # Layer 2
        m = model_cls.model_validate(sim)
        # Layer 3 — pick the right validator
        if persona == "bull_advocate":
            validator = BullAdvocateSanity()
        elif persona == "bear_advocate":
            validator = BearAdvocateSanity()
        else:
            validator = CrossPersonaAuditorSanity()
        res = validator.validate(m.model_dump(), evidence)
        assert res.passed, f"{persona} simulation failed sanity: {res.reason}"


# ── Property tests ────────────────────────────────────────────────────────

class TestProperties:
    def test_advocate_probs_sum_to_one(self):
        for cls, payload in ((BullAdvocateOutput, BULL_VALID), (BearAdvocateOutput, BEAR_VALID)):
            m = cls.model_validate(payload)
            assert abs(m.p_up + m.p_flat + m.p_down - 1.0) < 1e-9

    def test_auditor_output_has_no_probability_fields(self):
        m = AuditorOutput.model_validate(
            {"ticker": "X", "flags": [AUDITOR_FLAG_VALID], "summary": "s"}
        )
        dumped = m.model_dump()
        assert "p_up" not in dumped
        assert "p_flat" not in dumped
        assert "p_down" not in dumped

    def test_wave1_personas_excludes_wave2(self):
        for w2 in (PersonaName.BULL_ADVOCATE, PersonaName.BEAR_ADVOCATE, PersonaName.CROSS_PERSONA_AUDITOR):
            assert w2 not in WAVE1_PERSONAS
