"""Scenario-weighted expected price engine — deterministic, LLM-free.

Spec ref: Architecture.md §9.4b, Source.md §16.9

Consumes the Arbitrated probability vector (p_up/p_flat/p_down) plus bull/base/bear
fair-value prices (typically from the ReverseDcfEngine sensitivity or the valuation
range) and produces a probability-weighted expected price:

    E[price] = p_up * bull_price + p_flat * base_price + p_down * bear_price

Feeds MemoWriter only. It does NOT replace compute_ev's ev_multiple (a
trade-expectancy ratio, not a valuation multiple) and does NOT amend conviction.
"""
from __future__ import annotations

from pmacs.logsys import log_debug
from pmacs.schemas.scenario_price import ScenarioPriceResult

_PROB_TOL = 1e-6


def compute_scenario_price(
    *,
    ticker: str,
    cycle_id: str,
    p_up: float,
    p_flat: float,
    p_down: float,
    bull_price: float | None,
    base_price: float | None,
    bear_price: float | None,
    current_price_usd: float | None = None,
) -> ScenarioPriceResult:
    """Compute a probability-weighted expected price across three scenarios.

    Probabilities are normalized to sum 1.0 if within tolerance. Any missing scenario
    price degrades the result to ``is_available == False`` with a note — never
    fabricates. ``current_price_usd`` enables an expected-return-vs-current figure.
    """
    notes_bits: list[str] = []

    total = p_up + p_flat + p_down
    if abs(total - 1.0) > 0.10:
        notes_bits.append(f"probabilities sum to {total:.4f}; rejecting")
        result = ScenarioPriceResult(
            ticker=ticker, cycle_id=cycle_id, p_up=p_up, p_flat=p_flat, p_down=p_down,
            current_price_usd=current_price_usd,
            notes="; ".join(notes_bits),
        )
        _log(ticker, cycle_id, result, reason="degraded")
        return result
    if abs(total - 1.0) > _PROB_TOL and total > 0:
        p_up, p_flat, p_down = p_up / total, p_flat / total, p_down / total

    missing = [
        name for name, val in (("bull", bull_price), ("base", base_price), ("bear", bear_price))
        if val is None or val <= 0
    ]
    if missing:
        notes_bits.append("missing/non-positive scenario prices: " + ", ".join(missing))
        result = ScenarioPriceResult(
            ticker=ticker, cycle_id=cycle_id,
            p_up=round(p_up, 6), p_flat=round(p_flat, 6), p_down=round(p_down, 6),
            bull_price=bull_price, base_price=base_price, bear_price=bear_price,
            current_price_usd=current_price_usd,
            notes="; ".join(notes_bits),
        )
        _log(ticker, cycle_id, result, reason="degraded")
        return result

    expected = p_up * float(bull_price) + p_flat * float(base_price) + p_down * float(bear_price)
    expected_return_pct: float | None = None
    if current_price_usd and current_price_usd > 0:
        expected_return_pct = round((expected / current_price_usd - 1.0) * 100.0, 2)

    result = ScenarioPriceResult(
        ticker=ticker, cycle_id=cycle_id,
        bull_price=round(float(bull_price), 2),
        base_price=round(float(base_price), 2),
        bear_price=round(float(bear_price), 2),
        expected_price_usd=round(expected, 2),
        p_up=round(p_up, 6), p_flat=round(p_flat, 6), p_down=round(p_down, 6),
        current_price_usd=current_price_usd,
        expected_return_pct=expected_return_pct,
        notes="; ".join(notes_bits) if notes_bits else "",
    )
    _log(ticker, cycle_id, result, reason="computed")
    return result


def _log(ticker: str, cycle_id: str, result: ScenarioPriceResult, reason: str) -> None:
    log_debug(
        "SCENARIO_PRICE_COMPUTED",
        payload={
            "ticker": ticker,
            "reason": reason,
            "expected_price_usd": result.expected_price_usd,
            "bull_price": result.bull_price,
            "base_price": result.base_price,
            "bear_price": result.bear_price,
            "expected_return_pct": result.expected_return_pct,
            "notes": result.notes,
        },
        level="INFO",
        cycle_id=cycle_id or None,
        msg=f"scenario price {ticker}: expected={result.expected_price_usd} ({reason})",
    )