"""Unit tests for CrucibleRunner._pre_validate() hook.

deepseek-v4-flash (via openrouter) emits ``attacks`` as a dict keyed by
attack axis letter (A/B/C/D — natural given the prompt's A. VALUATION /
B. MOAT / C. MGMT / D. THREATS structure). The canonical schema and
all downstream consumers expect ``list[CrucibleAttack]``.

The _pre_validate hook converts dict→list while preserving alphabetical
A→B→C→D order and reconciles ``attack_count`` if needed. This file
covers:

- dict input → list output (alphabetical order)
- dict input → attack_count reconciled
- list input → no change (passthrough identity)
- dict input → CrucibleOutput.model_validate() now succeeds end-to-end
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pmacs.agents.crucible import CrucibleRunner
from pmacs.schemas.personas import CrucibleOutput


def _attack(severity: float, attack_type: str = "LOGICAL_HOLE", desc: str = "x") -> dict:
    """Helper: build one valid attack dict (the same shape the LLM emits)."""
    return {
        "attack_type": attack_type,
        "severity": severity,
        "description": desc,
        "evidence_ids": ["ev-1"],
    }


def _base_dict_output(attacks: object, attack_count: int = 0) -> dict:
    """Helper: build a full CrucibleOutput-shaped dict with the given attacks."""
    return {
        "ticker": "MSFT",
        "attacks": attacks,
        "attack_count": attack_count,
        "severity": 0.50,
        "thesis_survives": True,
        "summary": "Crucible output",
        "rewrite_cycle": 1,
    }


class TestPreValidateDictToList:
    """Tests that dict-shaped attacks are converted to list."""

    def test_dict_attacks_converted_to_list(self):
        runner = CrucibleRunner()
        raw = {
            "A": _attack(0.40, "LOGICAL_HOLE", "attack A"),
            "B": _attack(0.55, "MOAT_DURABILITY", "attack B"),
            "C": _attack(0.30, "MANAGEMENT", "attack C"),
            "D": _attack(0.45, "COMPETITIVE_THREAT", "attack D"),
        }
        result = runner._pre_validate(_base_dict_output(raw, attack_count=99))
        assert isinstance(result["attacks"], list)
        assert len(result["attacks"]) == 4

    def test_dict_attacks_preserve_alphabetical_order(self):
        """Keys A→B→C→D must be preserved (not insertion order)."""
        runner = CrucibleRunner()
        # Insert in non-alphabetical order
        raw = {
            "D": _attack(0.45, "COMPETITIVE_THREAT", "D attack"),
            "B": _attack(0.55, "MOAT_DURABILITY", "B attack"),
            "A": _attack(0.40, "LOGICAL_HOLE", "A attack"),
            "C": _attack(0.30, "MANAGEMENT", "C attack"),
        }
        result = runner._pre_validate(_base_dict_output(raw, attack_count=4))
        descriptions = [a["description"] for a in result["attacks"]]
        assert descriptions == ["A attack", "B attack", "C attack", "D attack"]

    def test_dict_attacks_reconcile_attack_count(self):
        """attack_count must match the new list length, even if LLM emitted wrong count."""
        runner = CrucibleRunner()
        raw = {"A": _attack(0.4), "B": _attack(0.5)}
        # LLM emitted attack_count=99 — must be reconciled to 2
        result = runner._pre_validate(_base_dict_output(raw, attack_count=99))
        assert result["attack_count"] == 2

    def test_partial_dict_three_attacks(self):
        """LLM may emit only 3 of 4 axes. Conversion must still work."""
        runner = CrucibleRunner()
        raw = {"A": _attack(0.4), "B": _attack(0.5), "D": _attack(0.6)}
        result = runner._pre_validate(_base_dict_output(raw, attack_count=3))
        assert len(result["attacks"]) == 3
        assert result["attack_count"] == 3


class TestPreValidateListPassthrough:
    """Tests that list input is unchanged (identity behavior)."""

    def test_list_input_unchanged(self):
        runner = CrucibleRunner()
        attacks = [
            _attack(0.4, "LOGICAL_HOLE", "a1"),
            _attack(0.5, "COUNTERARGUMENT", "a2"),
        ]
        result = runner._pre_validate(_base_dict_output(attacks, attack_count=2))
        assert result["attacks"] is attacks
        assert result["attack_count"] == 2

    def test_empty_list_unchanged(self):
        runner = CrucibleRunner()
        result = runner._pre_validate(_base_dict_output([], attack_count=0))
        assert result["attacks"] == []
        assert result["attack_count"] == 0

    def test_missing_attacks_key_unchanged(self):
        """If 'attacks' key is absent, hook must not crash."""
        runner = CrucibleRunner()
        d = {
            "ticker": "MSFT",
            "attack_count": 0,
            "severity": 0.5,
            "thesis_survives": True,
            "summary": "x",
            "rewrite_cycle": 1,
        }
        result = runner._pre_validate(d)
        assert "attacks" not in result


class TestEndToEndValidation:
    """Tests that dict-form output now passes CrucibleOutput validation."""

    def test_dict_form_passes_pydantic_validation(self):
        """After _pre_validate, dict-form output must pass CrucibleOutput."""
        runner = CrucibleRunner()
        # Per spec: severity must equal max attack severity (within 0.05)
        # max=0.55 → set severity=0.55
        raw = {
            "A": _attack(0.40, "LOGICAL_HOLE", "VALUATION attack"),
            "B": _attack(0.55, "COUNTERARGUMENT", "MOAT attack"),
            "C": _attack(0.30, "CITATION_GAP", "MGMT attack"),
            "D": _attack(0.45, "OVERLOOKED_RISK", "THREAT attack"),
        }
        parsed = runner._pre_validate(_base_dict_output(raw, attack_count=99))
        parsed["severity"] = 0.55  # match max attack severity
        # Now the canonical Pydantic model must accept it
        output = CrucibleOutput.model_validate(parsed)
        assert output.ticker == "MSFT"
        assert len(output.attacks) == 4
        assert output.attack_count == 4
        # severity must equal max attack severity per the @model_validator
        assert abs(output.severity - 0.55) < 0.001

    def test_list_form_passes_pydantic_validation(self):
        """List-form output must still pass (passthrough regression test)."""
        runner = CrucibleRunner()
        attacks = [_attack(0.4), _attack(0.5)]
        parsed = runner._pre_validate(_base_dict_output(attacks, attack_count=2))
        output = CrucibleOutput.model_validate(parsed)
        assert len(output.attacks) == 2

    def test_pre_validate_failure_does_not_crash(self):
        """If attacks is some other type (e.g. int), hook must not raise."""
        runner = CrucibleRunner()
        # Pathological input — attacks is neither list nor dict
        parsed = _base_dict_output(42, attack_count=1)
        # Should not raise; downstream Pydantic will then catch the type mismatch
        result = runner._pre_validate(parsed)
        assert result["attacks"] == 42  # unchanged
