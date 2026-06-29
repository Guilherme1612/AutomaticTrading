"""Unit tests for cost_calculator — table-driven from PRD §4.1."""

import pytest

from pmacs.billing.cost_calculator import compute_body_cost, compute_cost
from pmacs.schemas.billing import PricingRecord


# V4 Flash pricing: $0.10/M input, $0.20/M output → per-token prices
_INPUT_PER_TOKEN = 0.10 / 1_000_000
_OUTPUT_PER_TOKEN = 0.20 / 1_000_000

# PRD §4.1 numerical examples
PRD_TABLE = [
    # (prompt_tokens, completion_tokens, expected_cost)
    (500, 200, 0.00009),
    (2_000, 500, 0.00030),
    (5_000, 800, 0.00066),
    (10_000, 1_500, 0.00130),
    (50_000, 2_000, 0.00540),
]


@pytest.mark.parametrize("prompt,completion,expected", PRD_TABLE)
def test_compute_cost_table(prompt, completion, expected):
    """PRD §4.1: cost equation produces correct values for known inputs."""
    result = compute_cost(prompt, completion, _INPUT_PER_TOKEN, _OUTPUT_PER_TOKEN)
    # Allow tiny float rounding (PRD values are rounded to 5 decimals)
    assert abs(result - expected) < 1e-6, f"{result} != {expected}"


def test_compute_cost_zero_tokens():
    """Zero tokens → zero cost."""
    assert compute_cost(0, 0, 1.0, 1.0) == 0.0


def test_compute_cost_only_input():
    """Only input tokens → only input cost."""
    cost = compute_cost(1_000_000, 0, _INPUT_PER_TOKEN, _OUTPUT_PER_TOKEN)
    assert abs(cost - 0.10) < 1e-6


def test_compute_cost_only_output():
    """Only output tokens → only output cost."""
    cost = compute_cost(0, 1_000_000, _INPUT_PER_TOKEN, _OUTPUT_PER_TOKEN)
    assert abs(cost - 0.20) < 1e-6


def test_compute_body_cost():
    """compute_body_cost extracts tokens from usage dict."""
    pricing = PricingRecord(
        model_id="test-model",
        input_price_per_token=_INPUT_PER_TOKEN,
        output_price_per_token=_OUTPUT_PER_TOKEN,
        fetched_at="2026-01-01T00:00:00Z",
    )
    usage = {"prompt_tokens": 2000, "completion_tokens": 500}
    result = compute_body_cost(usage, pricing)
    expected = compute_cost(2000, 500, _INPUT_PER_TOKEN, _OUTPUT_PER_TOKEN)
    assert abs(result - expected) < 1e-10


def test_compute_body_cost_missing_fields():
    """Missing usage fields default to 0."""
    pricing = PricingRecord(
        model_id="test",
        input_price_per_token=0.10,
        output_price_per_token=0.20,
        fetched_at="2026-01-01T00:00:00Z",
    )
    assert compute_body_cost({}, pricing) == 0.0
    assert compute_body_cost({"prompt_tokens": 100}, pricing) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Spec exit test #1 (spec/Phases.md Phase 16, line 677)
# "compute_cost(1000, 500, $1/M in, $2/M out) = $0.002 exact; round-trip;
#  empty token counts → 0"
# The PRD §4.1 numerical examples above cover a different price point; this
# section pins the spec's exact wording.
# ---------------------------------------------------------------------------

def test_compute_cost_spec_exact_example():
    """Spec/Phases.md Phase 16 exit test #1, exact verbatim case.

    compute_cost(1000 input, 500 output, $1/M input, $2/M output) = $0.002 exact.
    """
    in_per_token = 1.0 / 1_000_000   # $1 per 1M input tokens
    out_per_token = 2.0 / 1_000_000  # $2 per 1M output tokens
    cost = compute_cost(1000, 500, in_per_token, out_per_token)
    # 1000 * 1e-6 + 500 * 2e-6 = 0.001 + 0.001 = 0.002 exact
    assert cost == pytest.approx(0.002, abs=1e-12)


def test_compute_cost_round_trip_via_pricing_record():
    """Spec/Phases.md Phase 16 exit test #1 round-trip.

    Round-trip via PricingRecord + compute_body_cost: prices stored per-token
    must produce the same USD as the spec's per-million tables.
    """
    pricing = PricingRecord(
        model_id="gpt-4",
        input_price_per_token=5.0 / 1_000_000,    # $5/M
        output_price_per_token=15.0 / 1_000_000,  # $15/M
        fetched_at="2026-01-01T00:00:00Z",
    )
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    cost = compute_body_cost(usage, pricing)
    # 1000 * 5e-6 + 500 * 15e-6 = 0.005 + 0.0075 = 0.0125
    assert cost == pytest.approx(0.0125, abs=1e-9)
