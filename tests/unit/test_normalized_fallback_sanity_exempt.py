"""Regression: base sanity validator must accept synthetic `normalized-fallback-*`
IDs that `_pre_validate._ensure_min_evidence_ids` injects.

Bug history: ONDS 3-cycle audit Jun 30 — macro_regime aborted at SANITY_VALIDATION_FAIL
because LLM output was padded with `normalized-fallback-001` and the base validator
only accepted IDs found in evidence packets. Fix adds an exemption in base.py.
"""
from __future__ import annotations

from types import SimpleNamespace

from pmacs.agents.sanity.base import BaseSanityValidator, SanityResult


class _StubValidator(BaseSanityValidator):
    """Bare-minimum subclass — no persona-specific checks."""

    def _persona_checks(self, output, evidence):  # type: ignore[override]
        return SanityResult(passed=True)


def _evidence_packets(*ids: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(evidence=[SimpleNamespace(id=i) for i in ids])]


def test_normalized_fallback_id_accepted():
    """Synthetic ID `normalized-fallback-001` injected by _ensure_min_evidence_ids
    must pass the evidence_ids reference check."""
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["normalized-fallback-001"],
    }
    result = v.validate(out, _evidence_packets())
    assert result.passed, f"normalized-fallback-001 should be accepted, got: {result.reason}"


def test_real_evidence_id_still_accepted():
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["edgar.filing.10k.2024"],
    }
    result = v.validate(out, _evidence_packets("edgar.filing.10k.2024"))
    assert result.passed, f"real ID should still pass, got: {result.reason}"


def test_unknown_id_still_rejected():
    """The exemption is narrow — only `normalized-fallback-*` synthetic IDs.
    LLM-hallucinated citations still fail."""
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["hallucinated.id.42"],
    }
    result = v.validate(out, _evidence_packets("real.id.1"))
    assert not result.passed
    assert "hallucinated.id.42" in (result.reason or "")


def test_mixed_real_and_synthetic_accepted():
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["real.id.1", "normalized-fallback-001"],
    }
    result = v.validate(out, _evidence_packets("real.id.1"))
    assert result.passed, f"mixed list should pass, got: {result.reason}"


def test_empty_evidence_ids_accepted():
    """No evidence_ids = no check runs. Trivially passes."""
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": [],
    }
    result = v.validate(out, _evidence_packets())
    assert result.passed


def test_prefix_must_be_exact():
    """Defense against over-broad exemption — `normalized-anything` without
    `-fallback-` must still fail."""
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["normalized-hallucinated-001"],
    }
    result = v.validate(out, _evidence_packets())
    assert not result.passed
    assert "normalized-hallucinated-001" in (result.reason or "")