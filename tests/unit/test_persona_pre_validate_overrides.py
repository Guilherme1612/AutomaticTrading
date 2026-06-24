"""Unit tests for per-persona _pre_validate() overrides.

Each test simulates the LLM (deepseek-v4-flash on openrouter with
``structured_output: "json_schema"``) emitting a drifted JSON shape, then
verifies the persona's _pre_validate() override normalizes it into a
Pydantic-valid form AND records fixes for the PERSONA_OUTPUT_NORMALIZED
audit event.

These tests do NOT mock the LLM call — they exercise _pre_validate()
directly with a synthetic parsed dict, which is the deterministic half of
the pipeline (Agents.md §3 layer 2: Pydantic validation after the hook).
"""

from __future__ import annotations

from typing import Any

import pytest

from pmacs.agents.bear_advocate import BearAdvocateRunner
from pmacs.agents.bull_advocate import BullAdvocateRunner
from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
from pmacs.agents.cross_persona_auditor import CrossPersonaAuditorRunner
from pmacs.agents.forensics import ForensicsRunner
from pmacs.agents.growth_hunter import GrowthHunterRunner
from pmacs.agents.insider_activity import InsiderActivityRunner
from pmacs.agents.macro_regime import MacroRegimeRunner
from pmacs.agents.moat_analyst import MoatAnalystRunner
from pmacs.agents.short_interest import ShortInterestRunner
from pmacs.agents.valuation_agent import ValuationAgentRunner
from pmacs.schemas.personas import (
    BearAdvocateOutput,
    BullAdvocateOutput,
    CatalystSummarizerOutput,
    GrowthHunterOutput,
    InsiderActivityOutput,
    MacroRegimeOutput,
    MoatAnalystOutput,
    ShortInterestOutput,
)


def _runner(cls, cycle_id: str = "test-cycle-001"):
    """Construct a runner with no audit writer and a synthetic cycle_id."""
    return cls(cycle_id=cycle_id, audit_writer=None, simulation_mode=False)


# --- MoatAnalyst ----------------------------------------------------------

class TestMoatAnalystPreValidate:
    """moat_analyst: type → moat_type, evidence_ids padding."""

    def test_renames_type_to_moat_type_nested(self):
        runner = _runner(MoatAnalystRunner)
        parsed = {
            "ticker": "MSFT",
            "moat_components": [
                {"type": "NETWORK_EFFECTS", "strength": 0.7, "trajectory": "WIDENING",
                 "reasoning": "x" * 50, "evidence_ids": ["e1"]},
                {"type": "SWITCHING_COSTS", "strength": 0.6, "trajectory": "STABLE",
                 "reasoning": "y" * 50, "evidence_ids": ["e2"]},
            ],
            "moat_strength": 0.7,
            "competitive_entry_risk": "MODERATE",
            "competitive_entry_reasoning": "Standard moat",
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        # Every moat_component must now have moat_type instead of type
        for c in result["moat_components"]:
            assert "type" not in c
            assert "moat_type" in c
        # Pydantic-valid
        out = MoatAnalystOutput.model_validate(result)
        assert out.ticker == "MSFT"
        assert all(c.moat_type for c in out.moat_components)

    def test_pads_empty_evidence_ids(self):
        runner = _runner(MoatAnalystRunner)
        parsed = {
            "ticker": "MSFT",
            "moat_components": [
                {"moat_type": "NETWORK_EFFECTS", "strength": 0.7, "trajectory": "WIDENING",
                 "reasoning": "ok", "evidence_ids": []},
            ],
            "moat_strength": 0.7,
            "competitive_entry_risk": "LOW",
            "competitive_entry_reasoning": "ok",
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": [],
        }
        result = runner._pre_validate(parsed)
        out = MoatAnalystOutput.model_validate(result)
        assert len(out.evidence_ids) >= 1
        for c in out.moat_components:
            assert len(c.evidence_ids) >= 1


# --- CatalystSummarizer ---------------------------------------------------

class TestCatalystSummarizerPreValidate:
    """catalyst_summarizer: thesis_impact inject, unknown catalyst_type default."""

    def test_injects_thesis_impact_neutral(self):
        runner = _runner(CatalystSummarizerRunner)
        parsed = {
            "ticker": "NBIS",
            "catalysts": [
                {"catalyst_type": "earnings", "description": "Q4 print",
                 "expected_date": None, "status": "PENDING"},  # NO thesis_impact
            ],
            "net_catalyst_outlook": "neutral",
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        # thesis_impact injected
        assert result["catalysts"][0].get("thesis_impact") == "NEUTRAL"
        out = CatalystSummarizerOutput.model_validate(result)
        assert out.catalysts[0].thesis_impact == "NEUTRAL"

    def test_unknown_catalyst_type_defaults_to_first_enum_member(self):
        runner = _runner(CatalystSummarizerRunner)
        parsed = {
            "ticker": "ONDS",
            "catalysts": [
                {"catalyst_type": "hyperscaler_deal", "description": "Big deal",
                 "expected_date": None, "status": "PENDING", "thesis_impact": "POSITIVE"},
            ],
            "net_catalyst_outlook": "x",
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        # Unknown value → mapped to first member ("earnings")
        assert result["catalysts"][0]["catalyst_type"] == "earnings"
        out = CatalystSummarizerOutput.model_validate(result)
        assert out.catalysts[0].catalyst_type == "earnings"


# --- MacroRegime ----------------------------------------------------------

class TestMacroRegimePreValidate:
    """macro_regime: lowercase literal enums → canonical."""

    def test_lowercase_yield_curve_signal_normalized(self):
        runner = _runner(MacroRegimeRunner)
        parsed = {
            "regime": "EXPANSION", "regime_confidence": 0.6,
            "regime_reasoning": "ok",
            "yield_curve_signal": "flat",  # lowercase drift
            "vix_regime": "elevated",      # lowercase drift
            "sector_rotation_summary": "ok",
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        assert result["yield_curve_signal"] == "FLAT"
        assert result["vix_regime"] == "ELEVATED"
        out = MacroRegimeOutput.model_validate(result)
        assert out.yield_curve_signal == "FLAT"
        assert out.vix_regime == "ELEVATED"

    def test_unknown_vix_regime_falls_back_to_first_enum_member(self):
        runner = _runner(MacroRegimeRunner)
        parsed = {
            "regime": "EXPANSION", "regime_confidence": 0.5,
            "regime_reasoning": "ok",
            "yield_curve_signal": "NORMAL",
            "vix_regime": "no_data",  # unknown value
            "sector_rotation_summary": "ok",
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        assert result["vix_regime"] == "LOW"  # first member of LOW/MODERATE/ELEVATED/CRISIS
        out = MacroRegimeOutput.model_validate(result)
        assert out.vix_regime == "LOW"


# --- GrowthHunter ---------------------------------------------------------

class TestGrowthHunterPreValidate:
    """growth_hunter: lowercase Literal enums → canonical."""

    def test_lowercase_growth_enums_normalized(self):
        runner = _runner(GrowthHunterRunner)
        parsed = {
            "ticker": "PLTR",
            "revenue_yoy_pct": 0.20,
            "revenue_acceleration": "accelerating",   # lowercase drift
            "gross_margin_pct": 0.40,
            "gross_margin_trend": "expanding",        # lowercase drift
            "tam_penetration_pct": 0.05,
            "growth_durability": "high",              # lowercase drift
            "growth_durability_reasoning": "ok",
            "key_risk_to_growth": "competition",
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        assert result["revenue_acceleration"] == "ACCELERATING"
        assert result["gross_margin_trend"] == "EXPANDING"
        assert result["growth_durability"] == "HIGH"
        out = GrowthHunterOutput.model_validate(result)
        assert out.revenue_acceleration == "ACCELERATING"
        assert out.growth_durability == "HIGH"


# --- BullAdvocate + BearAdvocate -----------------------------------------

class TestAdvocatePreValidate:
    """advocates: reasoning / counterpoint strings exceed max_length."""

    def _make_advocate_parsed(self, ticker: str) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "target_persona": "growth_hunter",
            "p_up": 0.45, "p_flat": 0.30, "p_down": 0.25,
            "reasoning": "x" * 1000,  # max=600 → drift
            "strongest_bear_counterpoint": "y" * 500,  # bull uses bear-counterpoint, max=300
            "evidence_ids": ["e1"],
        }

    def test_bull_truncates_long_reasoning_and_counterpoint(self):
        runner = _runner(BullAdvocateRunner)
        result = runner._pre_validate(self._make_advocate_parsed("MSFT"))
        assert len(result["reasoning"]) <= 600
        assert len(result["strongest_bear_counterpoint"]) <= 300
        out = BullAdvocateOutput.model_validate(result)
        assert out.ticker == "MSFT"

    def test_bear_truncates_long_reasoning_and_counterpoint(self):
        """Bear advocate's counterpart field is strongest_bull_counterpoint (max=300)."""
        runner = _runner(BearAdvocateRunner)
        parsed = {
            "ticker": "AMZN",
            "target_persona": "growth_hunter",
            "p_up": 0.45, "p_flat": 0.30, "p_down": 0.25,
            "reasoning": "x" * 1000,  # max=600 → drift
            "strongest_bull_counterpoint": "y" * 500,  # max=300 → drift
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        assert len(result["reasoning"]) <= 600
        assert len(result["strongest_bull_counterpoint"]) <= 300
        out = BearAdvocateOutput.model_validate(result)
        assert out.ticker == "AMZN"

    def test_advocates_pad_empty_evidence_ids(self):
        runner_b = _runner(BullAdvocateRunner)
        runner_bear = _runner(BearAdvocateRunner)
        for runner, ticker in [(runner_b, "MSFT"), (runner_bear, "AMZN")]:
            parsed = self._make_advocate_parsed(ticker)
            parsed["evidence_ids"] = []
            result = runner._pre_validate(parsed)
            assert len(result["evidence_ids"]) >= 1


# --- CrossPersonaAuditor -------------------------------------------------

class TestCrossPersonaAuditorPreValidate:
    """cross_persona_auditor: cycle_id int → str, description truncation."""

    def test_coerces_int_cycle_id_to_str(self):
        runner = _runner(CrossPersonaAuditorRunner)
        parsed = {
            "ticker": "MSFT",
            "flags": [
                {"flag_type": "NARRATIVE_DRIFT", "severity": 0.5,
                 "description": "ok",
                 "cycle_id": 12345,  # int drift
                 "evidence_ids": ["e1"]},
            ],
            "summary": "ok",
        }
        result = runner._pre_validate(parsed)
        assert isinstance(result["flags"][0]["cycle_id"], str)
        assert result["flags"][0]["cycle_id"] == "12345"

    def test_truncates_long_flag_description(self):
        runner = _runner(CrossPersonaAuditorRunner)
        parsed = {
            "ticker": "MSFT",
            "flags": [
                {"flag_type": "NARRATIVE_DRIFT", "severity": 0.5,
                 "description": "z" * 1000,  # way over max
                 "cycle_id": "c1",
                 "evidence_ids": ["e1"]},
            ],
            "summary": "ok",
        }
        result = runner._pre_validate(parsed)
        assert len(result["flags"][0]["description"]) <= 500


# --- ValuationAgent ------------------------------------------------------

class TestValuationAgentPreValidate:
    """valuation_agent: per-scenario rationale > 800 chars."""

    def test_truncates_scenario_rationale(self):
        runner = _runner(ValuationAgentRunner)
        long_rationale = "x" * 1500
        parsed = {
            "ticker": "MSFT",
            "horizon_months": 12,
            "bull": {
                "revenue_growth_path_pct": 0.20,
                "margin_trajectory": "STABLE",
                "margin_delta_pct": 0.01,
                "ebitda_margin_at_horizon_pct": 0.25,
                "acquisition_revenue_contribution_pct": 0.0,
                "acquisition_confidence": "NONE",
                "exit_multiple": 18.0,
                "rationale": long_rationale,
                "probability_of_occurrence": 0.30,
                "evidence_ids": ["e1"],
            },
            "base": {
                "revenue_growth_path_pct": 0.12,
                "margin_trajectory": "STABLE",
                "margin_delta_pct": 0.0,
                "ebitda_margin_at_horizon_pct": 0.22,
                "acquisition_revenue_contribution_pct": 0.0,
                "acquisition_confidence": "NONE",
                "exit_multiple": 15.0,
                "rationale": long_rationale,
                "probability_of_occurrence": 0.40,
                "evidence_ids": ["e1"],
            },
            "bear": {
                "revenue_growth_path_pct": 0.04,
                "margin_trajectory": "COMPRESSING",
                "margin_delta_pct": -0.02,
                "ebitda_margin_at_horizon_pct": 0.18,
                "acquisition_revenue_contribution_pct": 0.0,
                "acquisition_confidence": "NONE",
                "exit_multiple": 10.0,
                "rationale": long_rationale,
                "probability_of_occurrence": 0.30,
                "evidence_ids": ["e1"],
            },
            "data_gaps": ["ok"],
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        for scenario in ("bull", "base", "bear"):
            assert len(result[scenario]["rationale"]) <= 800


# --- Forensics / InsiderActivity / ShortInterest -------------------------

class TestEvidenceIdsPaddingPersonas:
    """forensics, insider_activity, short_interest: only need evidence_ids padding."""

    @pytest.mark.parametrize(
        "runner_cls",
        [ForensicsRunner, InsiderActivityRunner, ShortInterestRunner],
    )
    def test_pads_empty_evidence_ids(self, runner_cls):
        runner = _runner(runner_cls)
        # Build a minimal valid-ish dict with empty evidence_ids — _pre_validate
        # pads it; Pydantic model_validate is then expected to succeed.
        if runner_cls is ForensicsRunner:
            parsed = {
                "ticker": "MSFT", "red_flags": [], "red_flag_count": 0,
                "overall_accounting_quality": "INSUFFICIENT_DATA",
                "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
                "evidence_ids": [],
            }
        elif runner_cls is InsiderActivityRunner:
            parsed = {
                "ticker": "MSFT", "transactions": [], "signal": "NO_SIGNAL",
                "signal_reasoning": "ok",
                "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
                "evidence_ids": [],
            }
        else:  # ShortInterestRunner
            parsed = {
                "ticker": "MSFT",
                "short_pct_float": None, "days_to_cover": None,
                "short_change_pct": None,
                "anomaly": "INSUFFICIENT_DATA", "anomaly_reasoning": "ok",
                "p_up": 0.3, "p_flat": 0.4, "p_down": 0.3,
                "evidence_ids": [],
            }
        result = runner._pre_validate(parsed)
        assert len(result["evidence_ids"]) >= 1
        # Pydantic-valid
        from pmacs.schemas.personas import (
            ForensicsOutput,
            InsiderActivityOutput,
            ShortInterestOutput,
        )
        out_map = {
            ForensicsRunner: ForensicsOutput,
            InsiderActivityRunner: InsiderActivityOutput,
            ShortInterestRunner: ShortInterestOutput,
        }
        out_map[runner_cls].model_validate(result)


# --- End-to-end: schema validation passes after _pre_validate ------------

class TestPreValidateProducesPydanticValidOutput:
    """After _pre_validate, the LLM-emitted drift shape passes Pydantic validation."""

    def test_macro_regime_full_drift_shape_validates(self):
        runner = _runner(MacroRegimeRunner)
        # Simulate a fully-drifted LLM output
        parsed = {
            "regime": "expansion", "regime_confidence": 0.5,
            "regime_reasoning": "ok",
            "yield_curve_signal": "no_data",  # unknown → default
            "vix_regime": "low",  # lowercase
            "sector_rotation_summary": "ok",
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2,
            "evidence_ids": [],
        }
        result = runner._pre_validate(parsed)
        out = MacroRegimeOutput.model_validate(result)
        assert out.yield_curve_signal == "NORMAL"  # first member of NORMAL/FLAT/INVERTED
        assert out.vix_regime == "LOW"

    def test_growth_hunter_full_drift_shape_validates(self):
        runner = _runner(GrowthHunterRunner)
        parsed = {
            "ticker": "PLTR",
            "revenue_yoy_pct": None,
            "revenue_acceleration": "unknown",
            "gross_margin_pct": None,
            "gross_margin_trend": "stable",
            "tam_penetration_pct": None,
            "growth_durability": "moderate",
            "growth_durability_reasoning": "ok",
            "key_risk_to_growth": "ok",
            "p_up": 0.4, "p_flat": 0.4, "p_down": 0.2,
            "evidence_ids": ["e1"],
        }
        result = runner._pre_validate(parsed)
        out = GrowthHunterOutput.model_validate(result)
        assert out.revenue_acceleration == "UNKNOWN"  # canonical form
        assert out.gross_margin_trend == "STABLE"
        assert out.growth_durability == "MODERATE"
