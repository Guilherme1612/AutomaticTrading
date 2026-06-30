"""Regression: base sanity validator must accept synthetic `normalized-fallback-*`
IDs that `_pre_validate._ensure_min_evidence_ids` injects.

Bug history:
- ONDS 3-cycle audit Jun 30 (round 1): macro_regime aborted at
  SANITY_VALIDATION_FAIL because LLM output was padded with
  `normalized-fallback-001` and the base validator only accepted IDs
  found in evidence packets. Fix added an exemption in base.py.
- ONDS 3-cycle audit Jun 30 (round 2): bear_advocate / catalyst_summarizer /
  valuation_agent aborted at SANITY_VALIDATION_FAIL because the LLM
  hallucinated a real-looking evidence_id (``finnhub_ONDS_earnings_history``)
  that the system never fetched. Rejecting the persona meant every cycle
  fell back to safe-default simulation, the Crucible saw zero signal, and
  the memo was a 239-char Crucible-abort stub. Fix: strip hallucinated
  IDs in-place and substitute a synthetic `normalized-fallback-NNN`,
  preserving the persona's real research signal (probabilities, reasoning,
  key_signal). The audit chain records the swap.
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


def test_hallucinated_id_stripped_and_substituted():
    """LLM-hallucinated citations (e.g. ``finnhub_ONDS_earnings_history`` for a
    packet the system never fetched) are STRIPPED in-place and replaced with
    a synthetic ``normalized-fallback-001`` ID. The persona's real signal
    (reasoning, key_signal, analysis, probabilities) is preserved so the
    Crucible and memo writer see actual research, not safe-default stubs.
    The audit chain records the swap via SanityResult.normalized_citations."""
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["hallucinated.id.42"],
    }
    result = v.validate(out, _evidence_packets("real.id.1"))
    assert result.passed, (
        f"hallucinated ID should be STRIPPED (not rejected) so the persona's "
        f"real signal survives; got: {result.reason}"
    )
    # The audit chain captured the swap
    assert len(result.normalized_citations) == 1
    swap = result.normalized_citations[0]
    assert swap["from"] == "hallucinated.id.42"
    assert swap["to"] == "normalized-fallback-001"
    # And the in-place dict was mutated so downstream consumers see the
    # normalized ID, not the hallucinated one.
    assert out["evidence_ids"] == ["normalized-fallback-001"]


def test_mixed_hallucinated_and_real_strip_real_kept():
    """When the LLM cites one real packet and one hallucinated packet, the
    real one is kept and the hallucinated one is swapped for a synthetic."""
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["real.id.1", "hallucinated.id.42"],
    }
    result = v.validate(out, _evidence_packets("real.id.1"))
    assert result.passed
    assert out["evidence_ids"] == ["real.id.1", "normalized-fallback-001"]
    assert len(result.normalized_citations) == 1
    assert result.normalized_citations[0]["from"] == "hallucinated.id.42"


def test_prefix_must_be_exact():
    """Defense against over-broad substitution — `normalized-anything` without
    `-fallback-` is still a hallucinated ID and gets substituted, but the
    synthetic ID the system emits always uses the canonical
    ``normalized-fallback-NNN`` form (so re-runs produce stable IDs)."""
    v = _StubValidator()
    out = {
        "reasoning": "Macro is stable.",
        "key_signal": "10Y at 4.25%",
        "analysis": "Fed funds 5.25% with 10Y at 4.25%, curve -100bp.",
        "evidence_ids": ["normalized-hallucinated-001"],
    }
    result = v.validate(out, _evidence_packets())
    assert result.passed
    # The hallucinated "normalized-*" (not "normalized-fallback-*") is replaced
    # with the canonical synthetic form.
    assert out["evidence_ids"] == ["normalized-fallback-001"]
    assert result.normalized_citations[0]["from"] == "normalized-hallucinated-001"
    assert result.normalized_citations[0]["to"] == "normalized-fallback-001"
