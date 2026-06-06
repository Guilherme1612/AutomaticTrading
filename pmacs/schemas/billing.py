"""Billing schemas — cost accounting data models.

PRD: docs/prd/Phase_TokenCost.md §4, §7, §9
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Expected output tokens per persona (PRD §6.1)
# ---------------------------------------------------------------------------

PERSONA_EXPECTED_OUTPUT_TOKENS: dict[str, int] = {
    "macro_regime": 600,
    "catalyst_summarizer": 700,
    "moat_analyst": 500,
    "growth_hunter": 500,
    "insider_activity": 400,
    "short_interest": 300,
    "forensics": 700,
    "crucible": 800,
    "memo_writer": 600,
}


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

class PricingRecord(BaseModel):
    """Cached pricing for a single model (PRD §9.1)."""
    model_config = ConfigDict(frozen=True)

    model_id: str
    input_price_per_token: float
    output_price_per_token: float
    cached_input_price_per_token: float | None = None
    per_request_fee: float = 0.0
    fetched_at: str  # ISO 8601
    source: str = "openrouter"


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

class EstimatedCost(BaseModel):
    """Pre-flight cost estimate before an LLM call fires (PRD §7.1)."""
    model_config = ConfigDict(frozen=True)

    call_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    persona: str
    model_id: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Actual cost records
# ---------------------------------------------------------------------------

class BodyCost(BaseModel):
    """Cost computed from response body token usage (PRD §7.2)."""
    model_config = ConfigDict(frozen=True)

    call_id: str
    cycle_id: str
    persona: str
    model_id: str
    generation_id: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    body_cost_usd: float = 0.0
    latency_ms: int = 0


class ActualCost(BaseModel):
    """Authoritative cost from OpenRouter reconciliation (PRD §11)."""
    model_config = ConfigDict(frozen=True)

    call_id: str
    actual_cost_usd: float
    reconciled_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    delta_from_body: float = 0.0


# ---------------------------------------------------------------------------
# Budget state
# ---------------------------------------------------------------------------

class BudgetState(BaseModel):
    """Current budget period state (PRD §9.2)."""
    model_config = ConfigDict(frozen=True)

    period: Literal["today", "this_month"]
    period_start: str
    total_cost_usd: float
    cap_usd: float
    updated_at: str


class BudgetCheckResult(BaseModel):
    """Result of a budget enforcement check (PRD §8)."""
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str = ""
    cap_type: str = ""  # "cycle_soft", "daily_hard", "monthly_hard", "runaway"
    current_total: float = 0.0
    estimated_new_total: float = 0.0
    cap_usd: float = 0.0
