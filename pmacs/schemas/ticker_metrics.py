"""Derived per-ticker valuation metrics.

Output of the deterministic Python metrics engine that powers the Ticker Data page
(Source.md §16.8). Every field here is computed by `pmacs.engines.ticker_metrics`
from the *stored* EvidencePacket — never re-fetched, never produced by an LLM
(Five Non-Negotiables: LLMs never math).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class YearMultiple(BaseModel):
    """Per-fiscal-year valuation multiples for the multi-year breakdown."""

    model_config = ConfigDict(frozen=True)

    period: str
    eps: float | None = None
    fcf_usd: float | None = None
    revenue_usd: float | None = None
    book_value_usd: float | None = None
    ebitda_usd: float | None = None
    total_debt_usd: float | None = None
    cash_usd: float | None = None
    price: float | None = None
    shares: float | None = None
    pe: float | None = None
    pfcf: float | None = None
    ps: float | None = None
    pb: float | None = None
    ev_usd: float | None = None
    ev_ebitda: float | None = None
    shares_approximated: bool = False
    """True when historical share count was unavailable and the current diluted
    share count was used to derive market cap for this year (Source.md §16.8)."""


class SaasKpis(BaseModel):
    """SaaS / subscription-business KPIs extracted from stored evidence text."""

    model_config = ConfigDict(frozen=True)

    nrr_pct: float | None = None
    grr_pct: float | None = None
    arr_usd: float | None = None
    rpo_usd: float | None = None
    rule_of_40: float | None = None
    arr_is_approximation: bool = False
    """True when ARR was derived from quarterly/TTM revenue because no explicit
    ARR disclosure was found in evidence."""
    nrr_from_agent: bool = False
    grr_from_agent: bool = False
    arr_from_agent: bool = False
    rpo_from_agent: bool = False
    notes: list[str] = Field(default_factory=list)


class AnalystConsensus(BaseModel):
    """Analyst price targets and recommendation mix (already fetched, now displayed)."""

    model_config = ConfigDict(frozen=True)

    target_mean: float | None = None
    target_median: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    num_analysts: int | None = None
    current_price: float | None = None
    upside_to_mean_pct: float | None = None
    strong_buy: int | None = None
    buy: int | None = None
    hold: int | None = None
    sell: int | None = None
    strong_sell: int | None = None
    total_analysts: int | None = None
    consensus: str | None = None


class CurrentMultiples(BaseModel):
    """Point-in-time valuation multiples from stored evidence (Source.md §16.8)."""

    model_config = ConfigDict(frozen=True)

    pe: float | None = None
    forward_pe: float | None = None
    ps: float | None = None
    pb: float | None = None
    ev_ebitda: float | None = None
    peg: float | None = None


class TickerDerivedMetrics(BaseModel):
    """Derived valuation metrics for the Ticker Data page (Source.md §16.8)."""

    model_config = ConfigDict(frozen=True)

    ticker: str

    # Current-period inputs (echoed for transparency)
    market_cap_usd: float | None = None
    latest_fcf_usd: float | None = None
    sbc_usd: float | None = None

    # FCF yield — the two side-by-side columns
    fcf_yield_pct: float | None = None
    """Unadjusted: latest annual FCF / market cap, as a percentage."""
    fcf_yield_sbc_adjusted_pct: float | None = None
    """SBC-adjusted: (latest annual FCF - SBC) / market cap, as a percentage."""

    # Multi-year multiples
    per_year: list[YearMultiple] = Field(default_factory=list)
    pe_3y_avg: float | None = None
    pfcf_3y_avg: float | None = None
    ps_3y_avg: float | None = None
    pb_3y_avg: float | None = None
    ev_ebitda_3y_avg: float | None = None

    # Current point-in-time multiples (passthrough + context)
    current: CurrentMultiples = Field(default_factory=CurrentMultiples)

    # Cash-flow / profitability context
    fcf_margin_ttm: float | None = None
    roic_ttm: float | None = None

    # SaaS KPIs
    saas_kpis: SaasKpis = Field(default_factory=SaasKpis)

    # Analyst consensus
    analyst: AnalystConsensus = Field(default_factory=AnalystConsensus)

    # Provenance / freshness
    most_recent_period: str | None = None
    has_stale_data: bool = False
    notes: list[str] = Field(default_factory=list)
    """Human-readable caveats, e.g. years skipped for non-positive EPS/FCF or
    share-count approximations."""
