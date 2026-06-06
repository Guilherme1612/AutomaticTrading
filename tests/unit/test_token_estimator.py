"""Unit tests for token_estimator — character heuristic accuracy."""

import pytest

from pmacs.billing.token_estimator import estimate_call_cost, estimate_tokens
from pmacs.schemas.billing import PERSONA_EXPECTED_OUTPUT_TOKENS, PricingRecord


# V4 Flash pricing per token
_INPUT_PER_TOKEN = 0.10 / 1_000_000
_OUTPUT_PER_TOKEN = 0.20 / 1_000_000

_PRICING = PricingRecord(
    model_id="deepseek/deepseek-v4-flash",
    input_price_per_token=_INPUT_PER_TOKEN,
    output_price_per_token=_OUTPUT_PER_TOKEN,
    fetched_at="2026-01-01T00:00:00Z",
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        # "Hello world" = 11 chars → ceil(11 × 0.26) = ceil(2.86) = 3
        assert estimate_tokens("Hello world") == 3

    def test_medium_text(self):
        # ~1000 chars → ceil(1000 × 0.26) = 260
        text = "a" * 1000
        assert estimate_tokens(text) == 260

    def test_approximately_accurate(self):
        """The 0.26 heuristic should be within ±15% for English text.

        English text averages ~4 chars/token, so 1000 chars ≈ 250 tokens.
        Our estimate: ceil(1000 × 0.26) = 260.
        260 / 250 = 1.04 → 4% over, well within ±15%.
        """
        text = "The quick brown fox jumps over the lazy dog. " * 20  # ~880 chars
        estimated = estimate_tokens(text)
        # Actual would be ~220 tokens (880/4). Check ±15%.
        approx_actual = len(text) / 3.85
        assert abs(estimated - approx_actual) / approx_actual < 0.15


class TestEstimateCallCost:
    def test_returns_estimated_cost(self):
        prompt = "Analyze this stock."
        result = estimate_call_cost(prompt, "growth_hunter", _PRICING)
        assert result.persona == "growth_hunter"
        assert result.model_id == "deepseek/deepseek-v4-flash"
        assert result.estimated_input_tokens == estimate_tokens(prompt)
        assert result.estimated_output_tokens == PERSONA_EXPECTED_OUTPUT_TOKENS["growth_hunter"]
        assert result.estimated_cost_usd > 0

    def test_persona_output_lookup(self):
        """Each persona uses its configured expected output tokens."""
        for persona, expected_tokens in PERSONA_EXPECTED_OUTPUT_TOKENS.items():
            result = estimate_call_cost("test", persona, _PRICING)
            assert result.estimated_output_tokens == expected_tokens, (
                f"{persona}: expected {expected_tokens}, got {result.estimated_output_tokens}"
            )

    def test_unknown_persona_uses_default(self):
        """Unknown persona defaults to 500 output tokens."""
        result = estimate_call_cost("test", "unknown_persona", _PRICING)
        assert result.estimated_output_tokens == 500

    def test_cost_is_positive(self):
        """Any non-empty prompt should produce a positive cost estimate."""
        result = estimate_call_cost("x", "moat_analyst", _PRICING)
        assert result.estimated_cost_usd > 0
