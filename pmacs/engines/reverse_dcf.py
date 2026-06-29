"""Reverse-DCF valuation engine — deterministic, LLM-free.

Spec ref: Architecture.md §9.4b, Source.md §16.9, Agents.md §11b-§11d (valuation anchor)

Solves the perpetual growth rate the market is *implying* from the current market
cap and TTM free cash flow (Gordon growth), then compares it to the growth rate the
analysis layer estimates (GrowthHunter.revenue_yoy_pct or yfinance revenueGrowthTTMYoy).

    mc = fcf_ttm * (1 + g) / (r - g)
    =>  g = (mc * r - fcf_ttm) / (mc + fcf_ttm)      # implied growth, in (-1, r)

When fcf_ttm <= 0 (FCF-negative names — common in the current universe), a
perpetual-growth DCF is undefined; the engine returns a NEUTRAL result with a note
rather than fabricating a number. This is the "prefer N/A over wrong data" directive
(memory: feedback_yfinance_primary).

This engine does NOT enter Arbitration and does NOT amend the conviction formula
(Five Non-Negotiable #2). It feeds MemoWriter and the ScenarioPriceEngine.
"""
from __future__ import annotations

from pmacs.logsys import log_debug
from pmacs.schemas.reverse_dcf import ReverseDcfResult

# Defaults are code-versioned (Architecture.md §1.14). A discount rate of 10% and a
# 2% terminal/perpetual floor are conventional for a single-operator growth book.
DEFAULT_DISCOUNT_RATE: float = 0.10
DEFAULT_TERMINAL_GROWTH_PCT: float = 0.02
# Gap (assumed - implied) beyond which we call a clear lean. 3 percentage points.
DEFAULT_LEAN_THRESHOLD_PCT: float = 0.03


def _fair_value(fcf_ttm_usd: float, growth_pct: float, discount_rate: float) -> float | None:
    """Gordon fair value (market cap) at a given perpetual growth rate.

    Returns None when growth >= discount (formula undefined) or inputs are invalid.
    """
    if fcf_ttm_usd <= 0 or discount_rate <= 0 or growth_pct >= discount_rate:
        return None
    return fcf_ttm_usd * (1.0 + growth_pct) / (discount_rate - growth_pct)


def compute_reverse_dcf(
    *,
    ticker: str,
    cycle_id: str,
    market_cap_usd: float | None,
    fcf_ttm_usd: float | None,
    assumed_growth_pct: float | None,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    terminal_growth_pct: float = DEFAULT_TERMINAL_GROWTH_PCT,
    lean_threshold_pct: float = DEFAULT_LEAN_THRESHOLD_PCT,
) -> ReverseDcfResult:
    """Compute a reverse-DCF valuation anchor.

    All growth inputs are fractions (0.18 = 18%), not percentages-with-sign.
    Returns a ReverseDcfResult; fields are None when primitives are missing or the
    model is undefined. Never raises on bad data — degrades to NEUTRAL + notes.
    """
    notes_bits: list[str] = []

    # --- guard: missing / non-positive primitives ---
    if market_cap_usd is None or market_cap_usd <= 0:
        notes_bits.append("market cap unavailable")
    if fcf_ttm_usd is None or fcf_ttm_usd <= 0:
        notes_bits.append("FCF non-positive; reverse-DCF undefined")
    if assumed_growth_pct is None:
        notes_bits.append("assumed growth unavailable")

    if market_cap_usd is None or fcf_ttm_usd is None or fcf_ttm_usd <= 0 or market_cap_usd <= 0:
        result = ReverseDcfResult(
            ticker=ticker,
            cycle_id=cycle_id,
            assumed_growth_pct=assumed_growth_pct,
            valuation_lean="NEUTRAL",
            notes="; ".join(notes_bits),
        )
        _log(ticker, cycle_id, result, reason="degraded")
        return result

    mc = float(market_cap_usd)
    fcf = float(fcf_ttm_usd)
    r = float(discount_rate)

    # Implied perpetual growth (always in (-1, r) when fcf>0, mc>0, r>0).
    implied = (mc * r - fcf) / (mc + fcf)
    implied_pct = round(implied, 6)

    assumed = assumed_growth_pct if assumed_growth_pct is not None else terminal_growth_pct
    assumed_pct = float(assumed)

    growth_gap_pct = round(assumed_pct - implied_pct, 6)

    # Fair value at the assumed growth (None if assumed >= discount).
    fair_value = _fair_value(fcf, assumed_pct, r)
    fair_value_usd = round(fair_value, 2) if fair_value is not None else None
    if fair_value is None:
        notes_bits.append("assumed growth >= discount rate; fair value undefined")

    # Valuation lean from the growth gap.
    if growth_gap_pct > lean_threshold_pct:
        lean = "BULLISH"   # market under-pricing growth vs estimate
    elif growth_gap_pct < -lean_threshold_pct:
        lean = "BEARISH"   # market over-pricing growth vs estimate
    else:
        lean = "NEUTRAL"

    # Sensitivity: fair value across a growth grid around the estimate.
    sensitivity: dict[str, float] = {}
    for g in (implied_pct, assumed_pct - 0.05, assumed_pct, assumed_pct + 0.05):
        fv = _fair_value(fcf, g, r)
        if fv is not None:
            sensitivity[f"{g * 100:.1f}%"] = round(fv, 2)

    result = ReverseDcfResult(
        ticker=ticker,
        cycle_id=cycle_id,
        implied_growth_pct=implied_pct,
        assumed_growth_pct=assumed_pct,
        growth_gap_pct=growth_gap_pct,
        fair_value_usd=fair_value_usd,
        current_price_usd=None,  # caller may set from price cache if needed
        valuation_lean=lean,
        sensitivity=sensitivity,
        notes="; ".join(notes_bits) if notes_bits else "",
    )
    _log(ticker, cycle_id, result, reason="computed")
    return result


def _log(ticker: str, cycle_id: str, result: ReverseDcfResult, reason: str) -> None:
    log_debug(
        "REVERSE_DCF_COMPUTED",
        payload={
            "ticker": ticker,
            "reason": reason,
            "implied_growth_pct": result.implied_growth_pct,
            "assumed_growth_pct": result.assumed_growth_pct,
            "growth_gap_pct": result.growth_gap_pct,
            "fair_value_usd": result.fair_value_usd,
            "valuation_lean": result.valuation_lean,
            "notes": result.notes,
        },
        level="INFO",
        cycle_id=cycle_id or None,
        msg=f"reverse-DCF {ticker}: lean={result.valuation_lean} ({reason})",
    )