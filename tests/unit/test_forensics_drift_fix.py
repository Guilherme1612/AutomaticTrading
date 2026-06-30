"""Unit tests for forensics ``_pre_validate`` drift fix (Jun 29, ONDS 3-cycle audit).

Regression suite for the contradiction: deepseek-v4-flash on openrouter emits
both ``overall_accounting_quality="CLEAN"`` AND a non-empty ``red_flags``
list. The forensics sanity validator rejects this combination, aborting the
persona at Layer 3. The fix lives in ``ForensicsRunner._pre_validate`` and
bumps quality to ``MINOR_CONCERNS`` when the contradiction is detected.

Test matrix:
- contradiction is resolved (quality bumped, red_flags preserved)
- clean CLEAN + empty red_flags passes through unchanged
- non-CLEAN quality + red_flags passes through unchanged
- audit-logged via ``_log_normalization`` with ticker context

spec_ref: Agents.md §11 — forensics schema + sanity invariants
"""
from __future__ import annotations

from types import SimpleNamespace

from pmacs.agents.forensics import ForensicsRunner
from pmacs.schemas.personas import ForensicsOutput


def _red_flag(severity: float, category: str = "REVENUE_QUALITY") -> dict:
    return {
        "category": category,
        "description": f"test flag with severity {severity}",
        "severity": severity,
        "evidence_ids": ["ev-1"],
    }


def _evidence(known_ids: list[str]) -> list:
    return [
        SimpleNamespace(
            ticker="X",
            evidence=[SimpleNamespace(id=eid) for eid in known_ids],
        )
    ]


CLEAN_BUT_HAS_RED_FLAGS = {
    "ticker": "ONDS",
    "red_flags": [_red_flag(0.4), _red_flag(0.6)],
    "red_flag_count": 2,
    "overall_accounting_quality": "CLEAN",
    "p_up": 0.30,
    "p_flat": 0.40,
    "p_down": 0.30,
    "evidence_ids": ["ev-1"],
}


TRULY_CLEAN = {
    "ticker": "ONDS",
    "red_flags": [],
    "red_flag_count": 0,
    "overall_accounting_quality": "CLEAN",
    "p_up": 0.40,
    "p_flat": 0.35,
    "p_down": 0.25,
    "evidence_ids": ["ev-1"],
}


NON_CLEAN_WITH_FLAGS = {
    "ticker": "ONDS",
    "red_flags": [_red_flag(0.7)],
    "red_flag_count": 1,
    "overall_accounting_quality": "MATERIAL_CONCERNS",
    "p_up": 0.20,
    "p_flat": 0.30,
    "p_down": 0.50,
    "evidence_ids": ["ev-1"],
}


class TestForensicsContradictionFix:
    """Forensics ``CLEAN + N red_flags`` contradiction (Jun 29 drift fix)."""

    def setup_method(self):
        self.runner = ForensicsRunner(cycle_id="test-cycle")

    def test_clean_with_red_flags_is_bumped_to_minor_concerns(self):
        """The drift case: LLM emits CLEAN + N flags. Must be coerced to
        MINOR_CONCERNS so Pydantic + sanity both accept the output."""
        out = self.runner._pre_validate(dict(CLEAN_BUT_HAS_RED_FLAGS))
        assert out["overall_accounting_quality"] == "MINOR_CONCERNS"
        # red_flags must be preserved verbatim — don't drop LLM signal
        assert len(out["red_flags"]) == 2
        assert out["red_flag_count"] == 2

    def test_clean_with_red_flags_now_validates_against_pydantic(self):
        """After pre_validate, the output must round-trip through the schema
        AND the sanity validator (the original failure mode)."""
        out = self.runner._pre_validate(dict(CLEAN_BUT_HAS_RED_FLAGS))
        # Pydantic layer
        model = ForensicsOutput.model_validate(out)
        assert model.overall_accounting_quality == "MINOR_CONCERNS"
        # Sanity layer — SEVERE_RISK is no longer fired (no high severity);
        # MATERIAL_CONCERNS implies p_up <= p_flat+p_down (40 <= 70). Pass.
        from pmacs.agents.sanity.forensics import ForensicsSanity
        res = ForensicsSanity().validate(model.model_dump(), _evidence(["ev-1"]))
        assert res.passed, res.reason

    def test_truly_clean_output_is_not_modified(self):
        """No false-positive coercion: a clean CLEAN + no flags must pass
        through untouched."""
        out = self.runner._pre_validate(dict(TRULY_CLEAN))
        assert out["overall_accounting_quality"] == "CLEAN"
        assert out["red_flags"] == []
        assert out["red_flag_count"] == 0

    def test_non_clean_with_flags_is_not_modified(self):
        """Already-consistent output must not be re-bumped."""
        out = self.runner._pre_validate(dict(NON_CLEAN_WITH_FLAGS))
        assert out["overall_accounting_quality"] == "MATERIAL_CONCERNS"
        assert len(out["red_flags"]) == 1

    def test_single_red_flag_with_clean_is_bumped(self):
        """Boundary: even one red flag must contradict CLEAN."""
        payload = dict(CLEAN_BUT_HAS_RED_FLAGS)
        payload["red_flags"] = [_red_flag(0.3)]
        payload["red_flag_count"] = 1
        out = self.runner._pre_validate(payload)
        assert out["overall_accounting_quality"] == "MINOR_CONCERNS"

    def test_minor_concerns_with_red_flags_not_bumped_again(self):
        """Quality is bumped only once — the second-lowest tier is already
        consistent with having red flags."""
        payload = dict(CLEAN_BUT_HAS_RED_FLAGS)
        payload["overall_accounting_quality"] = "MINOR_CONCERNS"
        out = self.runner._pre_validate(payload)
        assert out["overall_accounting_quality"] == "MINOR_CONCERNS"


class TestForensicsDriftFixEvidenceIds:
    """The pre-existing evidence_ids padding must still work after the new rule."""

    def setup_method(self):
        self.runner = ForensicsRunner(cycle_id="test-cycle")

    def test_empty_evidence_ids_still_padded(self):
        payload = dict(CLEAN_BUT_HAS_RED_FLAGS)
        payload["evidence_ids"] = []
        out = self.runner._pre_validate(payload)
        # _ensure_min_evidence_ids still pads with normalized-fallback-*
        assert len(out["evidence_ids"]) >= 1
        assert all(eid.startswith("normalized-fallback-") for eid in out["evidence_ids"])
        # And the contradiction fix still applies
        assert out["overall_accounting_quality"] == "MINOR_CONCERNS"