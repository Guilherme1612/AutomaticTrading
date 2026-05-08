"""Unit tests for Crucible persona budget validators.

Tests:
- severity > 0.6 -> thesis_survives = False
- severity < 0.6 -> thesis_survives = True
- attack_count validator
- severity = max of attacks
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pmacs.schemas.personas import CrucibleAttack, CrucibleOutput


def _make_attack(severity: float = 0.3, attack_type: str = "LOGICAL_HOLE") -> dict:
    return {
        "attack_type": attack_type,
        "severity": severity,
        "description": "Test attack description",
        "evidence_ids": ["ev1"],
    }


def _make_output(
    attacks: list[dict] | None = None,
    severity: float = 0.3,
    thesis_survives: bool = True,
    rewrite_cycle: int = 1,
) -> dict:
    if attacks is None:
        attacks = [_make_attack(severity=severity)]
    return {
        "ticker": "AAPL",
        "attacks": attacks,
        "attack_count": len(attacks),
        "severity": severity,
        "thesis_survives": thesis_survives,
        "summary": "Test summary for the crucible output.",
        "rewrite_cycle": rewrite_cycle,
    }


class TestCrucibleSeveritySurvives:
    """thesis_survives must be consistent with severity vs 0.6 threshold."""

    def test_high_severity_survives_false(self):
        """severity > 0.6 requires thesis_survives=False."""
        output = CrucibleOutput.model_validate(
            _make_output(severity=0.8, thesis_survives=False, attacks=[_make_attack(0.8)])
        )
        assert output.thesis_survives is False
        assert output.severity == 0.8

    def test_low_severity_survives_true(self):
        """severity < 0.6 requires thesis_survives=True."""
        output = CrucibleOutput.model_validate(
            _make_output(severity=0.3, thesis_survives=True, attacks=[_make_attack(0.3)])
        )
        assert output.thesis_survives is True
        assert output.severity == 0.3

    def test_high_severity_survives_true_rejected(self):
        """severity > 0.6 with thesis_survives=True is invalid."""
        with pytest.raises(ValidationError, match="thesis_survives=True but severity > 0.6"):
            CrucibleOutput.model_validate(
                _make_output(severity=0.8, thesis_survives=True, attacks=[_make_attack(0.8)])
            )

    def test_low_severity_survives_false_rejected(self):
        """severity < 0.6 with thesis_survives=False is invalid."""
        with pytest.raises(ValidationError, match="thesis_survives=False but severity < 0.6"):
            CrucibleOutput.model_validate(
                _make_output(severity=0.3, thesis_survives=False, attacks=[_make_attack(0.3)])
            )


class TestCrucibleAttackCount:
    """attack_count must match len(attacks)."""

    def test_correct_count(self):
        output = CrucibleOutput.model_validate(
            _make_output(
                attacks=[_make_attack(0.3), _make_attack(0.5, "CITATION_GAP")],
                severity=0.5,
                thesis_survives=True,
            )
        )
        assert output.attack_count == 2

    def test_wrong_count_rejected(self):
        with pytest.raises(ValidationError, match="attack_count must match attacks length"):
            CrucibleOutput.model_validate(
                _make_output(
                    attacks=[_make_attack(0.3)],
                    severity=0.3,
                    thesis_survives=True,
                    attack_count_override=2,
                )
            )

    def test_empty_attacks_zero_severity(self):
        """Empty attacks list with severity=0.0 and thesis_survives=True."""
        data = _make_output(attacks=[], severity=0.0, thesis_survives=True)
        # Override attack_count to 0
        data["attack_count"] = 0
        output = CrucibleOutput.model_validate(data)
        assert output.attack_count == 0
        assert output.severity == 0.0


class TestCrucibleSeverityMax:
    """severity field must match max attack severity (within 0.05)."""

    def test_severity_matches_max(self):
        attacks = [
            _make_attack(severity=0.2),
            _make_attack(severity=0.5, attack_type="COUNTERARGUMENT"),
        ]
        output = CrucibleOutput.model_validate(
            _make_output(attacks=attacks, severity=0.5, thesis_survives=True)
        )
        assert output.severity == 0.5

    def test_severity_mismatch_rejected(self):
        attacks = [
            _make_attack(severity=0.2),
            _make_attack(severity=0.8, attack_type="COUNTERARGUMENT"),
        ]
        with pytest.raises(ValidationError, match="severity"):
            CrucibleOutput.model_validate(
                _make_output(attacks=attacks, severity=0.3, thesis_survives=True)
            )

    def test_rewrite_cycle_bounds(self):
        """rewrite_cycle must be 1 or 2."""
        data = _make_output(severity=0.3, thesis_survives=True, rewrite_cycle=1)
        output = CrucibleOutput.model_validate(data)
        assert output.rewrite_cycle == 1

        data["rewrite_cycle"] = 2
        output = CrucibleOutput.model_validate(data)
        assert output.rewrite_cycle == 2

        data["rewrite_cycle"] = 3
        with pytest.raises(ValidationError):
            CrucibleOutput.model_validate(data)


# Helper for the wrong_count test — override attack_count
def _make_output(
    attacks: list[dict] | None = None,
    severity: float = 0.3,
    thesis_survives: bool = True,
    rewrite_cycle: int = 1,
    attack_count_override: int | None = None,
) -> dict:
    if attacks is None:
        attacks = [_make_attack(severity=severity)]
    return {
        "ticker": "AAPL",
        "attacks": attacks,
        "attack_count": attack_count_override if attack_count_override is not None else len(attacks),
        "severity": severity,
        "thesis_survives": thesis_survives,
        "summary": "Test summary for the crucible output.",
        "rewrite_cycle": rewrite_cycle,
    }
