"""Unit tests for catalyst_summarizer drift fix — probability renormalization.

ONDS 3-cycle audit Jun 29: the LLM normalizes probabilities across
``catalysts[i]`` and emits (p_up, p_flat, p_down) summing to 1.05–1.10.
The schema's ``_check_prob_sum`` rejects with `abs(total - 1.0) > 0.10`
before the sanity layer can validate. Fix: renormalize in
``CatalystSummarizerRunner._pre_validate`` so we clamp instead of reject.

spec_ref: Agents.md §6 — catalyst_summarizer schema + sanity invariants
"""
from __future__ import annotations

from pmacs.agents.catalyst_summarizer import CatalystSummarizerRunner
from pmacs.schemas.personas import CatalystSummarizerOutput


def _minimal_valid(probs: tuple[float, float, float]) -> dict:
    """Build a minimal CatalystSummarizerOutput payload with the given probs."""
    p_up, p_flat, p_down = probs
    return {
        "ticker": "ONDS",
        "catalysts": [
            {
                "catalyst_type": "earnings",
                "description": "Q3 earnings release expected",
                "expected_date": "2026-08-15",
                "status": "PENDING",
                "thesis_impact": "POSITIVE",
                "evidence_ids": ["ev-1"],
            },
        ],
        "net_catalyst_outlook": "net outlook text",
        "p_up": p_up,
        "p_flat": p_flat,
        "p_down": p_down,
        "evidence_ids": ["ev-1"],
    }


class TestCatalystProbRenormalization:
    """catalyst_summarizer pre_validate renormalizes 1.0–1.10 sums."""

    def setup_method(self):
        self.runner = CatalystSummarizerRunner(cycle_id="test-cycle")

    def test_sum_1_05_is_renormalized(self):
        """LLM emits (0.40, 0.35, 0.30) summing to 1.05 — must clamp to 1.0."""
        payload = _minimal_valid((0.40, 0.35, 0.30))
        out = self.runner._pre_validate(payload)
        # Probs should now sum to exactly 1.0
        total = out["p_up"] + out["p_flat"] + out["p_down"]
        assert abs(total - 1.0) < 0.01, f"expected ~1.0, got {total}"
        # Pydantic must now accept the output (the original failure mode)
        m = CatalystSummarizerOutput.model_validate(out)
        assert abs(m.p_up + m.p_flat + m.p_down - 1.0) < 1e-9

    def test_sum_1_10_is_renormalized(self):
        """Boundary: 1.10 is the schema's reject threshold."""
        payload = _minimal_valid((0.40, 0.40, 0.30))  # = 1.10
        out = self.runner._pre_validate(payload)
        total = out["p_up"] + out["p_flat"] + out["p_down"]
        assert abs(total - 1.0) < 0.01

    def test_sum_at_1_0_not_renormalized(self):
        """Already-valid distribution must pass through untouched."""
        payload = _minimal_valid((0.40, 0.35, 0.25))
        out = self.runner._pre_validate(payload)
        assert out["p_up"] == 0.40
        assert out["p_flat"] == 0.35
        assert out["p_down"] == 0.25

    def test_sum_above_1_10_not_renormalized(self):
        """Catastrophic sums >1.10 must still be rejected by Pydantic (we
        don't paper over wildly broken outputs)."""
        from pydantic import ValidationError
        payload = _minimal_valid((0.50, 0.50, 0.50))  # = 1.50
        out = self.runner._pre_validate(payload)
        with __import__("pytest").raises(ValidationError):
            CatalystSummarizerOutput.model_validate(out)

    def test_sum_below_1_0_not_touched_here(self):
        """The schema's own validator normalizes <1.0 sums; pre_validate
        only acts on 1.0 < total <= 1.10. Below 1.0 is the schema's job."""
        payload = _minimal_valid((0.40, 0.30, 0.30))  # = 1.0 (schema-grid snaps)
        out = self.runner._pre_validate(payload)
        # pre_validate MUST NOT touch 1.0 sums
        assert out["p_up"] == 0.40
        assert out["p_flat"] == 0.30
        assert out["p_down"] == 0.30
        # Schema accepts (1.0 - 1.0 = 0 < 1e-9)
        m = CatalystSummarizerOutput.model_validate(out)
        assert abs(m.p_up + m.p_flat + m.p_down - 1.0) < 1e-9
