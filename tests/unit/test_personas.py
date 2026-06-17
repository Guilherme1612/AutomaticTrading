"""Unit tests for persona output schemas.

Tests:
- Each output model instantiates with valid data
- Probability sum validators reject invalid sums
- MacroRegime regime_confidence rule
- MoatAnalyst moat_strength vs component avg
- CatalystSummarizer catalyst count limit
- MoatAnalyst duplicate moat_type rejection
- MoatAnalyst high entry risk + high moat strength rejection
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pmacs.schemas.personas import (
    CatalystEntry,
    CatalystSummarizerOutput,
    MacroRegimeOutput,
    MoatAnalystOutput,
    MoatComponent,
)


# ── MacroRegimeOutput ──


class TestMacroRegimeOutput:
    def test_valid_expansion(self):
        out = MacroRegimeOutput(
            regime="EXPANSION",
            regime_confidence=0.85,
            regime_reasoning="GDP growing, rates stable",
            yield_curve_signal="NORMAL",
            vix_regime="LOW",
            sector_rotation_summary="Tech leading",
            p_up=0.5,
            p_flat=0.3,
            p_down=0.2,
            evidence_ids=["ev-001"],
        )
        assert out.regime == "EXPANSION"
        assert out.regime_confidence == 0.85

    def test_valid_uncertain(self):
        out = MacroRegimeOutput(
            regime="UNCERTAIN",
            regime_confidence=0.3,
            regime_reasoning="Signals mixed",
            yield_curve_signal="FLAT",
            vix_regime="MODERATE",
            sector_rotation_summary="No clear rotation",
            p_up=0.33,
            p_flat=0.34,
            p_down=0.33,
            evidence_ids=["ev-001"],
        )
        assert out.regime == "UNCERTAIN"

    def test_probability_sum_rejects(self):
        with pytest.raises(ValidationError, match="probabilities sum"):
            MacroRegimeOutput(
                regime="EXPANSION",
                regime_confidence=0.8,
                regime_reasoning="test",
                yield_curve_signal="NORMAL",
                vix_regime="LOW",
                sector_rotation_summary="test",
                p_up=0.5,
                p_flat=0.3,
                p_down=0.05,  # sum 0.85: >0.10 off -> rejected (not auto-normalized)
                evidence_ids=["ev-001"],
            )

    def test_empty_evidence_ids_rejects(self):
        with pytest.raises(ValidationError):
            MacroRegimeOutput(
                regime="EXPANSION",
                regime_confidence=0.8,
                regime_reasoning="test",
                yield_curve_signal="NORMAL",
                vix_regime="LOW",
                sector_rotation_summary="test",
                p_up=0.5,
                p_flat=0.3,
                p_down=0.2,
                evidence_ids=[],
            )

    def test_all_regime_values_valid(self):
        for regime in ["EXPANSION", "LATE_CYCLE", "CONTRACTION", "RECOVERY", "REGIME_SHIFT", "UNCERTAIN"]:
            out = MacroRegimeOutput(
                regime=regime,
                regime_confidence=0.7,
                regime_reasoning=f"test for {regime}",
                yield_curve_signal="NORMAL",
                vix_regime="LOW",
                sector_rotation_summary="test",
                p_up=0.4,
                p_flat=0.3,
                p_down=0.3,
                evidence_ids=["ev-001"],
            )
            assert out.regime == regime


# ── CatalystSummarizerOutput ──


class TestCatalystSummarizerOutput:
    def _make_catalyst(self, **overrides) -> dict:
        base = {
            "catalyst_type": "earnings",
            "description": "Q4 earnings report",
            "expected_date": "2026-06-15",
            "status": "PENDING",
            "thesis_impact": "POSITIVE",
            "evidence_ids": ["ev-001"],
        }
        base.update(overrides)
        return base

    def test_valid_output(self):
        out = CatalystSummarizerOutput(
            ticker="AAPL",
            catalysts=[self._make_catalyst()],
            net_catalyst_outlook="Positive bias from upcoming earnings",
            p_up=0.5,
            p_flat=0.3,
            p_down=0.2,
            evidence_ids=["ev-001"],
        )
        assert out.ticker == "AAPL"
        assert len(out.catalysts) == 1

    def test_catalyst_count_at_limit(self):
        catalysts = [self._make_catalyst(description=f"Cat {i}") for i in range(10)]
        out = CatalystSummarizerOutput(
            ticker="AAPL",
            catalysts=catalysts,
            net_catalyst_outlook="Mixed",
            p_up=0.4,
            p_flat=0.3,
            p_down=0.3,
            evidence_ids=["ev-001"],
        )
        assert len(out.catalysts) == 10

    def test_catalyst_count_over_limit(self):
        catalysts = [self._make_catalyst(description=f"Cat {i}") for i in range(11)]
        with pytest.raises(ValidationError):
            CatalystSummarizerOutput(
                ticker="AAPL",
                catalysts=catalysts,
                net_catalyst_outlook="Mixed",
                p_up=0.4,
                p_flat=0.3,
                p_down=0.3,
                evidence_ids=["ev-001"],
            )

    def test_probability_sum_rejects(self):
        with pytest.raises(ValidationError, match="probabilities sum"):
            CatalystSummarizerOutput(
                ticker="AAPL",
                catalysts=[self._make_catalyst()],
                net_catalyst_outlook="test",
                p_up=0.3,
                p_flat=0.3,
                p_down=0.3,
                evidence_ids=["ev-001"],
            )


# ── MoatAnalystOutput ──


class TestMoatAnalystOutput:
    def _make_component(self, **overrides) -> dict:
        base = {
            "moat_type": "NETWORK_EFFECTS",
            "strength": 0.8,
            "trajectory": "WIDENING",
            "reasoning": "Strong network effects",
            "evidence_ids": ["ev-001"],
        }
        base.update(overrides)
        return base

    def test_valid_output(self):
        out = MoatAnalystOutput(
            ticker="META",
            moat_components=[self._make_component()],
            moat_strength=0.8,
            competitive_entry_risk="LOW",
            competitive_entry_reasoning="Hard to replicate social graph",
            p_up=0.5,
            p_flat=0.3,
            p_down=0.2,
            evidence_ids=["ev-001"],
        )
        assert out.ticker == "META"
        assert out.moat_strength == 0.8

    def test_moat_strength_vs_avg_rejects(self):
        with pytest.raises(ValidationError, match="moat_strength"):
            MoatAnalystOutput(
                ticker="TEST",
                moat_components=[
                    self._make_component(moat_type="NETWORK_EFFECTS", strength=0.5),
                    self._make_component(moat_type="SWITCHING_COSTS", strength=0.5),
                ],
                moat_strength=0.9,  # avg=0.5, diff=0.4 > 0.15
                competitive_entry_risk="LOW",
                competitive_entry_reasoning="test",
                p_up=0.4,
                p_flat=0.3,
                p_down=0.3,
                evidence_ids=["ev-001"],
            )

    def test_moat_strength_within_tolerance(self):
        out = MoatAnalystOutput(
            ticker="TEST",
            moat_components=[
                self._make_component(moat_type="NETWORK_EFFECTS", strength=0.7),
                self._make_component(moat_type="SWITCHING_COSTS", strength=0.6),
            ],
            moat_strength=0.7,  # avg=0.65, diff=0.05 <= 0.15
            competitive_entry_risk="LOW",
            competitive_entry_reasoning="test",
            p_up=0.4,
            p_flat=0.3,
            p_down=0.3,
            evidence_ids=["ev-001"],
        )
        assert out.moat_strength == 0.7

    def test_duplicate_moat_type_rejects(self):
        with pytest.raises(ValidationError, match="duplicate moat_type"):
            MoatAnalystOutput(
                ticker="TEST",
                moat_components=[
                    self._make_component(moat_type="NETWORK_EFFECTS", strength=0.7),
                    self._make_component(moat_type="NETWORK_EFFECTS", strength=0.6),
                ],
                moat_strength=0.65,
                competitive_entry_risk="LOW",
                competitive_entry_reasoning="test",
                p_up=0.4,
                p_flat=0.3,
                p_down=0.3,
                evidence_ids=["ev-001"],
            )

    def test_high_risk_high_moat_rejects(self):
        with pytest.raises(ValidationError, match="competitive_entry_risk HIGH"):
            MoatAnalystOutput(
                ticker="TEST",
                moat_components=[self._make_component(strength=0.8)],
                moat_strength=0.75,  # >= 0.7 with HIGH risk
                competitive_entry_risk="HIGH",
                competitive_entry_reasoning="Competitors closing in",
                p_up=0.3,
                p_flat=0.3,
                p_down=0.4,
                evidence_ids=["ev-001"],
            )

    def test_high_risk_low_moat_accepts(self):
        out = MoatAnalystOutput(
            ticker="TEST",
            moat_components=[self._make_component(strength=0.4)],
            moat_strength=0.4,  # < 0.7 with HIGH risk -> OK
            competitive_entry_risk="HIGH",
            competitive_entry_reasoning="Weak moat",
            p_up=0.2,
            p_flat=0.3,
            p_down=0.5,
            evidence_ids=["ev-001"],
        )
        assert out.competitive_entry_risk == "HIGH"

    def test_empty_moat_components_rejects(self):
        with pytest.raises(ValidationError):
            MoatAnalystOutput(
                ticker="TEST",
                moat_components=[],
                moat_strength=0.5,
                competitive_entry_risk="MODERATE",
                competitive_entry_reasoning="test",
                p_up=0.4,
                p_flat=0.3,
                p_down=0.3,
                evidence_ids=["ev-001"],
            )

    def test_probability_sum_rejects(self):
        with pytest.raises(ValidationError, match="probabilities sum"):
            MoatAnalystOutput(
                ticker="TEST",
                moat_components=[self._make_component()],
                moat_strength=0.8,
                competitive_entry_risk="LOW",
                competitive_entry_reasoning="test",
                p_up=0.5,
                p_flat=0.3,
                p_down=0.05,  # sum 0.85: >0.10 off -> rejected (not auto-normalized)
                evidence_ids=["ev-001"],
            )
