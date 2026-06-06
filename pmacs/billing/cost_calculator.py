"""Cost calculator — the core cost equation.

PRD §4: cost_usd = (prompt_tokens × input_price) + (completion_tokens × output_price)
"""

from __future__ import annotations

from pmacs.schemas.billing import PricingRecord


def compute_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_token: float,
    output_price_per_token: float,
) -> float:
    """Compute cost in USD from token counts and per-token prices.

    PRD §4:
        cost = (prompt_tokens / 1_000_000) × input_price_per_million
             + (completion_tokens / 1_000_000) × output_price_per_million

    Since pricing stores per-token prices (per_million / 1_000_000):
        cost = prompt_tokens × input_price_per_token
             + completion_tokens × output_price_per_token
    """
    return (prompt_tokens * input_price_per_token) + (completion_tokens * output_price_per_token)


def compute_body_cost(usage: dict, pricing: PricingRecord) -> float:
    """Compute cost from a response usage dict and cached pricing.

    Args:
        usage: dict with 'prompt_tokens' and 'completion_tokens'.
        pricing: cached pricing record for the model.

    Returns:
        Cost in USD.
    """
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    return compute_cost(
        prompt_tokens,
        completion_tokens,
        pricing.input_price_per_token,
        pricing.output_price_per_token,
    )
