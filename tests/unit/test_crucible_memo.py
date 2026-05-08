"""Unit tests for CrucibleOutput and MemoWriterOutput models.

Tests:
- CrucibleOutput model with valid data
- CrucibleOutput severity validator
- CrucibleOutput thesis_survives validator
- MemoWriterOutput model with valid data
- MemoWriterOutput verdict_line validation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pmacs.schemas.personas import CrucibleAttack, CrucibleOutput, MemoWriterOutput


# ---------------------------------------------------------------------------
# CrucibleOutput tests
# ---------------------------------------------------------------------------


class TestCrucibleOutputValid:
    """CrucibleOutput accepts valid data."""

    def test_minimal_valid(self):
        output = CrucibleOutput.model_validate({
            "ticker": "AAPL",
            "attacks": [{
                "attack_type": "LOGICAL_HOLE",
                "severity": 0.3,
                "description": "Small logical gap in thesis",
                "evidence_ids": ["ev1", "ev2"],
            }],
            "attack_count": 1,
            "severity": 0.3,
            "thesis_survives": True,
            "summary": "Thesis survives with minor logical gap.",
            "rewrite_cycle": 1,
        })
        assert output.ticker == "AAPL"
        assert output.attack_count == 1
        assert output.thesis_survives is True

    def test_full_valid(self):
        output = CrucibleOutput.model_validate({
            "ticker": "TSLA",
            "attacks": [
                {
                    "attack_type": "LOGICAL_HOLE",
                    "severity": 0.4,
                    "description": "Assumes market share growth without justification",
                    "evidence_ids": ["ev3"],
                },
                {
                    "attack_type": "CITATION_GAP",
                    "severity": 0.7,
                    "description": "No evidence for revenue projections",
                    "evidence_ids": [],
                    "missing_evidence": "Revenue forecast data needed",
                },
            ],
            "attack_count": 2,
            "severity": 0.7,
            "thesis_survives": False,
            "summary": "Thesis fails due to citation gap on revenue projections.",
            "rewrite_cycle": 2,
        })
        assert output.ticker == "TSLA"
        assert output.attack_count == 2
        assert output.thesis_survives is False
        assert output.attacks[1].missing_evidence == "Revenue forecast data needed"

    def test_all_attack_types(self):
        """All 5 attack types are valid."""
        types = ["LOGICAL_HOLE", "CITATION_GAP", "COUNTERARGUMENT",
                 "OVERLOOKED_RISK", "BASE_RATE_NEGLECT"]
        attacks = [
            {"attack_type": t, "severity": 0.3, "description": f"Attack {t}",
             "evidence_ids": [f"ev{i}"]}
            for i, t in enumerate(types)
        ]
        output = CrucibleOutput.model_validate({
            "ticker": "NVDA",
            "attacks": attacks,
            "attack_count": 5,
            "severity": 0.3,
            "thesis_survives": True,
            "summary": "Multiple minor attacks, thesis survives.",
            "rewrite_cycle": 1,
        })
        assert len(output.attacks) == 5


class TestCrucibleSeverityValidator:
    """Severity field must match max attack severity."""

    def test_severity_at_max(self):
        data = {
            "ticker": "AAPL",
            "attacks": [
                {"attack_type": "LOGICAL_HOLE", "severity": 0.2,
                 "description": "Minor", "evidence_ids": ["ev1"]},
                {"attack_type": "COUNTERARGUMENT", "severity": 0.5,
                 "description": "Moderate", "evidence_ids": ["ev2"]},
            ],
            "attack_count": 2,
            "severity": 0.5,
            "thesis_survives": True,
            "summary": "Summary",
            "rewrite_cycle": 1,
        }
        output = CrucibleOutput.model_validate(data)
        assert output.severity == 0.5

    def test_severity_within_tolerance(self):
        """severity within 0.05 of max is allowed."""
        data = {
            "ticker": "AAPL",
            "attacks": [
                {"attack_type": "LOGICAL_HOLE", "severity": 0.50,
                 "description": "Attack", "evidence_ids": ["ev1"]},
            ],
            "attack_count": 1,
            "severity": 0.52,  # within 0.05
            "thesis_survives": True,
            "summary": "Summary",
            "rewrite_cycle": 1,
        }
        output = CrucibleOutput.model_validate(data)
        assert abs(output.severity - 0.52) < 0.001

    def test_severity_outside_tolerance_rejected(self):
        data = {
            "ticker": "AAPL",
            "attacks": [
                {"attack_type": "LOGICAL_HOLE", "severity": 0.2,
                 "description": "Minor", "evidence_ids": ["ev1"]},
                {"attack_type": "COUNTERARGUMENT", "severity": 0.8,
                 "description": "Severe", "evidence_ids": ["ev2"]},
            ],
            "attack_count": 2,
            "severity": 0.3,  # too far from 0.8
            "thesis_survives": False,
            "summary": "Summary",
            "rewrite_cycle": 1,
        }
        with pytest.raises(ValidationError, match="severity"):
            CrucibleOutput.model_validate(data)


class TestCrucibleThesisSurvives:
    """thesis_survives must be consistent with severity vs 0.6."""

    def test_severity_06_survives_true(self):
        """severity == 0.6 should allow thesis_survives=True (not > 0.6)."""
        data = {
            "ticker": "AAPL",
            "attacks": [
                {"attack_type": "LOGICAL_HOLE", "severity": 0.6,
                 "description": "Borderline", "evidence_ids": ["ev1"]},
            ],
            "attack_count": 1,
            "severity": 0.6,
            "thesis_survives": True,
            "summary": "Borderline, survives.",
            "rewrite_cycle": 1,
        }
        output = CrucibleOutput.model_validate(data)
        assert output.thesis_survives is True

    def test_severity_06_survives_false(self):
        """severity == 0.6 should also allow thesis_survives=False (not < 0.6)."""
        data = {
            "ticker": "AAPL",
            "attacks": [
                {"attack_type": "LOGICAL_HOLE", "severity": 0.6,
                 "description": "Borderline", "evidence_ids": ["ev1"]},
            ],
            "attack_count": 1,
            "severity": 0.6,
            "thesis_survives": False,
            "summary": "Borderline, does not survive.",
            "rewrite_cycle": 1,
        }
        output = CrucibleOutput.model_validate(data)
        assert output.thesis_survives is False


# ---------------------------------------------------------------------------
# MemoWriterOutput tests
# ---------------------------------------------------------------------------


class TestMemoWriterOutputValid:
    """MemoWriterOutput accepts valid data."""

    def test_minimal_valid(self):
        output = MemoWriterOutput.model_validate({
            "ticker": "AAPL",
            "verdict_line": "BUY -- strong catalysts ahead.",
            "thesis_summary": "Apple is positioned for growth with new product launches.",
            "key_evidence": ["Earnings beat", "Strong guidance"],
            "key_risks": ["Regulatory risk"],
            "conviction": 0.7,
            "p_up": 0.6,
            "p_flat": 0.2,
            "p_down": 0.2,
            "dissenting_personas": [],
        })
        assert output.ticker == "AAPL"
        assert output.verdict_line.startswith("BUY")
        assert len(output.key_evidence) == 2

    def test_full_valid(self):
        output = MemoWriterOutput.model_validate({
            "ticker": "TSLA",
            "verdict_line": "STRONG_BUY -- multiple catalysts aligned.",
            "thesis_summary": "Tesla has strong momentum with deliveries exceeding expectations.",
            "key_evidence": [
                "Record Q3 deliveries",
                "Margin expansion",
                "New market entry",
            ],
            "key_risks": [
                "Valuation stretched",
                "Competition increasing",
            ],
            "conviction": 0.85,
            "p_up": 0.7,
            "p_flat": 0.2,
            "p_down": 0.1,
            "ev_multiple": 2.5,
            "sizing_usd": 800.0,
            "dissenting_personas": ["forensics"],
            "dissent_summary": "Forensics flagged minor revenue quality concerns.",
        })
        assert output.ticker == "TSLA"
        assert output.ev_multiple == 2.5
        assert output.sizing_usd == 800.0
        assert "forensics" in output.dissenting_personas

    def test_hold_verdict(self):
        output = MemoWriterOutput.model_validate({
            "ticker": "MSFT",
            "verdict_line": "HOLD -- no strong catalysts either way.",
            "thesis_summary": "Microsoft is fairly valued with limited near-term catalysts.",
            "key_evidence": ["Stable earnings"],
            "key_risks": [],
            "conviction": 0.3,
            "p_up": 0.3,
            "p_flat": 0.4,
            "p_down": 0.3,
            "dissenting_personas": [],
        })
        assert output.verdict_line.startswith("HOLD")

    def test_skip_verdict(self):
        output = MemoWriterOutput.model_validate({
            "ticker": "XYZ",
            "verdict_line": "SKIP -- too many red flags.",
            "thesis_summary": "Company has accounting concerns.",
            "key_evidence": ["Audit flags raised"],
            "key_risks": ["Severe accounting risk"],
            "conviction": 0.1,
            "p_up": 0.1,
            "p_flat": 0.2,
            "p_down": 0.7,
            "dissenting_personas": [],
        })
        assert output.verdict_line.startswith("SKIP")


class TestMemoWriterVerdictValidation:
    """verdict_line must start with STRONG_BUY / BUY / HOLD / SKIP."""

    def test_invalid_verdict_rejected(self):
        with pytest.raises(ValidationError):
            MemoWriterOutput.model_validate({
                "ticker": "AAPL",
                "verdict_line": "MAYBE -- could go either way.",
                "thesis_summary": "Uncertain outlook.",
                "key_evidence": ["Mixed signals"],
                "key_risks": [],
                "conviction": 0.3,
                "p_up": 0.3,
                "p_flat": 0.4,
                "p_down": 0.3,
                "dissenting_personas": [],
            })

    def test_strong_buy_verdict(self):
        output = MemoWriterOutput.model_validate({
            "ticker": "AAPL",
            "verdict_line": "STRONG_BUY -- exceptional setup.",
            "thesis_summary": "Strong thesis.",
            "key_evidence": ["Ev1"],
            "key_risks": [],
            "conviction": 0.9,
            "p_up": 0.8,
            "p_flat": 0.1,
            "p_down": 0.1,
            "dissenting_personas": [],
        })
        assert output.verdict_line.startswith("STRONG_BUY")

    def test_empty_key_evidence_rejected(self):
        """key_evidence must have at least 1 item."""
        with pytest.raises(ValidationError):
            MemoWriterOutput.model_validate({
                "ticker": "AAPL",
                "verdict_line": "BUY -- strong case.",
                "thesis_summary": "Good thesis.",
                "key_evidence": [],
                "key_risks": [],
                "conviction": 0.7,
                "p_up": 0.6,
                "p_flat": 0.2,
                "p_down": 0.2,
                "dissenting_personas": [],
            })
