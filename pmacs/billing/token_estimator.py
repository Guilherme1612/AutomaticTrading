"""Token estimator — character-based pre-flight cost estimation.

PRD §6: estimate tokens via ceil(char_count × 0.26), then compute cost.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from math import ceil

from pmacs.billing.cost_calculator import compute_cost
from pmacs.schemas.billing import (
    PERSONA_EXPECTED_OUTPUT_TOKENS,
    EstimatedCost,
    PricingRecord,
)

# Empirical ratio for DeepSeek V4 Flash: ~3.85 chars per token
_CHARS_PER_TOKEN_INV = 0.26


def estimate_tokens(text: str) -> int:
    """Estimate token count from character count.

    PRD §6.1: estimated_tokens = ceil(character_count × 0.26)
    """
    return ceil(len(text) * _CHARS_PER_TOKEN_INV)


def estimate_call_cost(
    prompt_text: str,
    persona: str,
    pricing: PricingRecord,
) -> EstimatedCost:
    """Estimate the cost of an LLM call before it fires.

    PRD §7.1 (pre-flight):
    1. Estimate input tokens from prompt text.
    2. Look up expected output tokens for the persona.
    3. Compute estimated cost using the core equation.

    Args:
        prompt_text: The full prompt that will be sent.
        persona: Persona name (must be a key in PERSONA_EXPECTED_OUTPUT_TOKENS).
        pricing: Current pricing for the model.

    Returns:
        EstimatedCost record.
    """
    estimated_input = estimate_tokens(prompt_text)
    estimated_output = PERSONA_EXPECTED_OUTPUT_TOKENS.get(persona, 500)

    estimated_cost_usd = compute_cost(
        estimated_input,
        estimated_output,
        pricing.input_price_per_token,
        pricing.output_price_per_token,
    )

    return EstimatedCost(
        call_id=uuid.uuid4().hex[:16],
        persona=persona,
        model_id=pricing.model_id,
        estimated_input_tokens=estimated_input,
        estimated_output_tokens=estimated_output,
        estimated_cost_usd=estimated_cost_usd,
    )
