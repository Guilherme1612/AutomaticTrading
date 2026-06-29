"""Forward-valuation engine — deterministic, LLM-free.

Spec ref: Architecture.md §9.4b, Source.md §16.9

Consumes the ValuationAgent's structured bull/base/bear scenario assumptions
(revenue growth path to a 6-12 month horizon, EBITDA margin at horizon, exit
EV/EBITDA multiple, acquisition revenue contribution) and computes a
per-scenario forward fair-value price:

    years            = horizon_months / 12
    organic_revenue  = current_revenue_ttm * (1 + revenue_growth_path_pct) ** years
    acq_revenue      = acquisition_revenue_contribution_pct * current_revenue_ttm
    forward_revenue  = organic_revenue + acq_revenue
    forward_ebitda   = forward_revenue * ebitda_margin_at_horizon_pct
    forward_ev       = forward_ebitda * exit_multiple
    equity_value     = forward_ev - net_debt          # net_debt already net of cash
    price_per_share  = equity_value / shares_outstanding

Bull/base/bear prices feed ScenarioPriceEngine (preferred when ``is_available``,
falling back to the reverse-DCF sensitivity grid). The engine does NOT enter
Arbitration and does NOT amend the conviction formula (Five Non-Negotiable #2).
When primitives are missing, returns ``is_available=False`` + notes — never
fabricates (prefer N/A over wrong data).
"""
from __future__ import annotations

from typing import Any

from pmacs.logsys import log_debug
from pmacs.schemas.forward_valuation import ForwardScenarioPoint, ForwardValuationResult

# Defaults are code-versioned (Architecture.md §1.14). A 12-month forward
# horizon is the upper bound of the operator's 6-12 month scenario window.
DEFAULT_HORIZON_MONTHS: int = 12
MIN_HORIZON_MONTHS: int = 6
MAX_HORIZON_MONTHS: int = 12


def _coerce_frac(v: Any) -> float | None:
    """Coerce an assumption value to a fraction (float) or None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _scenario_price(
    *,
    scenario: str,
    assumptions: dict[str, Any],
    current_revenue_ttm: float,
    shares_outstanding: float,
    net_debt_usd: float,
    years: float,
) -> ForwardScenarioPoint:
    """Compute one scenario's forward fair-value chain. Degrades per-field."""
    notes_bits: list[str] = []

    g = _coerce_frac(assumptions.get("revenue_growth_path_pct"))
    margin = _coerce_frac(assumptions.get("ebitda_margin_at_horizon_pct"))
    exit_mult = _coerce_frac(assumptions.get("exit_multiple"))
    exit_sales = _coerce_frac(assumptions.get("exit_sales_multiple"))
    acq_pct = _coerce_frac(assumptions.get("acquisition_revenue_contribution_pct")) or 0.0

    forward_revenue: float | None = None
    forward_ebitda: float | None = None
    forward_ev: float | None = None
    equity_value: float | None = None
    price_usd: float | None = None
    valuation_path: str | None = None

    if g is None:
        notes_bits.append("revenue_growth_path_pct unavailable")
    else:
        try:
            organic = current_revenue_ttm * (1.0 + g) ** years
        except (OverflowError, ValueError):
            organic = None
            notes_bits.append("revenue growth path overflow")
        if organic is not None:
            forward_revenue = round(organic + acq_pct * current_revenue_ttm, 2)
            if acq_pct > 0.0:
                notes_bits.append(
                    f"acquisition contribution {acq_pct*100:.1f}% of revenue (low-confidence, LLM-inferred)"
                )

    # --- Valuation path selection ---
    # EV/EBITDA is the primary path, but it is meaningless when EBITDA <= 0
    # (a positive multiple applied to negative EBITDA yields a nonsensical
    # negative EV). For pre-profit names (hypergrowth AI-infra, pre-revenue
    # biotech), fall back to EV/Sales: forward_ev = forward_revenue * exit_sales.
    # This is the standard sell-side approach for pre-profit names and lets the
    # engine value the exact universe (NBIS/ONDS) that the EV/EBITDA-only path
    # silently dropped. When EBITDA <= 0 and no exit_sales_multiple is provided,
    # the scenario degrades to no price (prefer N/A over a wrong EV/EBITDA).
    #
    # Path priority:
    #   1. EV/EBITDA  when margin > 0 AND exit_multiple provided
    #   2. EV/Sales   when exit_sales_multiple provided (pre-profit, or profitable
    #                 but the LLM only gave EV/Sales)
    #   3. degrade    (no price + a note)
    can_ev_ebitda = margin is not None and margin > 0.0 and exit_mult is not None and exit_mult > 0.0
    can_ev_sales = forward_revenue is not None and exit_sales is not None and exit_sales > 0.0

    if can_ev_ebitda:
        forward_ebitda = round(forward_revenue * margin, 2)  # type: ignore[operator]
        forward_ev = round(forward_ebitda * exit_mult, 2)
        valuation_path = "ev_ebitda"
        # V-009: when both exit multiples were provided, audit which one was
        # selected (EV/EBITDA wins by the can_ev_ebitda branch order) so the
        # memo can surface the agent's exit_sales_multiple even when ignored.
        if exit_sales is not None and exit_sales > 0.0:
            notes_bits.append(
                f"both exit_multiple ({exit_mult}) and exit_sales_multiple "
                f"({exit_sales}) provided — EV/EBITDA path selected"
            )
    elif can_ev_sales:
        if margin is not None and margin <= 0.0:
            notes_bits.append(
                f"ebitda_margin {margin*100:.1f}% <= 0 → EV/Sales path (pre-profit)"
            )
        else:
            notes_bits.append("EV/Sales path (exit_multiple not provided)")
        forward_ev = round(forward_revenue * exit_sales, 2)  # type: ignore[operator]
        valuation_path = "ev_sales"
    else:
        # Degrade with a precise reason.
        if margin is None:
            notes_bits.append("ebitda_margin_at_horizon_pct unavailable")
        elif margin <= 0.0:
            notes_bits.append(
                f"ebitda_margin {margin*100:.1f}% <= 0 and no exit_sales_multiple"
            )
        elif exit_mult is None:
            notes_bits.append("exit_multiple unavailable and no exit_sales_multiple fallback")
        else:
            notes_bits.append("no usable exit multiple (ev_ebitda/ev_sales both unavailable)")

    if forward_ev is not None:
        raw_equity = round(forward_ev - net_debt_usd, 2)
        # Limited liability: a shareholder's downside is floored at zero — you
        # cannot lose more than you invested. When forward EV < net debt the
        # equity is "underwater"; we floor at $0 and flag it (never fabricate a
        # negative price, never silently hide the distress signal).
        if raw_equity < 0.0:
            notes_bits.append("equity underwater (EV < net debt), floored at $0")
            equity_value = 0.0
        else:
            equity_value = raw_equity
        if shares_outstanding > 0:
            price_usd = round(equity_value / shares_outstanding, 2)
        else:
            notes_bits.append("shares_outstanding <= 0")

    return ForwardScenarioPoint(
        scenario=scenario,  # type: ignore[arg-type]
        revenue_growth_path_pct=g,
        ebitda_margin_at_horizon_pct=margin,
        exit_multiple=exit_mult,
        exit_sales_multiple=exit_sales,
        valuation_path=valuation_path,
        acquisition_revenue_contribution_pct=acq_pct,
        forward_revenue_usd=forward_revenue,
        forward_ebitda_usd=forward_ebitda,
        forward_ev_usd=forward_ev,
        equity_value_usd=equity_value,
        price_usd=price_usd,
        notes="; ".join(notes_bits),
    )


def compute_forward_valuation(
    *,
    ticker: str,
    cycle_id: str,
    horizon_months: int = DEFAULT_HORIZON_MONTHS,
    bull_assumptions: dict[str, Any],
    base_assumptions: dict[str, Any],
    bear_assumptions: dict[str, Any],
    scenario_probabilities: dict[str, float],
    current_revenue_ttm_usd: float | None = None,
    shares_outstanding: float | None = None,
    net_debt_usd: float | None = None,
    current_price_usd: float | None = None,
    current_ev_sales: float | None = None,
    analyst_target_mean_usd: float | None = None,
) -> ForwardValuationResult:
    """Compute per-scenario forward fair-value prices. Keyword-only.

    Assumptions are the ValuationAgent's per-scenario blocks (as plain dicts,
    e.g. via ``ValuationAgentOutput.base.model_dump()``). ``scenario_probabilities``
    is ``{"bull": p, "base": p, "bear": p}`` from the agent's
    ``probability_of_occurrence`` fields (NOT the Arbitrated vector). Never raises
    on bad data — degrades to ``is_available=False`` + notes.

    ``cycle_id`` is REQUIRED (Architecture.md §5.2) — pass a non-empty string.
    The orchestrator always passes one; this guard prevents a latent crash if
    a future caller forgets.
    """
    if not cycle_id:
        raise ValueError(
            "compute_forward_valuation: cycle_id is REQUIRED "
            "(Architecture.md §5.2) — pass a non-empty string"
        )

    notes_bits: list[str] = []

    # Clamp horizon into [6, 12].
    horizon = horizon_months
    if horizon < MIN_HORIZON_MONTHS:
        notes_bits.append(f"horizon {horizon_months}mo < {MIN_HORIZON_MONTHS}; clamped")
        horizon = MIN_HORIZON_MONTHS
    elif horizon > MAX_HORIZON_MONTHS:
        notes_bits.append(f"horizon {horizon_months}mo > {MAX_HORIZON_MONTHS}; clamped")
        horizon = MAX_HORIZON_MONTHS
    years = horizon / 12.0

    # --- guard: missing / non-positive market primitives ---
    if current_revenue_ttm_usd is None or current_revenue_ttm_usd <= 0:
        notes_bits.append("TTM revenue unavailable")
    if shares_outstanding is None or shares_outstanding <= 0:
        notes_bits.append("shares outstanding unavailable")
    if net_debt_usd is None:
        notes_bits.append("net debt unavailable")

    if (
        current_revenue_ttm_usd is None
        or current_revenue_ttm_usd <= 0
        or shares_outstanding is None
        or shares_outstanding <= 0
        or net_debt_usd is None
    ):
        result = ForwardValuationResult(
            ticker=ticker,
            cycle_id=cycle_id,
            horizon_months=horizon,
            current_price_usd=current_price_usd,
            shares_outstanding=shares_outstanding,
            net_debt_usd=net_debt_usd,
            current_revenue_ttm_usd=current_revenue_ttm_usd,
            current_ev_sales=current_ev_sales,
            analyst_target_mean_usd=analyst_target_mean_usd,
            notes="; ".join(notes_bits),
        )
        _log(ticker, cycle_id, result, reason="degraded")
        return result

    rev = float(current_revenue_ttm_usd)
    shares = float(shares_outstanding)
    net_debt = float(net_debt_usd)

    bull_pt = _scenario_price(
        scenario="bull", assumptions=bull_assumptions,
        current_revenue_ttm=rev, shares_outstanding=shares,
        net_debt_usd=net_debt, years=years,
    )
    base_pt = _scenario_price(
        scenario="base", assumptions=base_assumptions,
        current_revenue_ttm=rev, shares_outstanding=shares,
        net_debt_usd=net_debt, years=years,
    )
    bear_pt = _scenario_price(
        scenario="bear", assumptions=bear_assumptions,
        current_revenue_ttm=rev, shares_outstanding=shares,
        net_debt_usd=net_debt, years=years,
    )

    bull_price = bull_pt.price_usd
    base_price = base_pt.price_usd
    bear_price = bear_pt.price_usd

    # Expected price across the agent's scenario probabilities (NOT Arbitrated).
    expected_price_usd: float | None = None
    p_bull = _coerce_frac(scenario_probabilities.get("bull"))
    p_base = _coerce_frac(scenario_probabilities.get("base"))
    p_bear = _coerce_frac(scenario_probabilities.get("bear"))
    if None not in (p_bull, p_base, p_bear) and None not in (bull_price, base_price, bear_price):
        total = p_bull + p_base + p_bear
        if total > 0:
            expected_price_usd = round(
                (p_bull / total) * bull_price + (p_base / total) * base_price + (p_bear / total) * bear_price,
                2,
            )
    if expected_price_usd is None and None in (bull_price, base_price, bear_price):
        missing = [n for n, p in (("bull", bull_price), ("base", base_price), ("bear", bear_price)) if p is None]
        notes_bits.append("scenario price(s) missing for expected price: " + ", ".join(missing))

    # Carry scenario-point notes into the top-level notes when the base case failed.
    if base_price is None and base_pt.notes:
        notes_bits.append(f"base: {base_pt.notes}")

    # Surface underwater distress in-band (V-002): a floored-at-$0 base price
    # is a real valuation result, not a missing primitive — flag it explicitly
    # so the memo and ScenarioPriceEngine can distinguish "no result" from
    # "result: equity floored at zero".
    base_price_underwater = base_price == 0.0 and base_pt.equity_value_usd == 0.0

    result = ForwardValuationResult(
        ticker=ticker,
        cycle_id=cycle_id,
        horizon_months=horizon,
        bull_price=bull_price,
        base_price=base_price,
        bear_price=bear_price,
        expected_price_usd=expected_price_usd,
        scenario_points={"bull": bull_pt, "base": base_pt, "bear": bear_pt},
        current_price_usd=current_price_usd,
        shares_outstanding=shares,
        net_debt_usd=net_debt,
        current_revenue_ttm_usd=rev,
        current_ev_sales=current_ev_sales,
        analyst_target_mean_usd=analyst_target_mean_usd,
        base_price_underwater=base_price_underwater,
        notes="; ".join(notes_bits) if notes_bits else "",
    )
    _log(ticker, cycle_id, result, reason="computed")
    return result


def _log(ticker: str, cycle_id: str, result: ForwardValuationResult, reason: str) -> None:
    log_debug(
        "FORWARD_VALUATION_COMPUTED",
        payload={
            "ticker": ticker,
            "reason": reason,
            "horizon_months": result.horizon_months,
            "bull_price": result.bull_price,
            "base_price": result.base_price,
            "bear_price": result.bear_price,
            "expected_price_usd": result.expected_price_usd,
            "is_available": result.is_available,
            "base_price_underwater": result.base_price_underwater,
            "notes": result.notes,
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"forward valuation {ticker}: base={result.base_price} ({reason})",
    )
