"""Tests for MemoWriterOutput schema-level invariants.

Validates the @model_validator(mode="after") that enforces:
  - verdict=PASS requires non-empty pass_reason (≤ 200 chars)
  - thesis_bullets: 1-5 entries, all four required fields
  - comparable_transactions: ≤ 5 entries, at least one multiple each
  - counter_thesis: claim + falsifier both required
  - verdict_line prefix set now includes PASS
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pmacs.schemas.personas import MemoWriterOutput


def _valid_minimum() -> dict:
    return {
        "ticker": "TEST",
        "verdict_line": "BUY — test",
        "thesis": "Test thesis with at least one number 42.",
        "key_evidence": ["Evidence A 1", "Evidence B 2"],
        "key_risks": ["Risk A"],
    }


class TestVerdictLine:
    def test_pass_prefix_accepted(self):
        # PASS is now a valid verdict_line prefix
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "verdict_line": "PASS — R:R 1.0 below threshold",
            "verdict": "PASS",
            "pass_reason": "R:R 1.0 below 1.5 threshold",
        })
        assert m.verdict_line.startswith("PASS")

    def test_unknown_prefix_rejected(self):
        with pytest.raises(ValidationError):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "verdict_line": "MAYBE — undecided",
            })


class TestVerdictPASS:
    def test_pass_without_pass_reason_rejected(self):
        with pytest.raises(ValidationError, match="pass_reason"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "verdict_line": "PASS — no edge",
                "verdict": "PASS",
            })

    def test_pass_with_empty_pass_reason_rejected(self):
        with pytest.raises(ValidationError, match="pass_reason"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "verdict_line": "PASS — no edge",
                "verdict": "PASS",
                "pass_reason": "   ",
            })

    def test_pass_with_pass_reason_accepted(self):
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "verdict_line": "PASS — R:R low",
            "verdict": "PASS",
            "pass_reason": "R:R 1.2 below 1.5 threshold",
        })
        assert m.pass_reason == "R:R 1.2 below 1.5 threshold"

    def test_pass_reason_over_200_chars_rejected(self):
        with pytest.raises(ValidationError, match="200"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "verdict": "PASS",
                "pass_reason": "x" * 201,
            })

    def test_buy_with_pass_reason_field_accepted(self):
        # pass_reason is optional for non-PASS verdicts
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "verdict": "BUY",
        })
        assert m.pass_reason is None


class TestThesisBullets:
    def test_empty_list_accepted(self):
        m = MemoWriterOutput.model_validate(_valid_minimum())
        assert m.thesis_bullets == []

    def test_single_valid_bullet_accepted(self):
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "thesis_bullets": [{
                "premise": "P1",
                "mechanism": "M1",
                "outcome": "O1",
                "number": "+37% upside",
            }],
        })
        assert len(m.thesis_bullets) == 1

    def test_missing_number_rejected(self):
        with pytest.raises(ValidationError, match="number"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "thesis_bullets": [{
                    "premise": "P1",
                    "mechanism": "M1",
                    "outcome": "O1",
                }],
            })

    def test_missing_premise_rejected(self):
        with pytest.raises(ValidationError, match="premise"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "thesis_bullets": [{
                    "mechanism": "M1",
                    "outcome": "O1",
                    "number": "N1",
                }],
            })

    def test_over_five_bullets_rejected(self):
        bullets = [{
            "premise": f"P{i}",
            "mechanism": f"M{i}",
            "outcome": f"O{i}",
            "number": f"N{i}",
        } for i in range(6)]
        with pytest.raises(ValidationError, match="5"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "thesis_bullets": bullets,
            })

    def test_five_bullets_accepted(self):
        bullets = [{
            "premise": f"P{i}",
            "mechanism": f"M{i}",
            "outcome": f"O{i}",
            "number": f"N{i}",
        } for i in range(5)]
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "thesis_bullets": bullets,
        })
        assert len(m.thesis_bullets) == 5


class TestComparableTransactions:
    def test_empty_list_accepted(self):
        m = MemoWriterOutput.model_validate(_valid_minimum())
        assert m.comparable_transactions == []

    def test_valid_row_with_ev_revenue_accepted(self):
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "comparable_transactions": [{
                "date": "2025-Q2",
                "target": "CompA",
                "acquirer": "StratA",
                "ev_revenue_multiple": 12.4,
                "ev_ebitda_multiple": 28.1,
                "vertical": "data infra",
            }],
        })
        assert len(m.comparable_transactions) == 1

    def test_valid_row_with_only_ev_ebitda_accepted(self):
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "comparable_transactions": [{
                "date": "2025-Q1",
                "target": "CompB",
                "acquirer": "StratB",
                "ev_ebitda_multiple": 22.3,
            }],
        })
        assert len(m.comparable_transactions) == 1

    def test_no_multiples_rejected(self):
        with pytest.raises(ValidationError, match="ev_revenue_multiple"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "comparable_transactions": [{
                    "date": "2025-Q1",
                    "target": "CompC",
                    "acquirer": "StratC",
                }],
            })

    def test_over_five_rows_rejected(self):
        rows = [{
            "date": "2025-Q1",
            "target": f"Comp{i}",
            "acquirer": f"Strat{i}",
            "ev_revenue_multiple": 10.0 + i,
        } for i in range(6)]
        with pytest.raises(ValidationError, match="5"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "comparable_transactions": rows,
            })

    def test_multiple_zero_rejected(self):
        with pytest.raises(ValidationError, match="ev_revenue_multiple"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "comparable_transactions": [{
                    "target": "X",
                    "acquirer": "Y",
                    "ev_revenue_multiple": 0.0,
                }],
            })

    def test_multiple_over_100_rejected(self):
        with pytest.raises(ValidationError, match="ev_revenue_multiple"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "comparable_transactions": [{
                    "target": "X",
                    "acquirer": "Y",
                    "ev_revenue_multiple": 200.0,
                }],
            })


class TestCounterThesis:
    def test_empty_list_accepted(self):
        m = MemoWriterOutput.model_validate(_valid_minimum())
        assert m.counter_thesis == []

    def test_valid_claim_with_falsifier_accepted(self):
        m = MemoWriterOutput.model_validate({
            **_valid_minimum(),
            "counter_thesis": [{
                "claim": "Margin compresses",
                "falsifier": "Margin < 38% any quarter",
            }],
        })
        assert len(m.counter_thesis) == 1

    def test_missing_falsifier_rejected(self):
        with pytest.raises(ValidationError, match="falsifier"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "counter_thesis": [{
                    "claim": "Margin compresses",
                }],
            })

    def test_missing_claim_rejected(self):
        with pytest.raises(ValidationError, match="claim"):
            MemoWriterOutput.model_validate({
                **_valid_minimum(),
                "counter_thesis": [{
                    "falsifier": "Something",
                }],
            })


class TestBackwardsCompat:
    """Old memos (pre-schema-bump) must still load without error."""

    def test_minimum_valid_dict(self):
        m = MemoWriterOutput.model_validate(_valid_minimum())
        assert m.ticker == "TEST"
        assert m.verdict == ""
        assert m.pass_reason is None
        assert m.thesis_bullets == []
        assert m.comparable_transactions == []
        assert m.counter_thesis == []
        assert m.short_interest_row is None
        assert m.insider_strip == {}

    def test_empty_verdict_line_accepted(self):
        # Empty verdict_line is allowed (sanity validator catches it later)
        d = _valid_minimum()
        d["verdict_line"] = ""
        m = MemoWriterOutput.model_validate(d)
        assert m.verdict_line == ""

    def test_legacy_dict_without_new_fields_loads(self):
        # Mimic an old memo row from SQLite
        legacy = {
            "ticker": "NBIS",
            "verdict_line": "BUY — hyperscaler",
            "thesis": "Old memo 42.",
            "key_evidence": ["A 1", "B 2"],
            "key_risks": ["R1"],
            "fair_value": 84.20,
            "valuation_range": {"low": 60, "base": 84, "high": 110},
            "bull_bear_debate": {},
            "what_would_change_my_mind": ["T1"],
        }
        m = MemoWriterOutput.model_validate(legacy)
        assert m.fair_value == 84.20
        assert m.verdict == ""  # New field defaults empty
