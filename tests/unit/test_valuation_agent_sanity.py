"""Unit tests for the ValuationAgent sanity validator (Agents.md §13b, §3).

Verifies evidence_id resolution (nested scenario blocks + top-level), scenario-
probability sum, horizon bounds, exit-multiple/margin bounds, margin-trajectory
sign agreement, acquisition-confidence+data_gaps requirement, rationale citation,
degenerate-prob rejection, and bull>=base>=bear growth ordering.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pmacs.agents.sanity.valuation_agent import ValuationAgentSanity


def _evidence(*ids: str) -> list:
    ev_list = [SimpleNamespace(id=eid) for eid in ids]
    return [SimpleNamespace(evidence=ev_list)]


def _scenario(
    *, g=0.12, traj="STABLE", delta=0.0, margin=0.22, acq=0.0, acq_conf="NONE",
    exit_mult=15.0, exit_sales=None, prob=0.40, rationale="base case evidence=e1", ev_id="e1",
) -> dict:
    return {
        "revenue_growth_path_pct": g,
        "margin_trajectory": traj,
        "margin_delta_pct": delta,
        "ebitda_margin_at_horizon_pct": margin,
        "acquisition_revenue_contribution_pct": acq,
        "acquisition_confidence": acq_conf,
        "exit_multiple": exit_mult,
        "exit_sales_multiple": exit_sales,
        "rationale": rationale,
        "probability_of_occurrence": prob,
        "evidence_ids": [ev_id],
    }


def _output(**overrides) -> dict:
    base = {
        "ticker": "X",
        "horizon_months": 12,
        "bull": _scenario(g=0.20, traj="EXPANDING", delta=0.02, margin=0.25,
                          exit_mult=18.0, prob=0.30, rationale="bull e1 growth accelerating"),
        "base": _scenario(g=0.12, prob=0.40, rationale="base e1 consensus growth"),
        "bear": _scenario(g=0.04, traj="COMPRESSING", delta=-0.02, margin=0.18,
                          exit_mult=10.0, prob=0.30, rationale="bear e1 growth decelerating"),
        "data_gaps": ["management guidance: N/A, using analyst consensus proxy"],
        "evidence_ids": ["e1"],
    }
    base.update(overrides)
    return base


class TestValuationAgentSanityHappy:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1", "e2")

    def test_valid_output_passes(self):
        r = self.validator.validate(_output(), self.evidence)
        assert r.passed, r.reason

    def test_multiple_evidence_ids_resolve(self):
        out = _output()
        out["bull"]["evidence_ids"] = ["e1"]
        out["bull"]["rationale"] = "bull e1 growth accelerating"
        out["base"]["evidence_ids"] = ["e2"]
        out["base"]["rationale"] = "base e2 consensus growth"
        out["bear"]["evidence_ids"] = ["e1"]
        out["bear"]["rationale"] = "bear e1 growth decelerating"
        out["evidence_ids"] = ["e1", "e2"]
        r = self.validator.validate(out, self.evidence)
        assert r.passed, r.reason


class TestValuationAgentSanityEvidenceResolution:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def test_unknown_scenario_evidence_id_fails(self):
        out = _output()
        out["bull"]["evidence_ids"] = ["eX"]
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "eX" in (r.reason or "")

    def test_unknown_top_level_evidence_id_fails(self):
        out = _output()
        out["evidence_ids"] = ["e1", "eY"]
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "eY" in (r.reason or "")


class TestValuationAgentSanityBounds:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def test_horizon_below_six_fails(self):
        r = self.validator.validate(_output(horizon_months=5), self.evidence)
        assert not r.passed
        assert "horizon" in (r.reason or "").lower()

    def test_horizon_above_twelve_fails(self):
        r = self.validator.validate(_output(horizon_months=13), self.evidence)
        assert not r.passed
        assert "horizon" in (r.reason or "").lower()

    def test_exit_multiple_below_half_fails(self):
        out = _output()
        out["bull"]["exit_multiple"] = 0.1
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "exit_multiple" in (r.reason or "")

    def test_exit_multiple_above_eighty_fails(self):
        out = _output()
        out["bull"]["exit_multiple"] = 90.0
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "exit_multiple" in (r.reason or "")

    def test_ebitda_margin_below_floor_fails(self):
        out = _output()
        out["bear"]["ebitda_margin_at_horizon_pct"] = -0.50
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "ebitda_margin" in (r.reason or "")

    def test_ebitda_margin_above_ceiling_fails(self):
        out = _output()
        out["bull"]["ebitda_margin_at_horizon_pct"] = 0.95
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "ebitda_margin" in (r.reason or "")


class TestValuationAgentSanityProbabilities:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def test_probs_sum_outside_tolerance_fails(self):
        out = _output()
        out["bull"]["probability_of_occurrence"] = 0.30
        out["base"]["probability_of_occurrence"] = 0.20
        out["bear"]["probability_of_occurrence"] = 0.10  # sum 0.60, far from 1.0
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "probabilit" in (r.reason or "").lower()

    def test_degenerate_all_mass_on_bull_fails(self):
        out = _output()
        out["bull"]["probability_of_occurrence"] = 1.0
        out["base"]["probability_of_occurrence"] = 0.0
        out["bear"]["probability_of_occurrence"] = 0.0
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "degenerate" in (r.reason or "").lower()

    def test_degenerate_all_mass_on_bear_fails(self):
        out = _output()
        out["bull"]["probability_of_occurrence"] = 0.0
        out["base"]["probability_of_occurrence"] = 0.0
        out["bear"]["probability_of_occurrence"] = 1.0
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "degenerate" in (r.reason or "").lower()


class TestValuationAgentSanityMarginTrajectory:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def test_expanding_with_negative_delta_fails(self):
        out = _output()
        out["bull"]["margin_trajectory"] = "EXPANDING"
        out["bull"]["margin_delta_pct"] = -0.03
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "EXPANDING" in (r.reason or "")

    def test_compressing_with_positive_delta_fails(self):
        out = _output()
        out["bear"]["margin_trajectory"] = "COMPRESSING"
        out["bear"]["margin_delta_pct"] = 0.03
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "COMPRESSING" in (r.reason or "")


class TestValuationAgentSanityAcquisition:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def test_acquisition_with_high_confidence_fails(self):
        out = _output()
        out["bull"]["acquisition_revenue_contribution_pct"] = 0.05
        out["bull"]["acquisition_confidence"] = "HIGH"
        out["data_gaps"] = ["acquisition inferred narratively"]
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "confidence" in (r.reason or "")

    def test_acquisition_without_data_gaps_note_fails(self):
        out = _output()
        out["base"]["acquisition_revenue_contribution_pct"] = 0.05
        out["base"]["acquisition_confidence"] = "LOW"
        out["data_gaps"] = ["management guidance: N/A"]  # no "acqui" note
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "acqui" in (r.reason or "").lower() or "data_gaps" in (r.reason or "")

    def test_acquisition_low_confidence_with_note_passes(self):
        out = _output()
        out["base"]["acquisition_revenue_contribution_pct"] = 0.05
        out["base"]["acquisition_confidence"] = "LOW"
        out["data_gaps"] = ["acquisition inferred narratively from press release"]
        r = self.validator.validate(out, self.evidence)
        assert r.passed, r.reason


class TestValuationAgentSanityRationaleCitation:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def test_rationale_not_citing_evidence_id_fails(self):
        out = _output()
        out["bull"]["rationale"] = "bull case: growth accelerating, no citation"
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "rationale" in (r.reason or "").lower()


class TestValuationAgentSanityGrowthOrdering:
    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def test_bull_below_base_fails(self):
        out = _output()
        out["bull"]["revenue_growth_path_pct"] = 0.05
        out["base"]["revenue_growth_path_pct"] = 0.12  # bull < base
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "ordered" in (r.reason or "").lower()

    def test_bear_above_base_fails(self):
        out = _output()
        out["bear"]["revenue_growth_path_pct"] = 0.20  # bear > base
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "ordered" in (r.reason or "").lower()


class TestValuationAgentSanityPreProfit:
    """V-004 — pre-profit scenarios (margin <= 0) require exit_sales_multiple.

    Most current universe tickers (NBIS, ONDS) are pre-profit per
    `crucible_as_primary_filter.md` — this path is the default, not the edge
    case. The pre-profit fixture (margin=-0.30) exercises the EV/Sales branch
    in `_persona_checks` and the engine's pre-profit path. The pre-existing
    _scenario() helper uses margin=0.22 (profitable), so the pre-profit code
    paths were never executed by the suite.
    """

    def setup_method(self):
        self.validator = ValuationAgentSanity()
        self.evidence = _evidence("e1")

    def _pre_profit_output(self, **overrides):
        """Build an output where bull/base/bear are all pre-profit (margin=-0.30)."""
        base = {
            "ticker": "NBIS",
            "horizon_months": 12,
            "bull": _scenario(g=0.45, traj="EXPANDING", delta=0.05, margin=-0.20,
                              exit_mult=None, exit_sales=18.0,
                              prob=0.30, rationale="bull e1 hypergrowth on revenue", ev_id="e1"),
            "base": _scenario(g=0.25, margin=-0.30,
                              exit_mult=None, exit_sales=12.0,
                              prob=0.40, rationale="base e1 consensus pre-profit ramp", ev_id="e1"),
            "bear": _scenario(g=0.05, traj="COMPRESSING", delta=-0.02, margin=-0.40,
                              exit_mult=None, exit_sales=6.0,
                              prob=0.30, rationale="bear e1 growth decelerating", ev_id="e1"),
            "data_gaps": ["management guidance: N/A, using analyst consensus proxy"],
            "evidence_ids": ["e1"],
        }
        base.update(overrides)
        return base

    def test_pre_profit_with_exit_sales_passes(self):
        r = self.validator.validate(self._pre_profit_output(), self.evidence)
        assert r.passed, r.reason

    def test_pre_profit_missing_exit_sales_fails(self):
        """When margin <= 0, exit_sales_multiple is REQUIRED for the EV/Sales path."""
        out = self._pre_profit_output()
        out["base"]["exit_sales_multiple"] = None  # missing
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "exit_sales_multiple" in (r.reason or "")
        assert "pre-profit" in (r.reason or "").lower()

    def test_pre_profit_exit_sales_above_ceiling_fails(self):
        """exit_sales_multiple must be in [0, 100] per spec §13b."""
        out = self._pre_profit_output()
        out["base"]["exit_sales_multiple"] = 150.0
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "exit_sales_multiple" in (r.reason or "")
        assert "100" in (r.reason or "")

    def test_pre_profit_negative_exit_sales_fails(self):
        out = self._pre_profit_output()
        out["base"]["exit_sales_multiple"] = -2.0
        r = self.validator.validate(out, self.evidence)
        assert not r.passed
        assert "exit_sales_multiple" in (r.reason or "")


class TestValuationAgentSchemaOrdering:
    """V-003 — schema-level mirror of the sanity-layer bull>=base>=bear check.

    The schema validator in `pmacs/schemas/personas.py:_check_invariants` now
    enforces the same ordering invariant as the sanity layer, so a producer
    that bypasses sanity still cannot pass a wrong-ordered output.
    """

    def test_bull_below_base_fails_at_schema(self):
        """Pydantic model_validator on ValuationAgentOutput must reject
        bull < base ordering, mirroring the sanity check.
        """
        from pydantic import ValidationError
        from pmacs.schemas.personas import ValuationAgentOutput

        base = {
            "ticker": "X",
            "horizon_months": 12,
            "bull": _scenario(g=0.05, margin=0.22, prob=0.30, rationale="bull e1",
                              ev_id="e1"),
            "base": _scenario(g=0.12, prob=0.40, rationale="base e1", ev_id="e1"),
            "bear": _scenario(g=0.04, margin=0.18, prob=0.30, rationale="bear e1",
                              ev_id="e1"),
            "evidence_ids": ["e1"],
        }
        with pytest.raises(ValidationError, match="ordered bull>=base>=bear"):
            ValuationAgentOutput(**base)
