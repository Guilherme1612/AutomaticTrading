"""Unit tests for persona sanity validators.

Tests:
- Each sanity validator with valid data passes
- Each with degenerate distribution fails
- Each with invalid evidence_ids fails
- MacroRegime: regime_confidence <= 0.5 with non-UNCERTAIN fails
- CatalystSummarizer: PENDING with past expected_date fails
- MoatAnalyst: duplicate moat_types fails, high risk + high moat fails
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from pmacs.agents.sanity.base import SanityResult
from pmacs.agents.sanity.macro_regime import MacroRegimeSanity
from pmacs.agents.sanity.catalyst_summarizer import CatalystSummarizerSanity
from pmacs.agents.sanity.moat_analyst import MoatAnalystSanity


def _make_evidence_packet(*ids: str) -> list:
    """Create a mock evidence packet list with the given evidence IDs."""
    evidence_list = [SimpleNamespace(id=eid) for eid in ids]
    packet = SimpleNamespace(evidence=evidence_list)
    return [packet]


# ── MacroRegimeSanity ──


class TestMacroRegimeSanity:
    def setup_method(self):
        self.validator = MacroRegimeSanity()
        self.evidence = _make_evidence_packet("ev-001", "ev-002")

    def _make_output(self, **overrides) -> dict:
        base = {
            "regime": "UNCERTAIN",
            "regime_confidence": 0.5,
            "regime_reasoning": "Mixed signals in the data",
            "reasoning": "Macro regime analysis with mixed signals",
            "p_up": 0.4,
            "p_flat": 0.3,
            "p_down": 0.3,
            "evidence_ids": ["ev-001"],
        }
        base.update(overrides)
        return base

    def test_valid_output_passes(self):
        result = self.validator.validate(self._make_output(regime="EXPANSION", regime_confidence=0.85), self.evidence)
        assert result.passed

    def test_uncertain_low_confidence_passes(self):
        result = self.validator.validate(self._make_output(regime="UNCERTAIN", regime_confidence=0.3, p_up=0.33, p_flat=0.34, p_down=0.33), self.evidence)
        assert result.passed

    def test_non_uncertain_low_confidence_fails(self):
        result = self.validator.validate(self._make_output(regime="EXPANSION", regime_confidence=0.4), self.evidence)
        assert not result.passed
        assert "regime_confidence" in result.reason

    def test_degenerate_distribution_fails(self):
        result = self.validator.validate(self._make_output(p_up=1/3, p_flat=1/3, p_down=1/3), self.evidence)
        assert not result.passed
        assert "degenerate" in result.reason

    def test_non_degenerate_distribution_passes(self):
        result = self.validator.validate(self._make_output(regime="EXPANSION", regime_confidence=0.7, p_up=0.5, p_flat=0.3, p_down=0.2), self.evidence)
        assert result.passed

    def test_invalid_evidence_id_fails(self):
        # Policy change (ONDS 3-cycle audit Jun 30 round 2): hallucinated
        # evidence_ids are STRIPPED in-place and replaced with synthetic
        # normalized-fallback-NNN, so the persona's real signal survives.
        # The audit chain records the swap.
        result = self.validator.validate(self._make_output(evidence_ids=["ev-999"]), self.evidence)
        assert result.passed
        assert len(result.normalized_citations) == 1
        assert result.normalized_citations[0]["from"] == "ev-999"

    def test_empty_reasoning_fails(self):
        result = self.validator.validate(self._make_output(reasoning=""), self.evidence)
        assert not result.passed
        assert "reasoning" in result.reason.lower()


# ── CatalystSummarizerSanity ──


class TestCatalystSummarizerSanity:
    def setup_method(self):
        self.validator = CatalystSummarizerSanity()
        self.evidence = _make_evidence_packet("ev-001", "ev-002")

    def _make_output(self, **overrides) -> dict:
        base = {
            "ticker": "AAPL",
            "reasoning": "Catalyst analysis for AAPL",
            "catalysts": [
                {
                    "catalyst_type": "earnings",
                    "description": "Q4 earnings",
                    "expected_date": "2027-06-15",
                    "status": "PENDING",
                    "thesis_impact": "POSITIVE",
                    "evidence_ids": ["ev-001"],
                },
            ],
            "net_catalyst_outlook": "Positive",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
            "evidence_ids": ["ev-001"],
        }
        base.update(overrides)
        return base

    def test_valid_output_passes(self):
        result = self.validator.validate(self._make_output(), self.evidence)
        assert result.passed

    def test_degenerate_distribution_fails(self):
        output = self._make_output(p_up=1/3, p_flat=1/3, p_down=1/3)
        result = self.validator.validate(output, self.evidence)
        assert not result.passed
        assert "degenerate" in result.reason

    def test_invalid_evidence_id_fails(self):
        # Policy change: hallucinated evidence_ids are STRIPPED, not rejected.
        output = self._make_output(evidence_ids=["ev-999"])
        result = self.validator.validate(output, self.evidence)
        assert result.passed
        assert any(
            c["from"] == "ev-999" for c in result.normalized_citations
        )

    def test_catalyst_invalid_evidence_id_fails(self):
        # Policy change: hallucinated evidence_ids are STRIPPED, not rejected.
        # The persona's real signal (catalyst text, expected_date, etc.) is
        # preserved and the audit chain records the swap.
        output = self._make_output()
        output["catalysts"][0]["evidence_ids"] = ["ev-999"]
        result = self.validator.validate(output, self.evidence)
        assert result.passed
        assert any(
            c["from"] == "ev-999" for c in result.normalized_citations
        )
        # And the in-place mutation replaces the hallucinated ID with synthetic
        assert output["catalysts"][0]["evidence_ids"] == ["normalized-fallback-001"]

    def test_catalyst_count_over_10_fails(self):
        catalysts = [
            {
                "catalyst_type": "earnings",
                "description": f"Cat {i}",
                "expected_date": "2027-06-15",
                "status": "PENDING",
                "thesis_impact": "POSITIVE",
                "evidence_ids": ["ev-001"],
            }
            for i in range(11)
        ]
        output = self._make_output(catalysts=catalysts)
        result = self.validator.validate(output, self.evidence)
        assert not result.passed
        assert "11" in result.reason

    def test_pending_past_date_fails(self):
        past_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        output = self._make_output()
        output["catalysts"][0]["expected_date"] = past_date
        result = self.validator.validate(output, self.evidence)
        assert not result.passed
        assert "past" in result.reason

    def test_resolved_past_date_passes(self):
        past_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        output = self._make_output()
        output["catalysts"][0]["status"] = "RESOLVED_UP"
        output["catalysts"][0]["expected_date"] = past_date
        result = self.validator.validate(output, self.evidence)
        assert result.passed


# ── MoatAnalystSanity ──


class TestMoatAnalystSanity:
    def setup_method(self):
        self.validator = MoatAnalystSanity()
        self.evidence = _make_evidence_packet("ev-001", "ev-002")

    def _make_output(self, **overrides) -> dict:
        base = {
            "ticker": "META",
            "reasoning": "Moat analysis for META",
            "moat_components": [
                {
                    "moat_type": "NETWORK_EFFECTS",
                    "strength": 0.8,
                    "trajectory": "WIDENING",
                    "reasoning": "Strong",
                    "evidence_ids": ["ev-001"],
                },
            ],
            "moat_strength": 0.8,
            "competitive_entry_risk": "LOW",
            "competitive_entry_reasoning": "Hard to replicate",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
            "evidence_ids": ["ev-001"],
        }
        base.update(overrides)
        return base

    def test_valid_output_passes(self):
        result = self.validator.validate(self._make_output(), self.evidence)
        assert result.passed

    def test_degenerate_distribution_fails(self):
        output = self._make_output(p_up=1/3, p_flat=1/3, p_down=1/3)
        result = self.validator.validate(output, self.evidence)
        assert not result.passed
        assert "degenerate" in result.reason

    def test_invalid_evidence_id_fails(self):
        # Policy change: hallucinated evidence_ids are STRIPPED, not rejected.
        output = self._make_output(evidence_ids=["ev-999"])
        result = self.validator.validate(output, self.evidence)
        assert result.passed
        assert any(
            c["from"] == "ev-999" for c in result.normalized_citations
        )

    def test_duplicate_moat_types_fails(self):
        output = self._make_output(
            moat_components=[
                {
                    "moat_type": "NETWORK_EFFECTS",
                    "strength": 0.7,
                    "trajectory": "STABLE",
                    "reasoning": "test",
                    "evidence_ids": ["ev-001"],
                },
                {
                    "moat_type": "NETWORK_EFFECTS",
                    "strength": 0.6,
                    "trajectory": "NARROWING",
                    "reasoning": "test",
                    "evidence_ids": ["ev-001"],
                },
            ],
            moat_strength=0.65,
        )
        result = self.validator.validate(output, self.evidence)
        assert not result.passed
        assert "duplicate" in result.reason

    def test_high_risk_high_moat_fails(self):
        output = self._make_output(
            moat_strength=0.8,
            competitive_entry_risk="HIGH",
        )
        result = self.validator.validate(output, self.evidence)
        assert not result.passed
        assert "HIGH" in result.reason

    def test_high_risk_low_moat_passes(self):
        output = self._make_output(
            moat_components=[
                {
                    "moat_type": "NETWORK_EFFECTS",
                    "strength": 0.4,
                    "trajectory": "NARROWING",
                    "reasoning": "Eroding",
                    "evidence_ids": ["ev-001"],
                },
            ],
            moat_strength=0.4,
            competitive_entry_risk="HIGH",
            p_up=0.2,
            p_flat=0.3,
            p_down=0.5,
        )
        result = self.validator.validate(output, self.evidence)
        assert result.passed

    def test_moat_strength_outside_tolerance_fails(self):
        output = self._make_output(
            moat_components=[
                {
                    "moat_type": "NETWORK_EFFECTS",
                    "strength": 0.5,
                    "trajectory": "STABLE",
                    "reasoning": "test",
                    "evidence_ids": ["ev-001"],
                },
            ],
            moat_strength=0.8,  # avg=0.5, diff=0.3 > 0.15
        )
        result = self.validator.validate(output, self.evidence)
        assert not result.passed
        assert "moat_strength" in result.reason
