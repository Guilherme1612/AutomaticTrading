"""Unit tests for the ForwardValuationEngine (Architecture.md §9.4b).

Verifies the deterministic EV/EBITDA forward-price math, scenario-probability
weighting, horizon clamping, per-primitive graceful degradation, round-trip
into ScenarioPriceEngine, and the frozen Pydantic schema.
"""
from __future__ import annotations

import pytest

from pmacs.engines.forward_valuation import compute_forward_valuation
from pmacs.schemas.forward_valuation import ForwardValuationResult


def _assumptions(
    *, g: float, margin: float, exit_mult: float | None = None,
    exit_sales: float | None = None,
    acq: float = 0.0, acq_conf: str = "NONE", prob: float = 0.33,
    ev_id: str = "e1",
) -> dict:
    return {
        "revenue_growth_path_pct": g,
        "margin_trajectory": "STABLE",
        "margin_delta_pct": 0.0,
        "ebitda_margin_at_horizon_pct": margin,
        "acquisition_revenue_contribution_pct": acq,
        "acquisition_confidence": acq_conf,
        "exit_multiple": exit_mult,
        "exit_sales_multiple": exit_sales,
        "rationale": f"scenario growth {g} evidence={ev_id}",
        "probability_of_occurrence": prob,
        "evidence_ids": [ev_id],
    }


class TestForwardValuationMath:
    def test_base_case_price_to_the_cent(self):
        # rev=1e9, g=0.10, horizon=12mo -> years=1.0
        # organic = 1e9 * 1.10 = 1.1e9; acq=0 -> forward_revenue=1.1e9
        # ebitda = 1.1e9 * 0.20 = 2.2e8; ev = 2.2e8 * 15 = 3.3e9
        # equity = 3.3e9 - 2e8(net_debt) = 3.1e9; price = 3.1e9 / 1e8 shares = 31.0
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
            current_price_usd=25.0,
        )
        assert r.is_available
        assert r.base_price is not None
        assert abs(r.base_price - 31.0) < 1e-6
        # bull: organic=1e9*1.20=1.2e9; ebitda=1.2e9*0.25=3e8; ev=3e8*18=5.4e9;
        # equity=5.4e9-2e8=5.2e9; price=5.2e9/1e8=52.0
        assert abs(r.bull_price - 52.0) < 1e-6
        # bear: organic=1e9*1.02=1.02e9; ebitda=1.02e9*0.16=1.632e8; ev=1.632e9;
        # equity=1.632e9-2e8=1.432e9; price=14.32
        assert abs(r.bear_price - 14.32) < 1e-6

    def test_expected_price_is_scenario_probability_weighted(self):
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        # expected = 0.30*52 + 0.40*31 + 0.30*14.32 = 15.6 + 12.4 + 4.296 = 32.296 -> 32.30
        assert r.expected_price_usd is not None
        assert abs(r.expected_price_usd - 32.30) < 1e-6

    def test_expected_price_normalizes_non_unit_probs(self):
        # probs sum to 0.99 -> engine normalizes by total.
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.29),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.29},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        assert r.expected_price_usd is not None
        # 0.30/0.99 * 52 + 0.40/0.99 * 31 + 0.29/0.99 * 14.32
        expected = (0.30 / 0.99) * 52.0 + (0.40 / 0.99) * 31.0 + (0.29 / 0.99) * 14.32
        assert abs(r.expected_price_usd - round(expected, 2)) < 1e-6

    def test_acquisition_contribution_adds_to_revenue(self):
        # acq=0.10 -> acq_revenue = 0.10 * 1e9 = 1e8 added to organic.
        # base: organic=1e9*1.10=1.1e9; +acq 1e8 -> 1.2e9; ebitda=1.2e9*0.20=2.4e8;
        # ev=2.4e8*15=3.6e9; equity=3.6e9-2e8=3.4e9; price=34.0 (vs 31.0 without acq)
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40, acq=0.10, acq_conf="LOW"),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        assert r.base_price is not None
        assert abs(r.base_price - 34.0) < 1e-6
        base_pt = r.scenario_points["base"]
        assert base_pt is not None
        assert "acquisition" in base_pt.notes.lower()

    def test_horizon_clamped_to_min(self):
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=3,  # below 6 -> clamp to 6
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        assert r.horizon_months == 6
        assert "clamped" in r.notes.lower()

    def test_horizon_clamped_to_max(self):
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=24,  # above 12 -> clamp to 12
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        assert r.horizon_months == 12
        assert "clamped" in r.notes.lower()

    def test_six_month_horizon_uses_half_year_exponent(self):
        # horizon=6 -> years=0.5; organic = 1e9 * (1.10)^0.5
        import math
        expected_organic = 1_000_000_000.0 * (1.10) ** 0.5
        expected_rev = round(expected_organic, 2)
        expected_ebitda = round(expected_rev * 0.20, 2)
        expected_ev = round(expected_ebitda * 15.0, 2)
        expected_equity = round(expected_ev - 200_000_000.0, 2)
        expected_price = round(expected_equity / 100_000_000.0, 2)
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=6,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        assert r.horizon_months == 6
        assert abs(r.base_price - expected_price) < 1e-6


class TestForwardValuationDegradation:
    def _args(self, **overrides):
        args = dict(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        args.update(overrides)
        return args

    def test_missing_revenue_degrades(self):
        r = compute_forward_valuation(**self._args(current_revenue_ttm_usd=None))
        assert not r.is_available
        assert r.base_price is None
        assert "revenue" in r.notes.lower()

    def test_zero_revenue_degrades(self):
        r = compute_forward_valuation(**self._args(current_revenue_ttm_usd=0.0))
        assert not r.is_available
        assert "revenue" in r.notes.lower()

    def test_missing_shares_degrades(self):
        r = compute_forward_valuation(**self._args(shares_outstanding=None))
        assert not r.is_available
        assert "shares" in r.notes.lower()

    def test_missing_net_debt_degrades(self):
        r = compute_forward_valuation(**self._args(net_debt_usd=None))
        assert not r.is_available
        assert "net debt" in r.notes.lower()

    def test_zero_margin_in_a_scenario_degrades_that_scenario_only(self):
        # base margin = 0 -> base price None, but bull/bear still priced.
        r = compute_forward_valuation(**self._args(
            base_assumptions=_assumptions(g=0.10, margin=0.0, exit_mult=15.0, prob=0.40),
        ))
        assert r.bull_price is not None and r.bull_price > 0
        assert r.base_price is None
        assert r.bear_price is not None
        assert not r.is_available  # base_price None -> not available
        assert r.expected_price_usd is None  # a scenario price missing

    def test_zero_exit_multiple_in_a_scenario_degrades_that_scenario_only(self):
        r = compute_forward_valuation(**self._args(
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=0.0, prob=0.30),
        ))
        assert r.bull_price is not None and r.bull_price > 0
        assert r.base_price is not None and r.base_price > 0
        assert r.bear_price is None
        assert r.is_available  # base available even though bear degraded
        assert r.expected_price_usd is None  # bear missing -> no expected price


class TestForwardValuationRoundTrip:
    def test_round_trips_into_scenario_price(self):
        from pmacs.engines.scenario_price import compute_scenario_price
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        # The orchestrator feeds forward bull/base/bear into scenario_price with
        # the Arbitrated p_up/p_flat/p_down (NOT the agent's scenario probs).
        sp = compute_scenario_price(
            ticker="X", cycle_id="c1",
            p_up=0.5, p_flat=0.3, p_down=0.2,
            bull_price=r.bull_price, base_price=r.base_price, bear_price=r.bear_price,
        )
        assert sp.is_available
        expected = round(0.5 * 52.0 + 0.3 * 31.0 + 0.2 * 14.32, 2)  # 38.16
        assert abs(sp.expected_price_usd - expected) < 1e-6


class TestForwardValuationSchema:
    def test_result_is_frozen(self):
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        with pytest.raises(Exception):
            r.base_price = 999.0  # type: ignore[misc]

    def test_is_available_distinguishes_missing_from_floored(self):
        """V-002 contract: is_available is True iff base_price is not None.
        A floored-at-$0 base price is a real (distress) result — distinct from
        'no result at all' (base_price is None). Consumers that want to react
        to underwater distress should check `base_price_underwater` instead.
        """
        r = ForwardValuationResult(ticker="X", cycle_id="c1", base_price=None)
        assert not r.is_available
        # Floored-at-0 IS available (it's the distress signal):
        r2 = ForwardValuationResult(ticker="X", cycle_id="c1", base_price=0.0,
                                     base_price_underwater=True)
        assert r2.is_available
        assert r2.base_price_underwater is True
        # Normal positive price:
        r3 = ForwardValuationResult(ticker="X", cycle_id="c1", base_price=31.0)
        assert r3.is_available
        assert r3.base_price_underwater is False


class TestForwardValuationEquityFloor:
    """Limited liability: a shareholder's downside is floored at $0. When
    forward EV < net debt the equity is underwater; the engine must floor the
    per-share price at 0 and flag it — never emit a negative price, never
    silently hide the distress signal. (Caught by the AAPL/NBIS live smoke:
    NBIS bear printed -$1.38 before this fix.)"""

    def test_bear_equity_underwater_floored_to_zero(self):
        # High net debt so the bear case is underwater: bear EV < net_debt.
        # bear: organic=1e9*1.02=1.02e9; ebitda=1.02e9*0.16=1.632e8;
        # ev=1.632e8*10=1.632e9; net_debt=2.5e9 -> equity=1.632e9-2.5e9=-868e6 < 0
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=2_500_000_000.0,
        )
        # base: ev=3.3e9-2.5e9=0.8e9 -> price=8.0 (>0, so is_available stays True)
        assert r.is_available
        assert r.bear_price == 0.0
        assert r.bear_price is not None and r.bear_price >= 0.0
        bear_pt = r.scenario_points["bear"]
        assert bear_pt is not None
        assert "underwater" in (bear_pt.notes or "").lower()
        assert bear_pt.equity_value_usd == 0.0

    def test_negative_price_never_emitted(self):
        # Even more extreme net debt — all scenarios underwater. Every price
        # floored at 0; no negative price anywhere.
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=10_000_000_000.0,
        )
        for price in (r.bull_price, r.base_price, r.bear_price):
            assert price is None or price >= 0.0
        # Base floored at 0 → is_available True (V-002 contract; the floored
        # value IS the valuation, surfaced in-band via base_price_underwater).
        # base_price_underwater reflects the distress signal:
        assert r.is_available
        assert r.base_price_underwater is True

    def test_positive_equity_not_floored(self):
        # Sanity: the normal case (equity > 0) is unchanged — no flooring, no
        # underwater note. Regression guard against over-eager clamping.
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.20, margin=0.25, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        assert abs(r.bear_price - 14.32) < 1e-6
        bear_pt = r.scenario_points["bear"]
        assert "underwater" not in (bear_pt.notes or "").lower()
        assert bear_pt.equity_value_usd == pytest.approx(1_432_000_000.0)


class TestForwardValuationCycleIdGuard:
    """V-001 — cycle_id is REQUIRED (Architecture.md §5.2)."""

    def test_empty_cycle_id_raises_value_error(self):
        """The orchestrator always passes a real cycle_id; an empty string
        would have crashed log_debug downstream (cycle_id=None on a
        cycle-scoped event raises per §5.2). The guard catches it early.
        """
        with pytest.raises(ValueError, match="cycle_id is REQUIRED"):
            compute_forward_valuation(
                ticker="X", cycle_id="",  # ← empty string
                bull_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30),
                base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
                bear_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30),
                scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
                current_revenue_ttm_usd=1_000_000_000.0,
                shares_outstanding=100_000_000.0,
                net_debt_usd=200_000_000.0,
            )

    def test_non_empty_cycle_id_does_not_raise(self):
        """Sanity check: a real cycle_id does not trip the guard."""
        r = compute_forward_valuation(
            ticker="X", cycle_id="cyc-abc-123",
            bull_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        assert r.cycle_id == "cyc-abc-123"


class TestForwardValuationUnderwaterFlag:
    """V-002 — distinguish 'computed and floored at $0' from 'missing primitive'."""

    def test_floored_base_price_is_available_with_underwater_flag(self):
        """Forward EV < net debt floors the equity at $0. The result IS the
        valuation (a real distress signal); the underwater flag carries that
        signal in-band instead of silently flipping is_available to False.
        """
        # forward_revenue = 1_000_000_000 * 1.10 = 1.1B (1y at 10% growth)
        # forward_ebitda   = 1.1B * 0.20 = 220M
        # forward_ev       = 220M * 15.0 = 3.3B
        # equity_value     = max(0, 3.3B - 5.0B) = 0  (floored)
        # base_price       = 0 / 100M = 0.0
        r = compute_forward_valuation(
            ticker="ONDS", cycle_id="cyc-onds-1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.30, margin=0.20, exit_mult=18.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.02, margin=0.16, exit_mult=10.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=5_000_000_000.0,  # 5B net debt dwarfs 3.3B forward EV
        )
        assert r.base_price == 0.0
        assert r.base_price_underwater is True
        assert r.is_available is True  # floored-at-0 IS available (V-002 fix)
        # Notes should carry the underwater signal at the scenario-point level
        base_pt = r.scenario_points["base"]
        assert "underwater" in base_pt.notes.lower()

    def test_normal_base_price_has_underwater_flag_false(self):
        """Regression guard: a positive base price (equity > 0) must NOT
        trigger the underwater flag.
        """
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30),
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40),
            bear_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,  # 200M net debt vs 3.3B forward EV = healthy
        )
        assert r.base_price > 0
        assert r.base_price_underwater is False
        assert r.is_available is True


class TestForwardValuationEVPathSelection:
    """V-009 — when both exit multiples are provided, audit which was selected."""

    def test_both_multiples_provided_logs_ev_ebitda_selection(self):
        """If a profitable scenario has BOTH exit_multiple and exit_sales_multiple,
        the EV/EBITDA path wins (per can_ev_ebitda branch order). The selection
        is audit-logged in the notes_bits so the memo can show it.
        """
        r = compute_forward_valuation(
            ticker="X", cycle_id="c1", horizon_months=12,
            bull_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30,
                                           exit_sales=10.0),  # BOTH provided
            base_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.40,
                                           exit_sales=10.0),
            bear_assumptions=_assumptions(g=0.10, margin=0.20, exit_mult=15.0, prob=0.30,
                                           exit_sales=10.0),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=1_000_000_000.0,
            shares_outstanding=100_000_000.0,
            net_debt_usd=200_000_000.0,
        )
        # EV/EBITDA selected → base_pt.notes carries the audit line
        base_pt = r.scenario_points["base"]
        assert base_pt.valuation_path == "ev_ebitda"
        assert "EV/EBITDA path selected" in base_pt.notes
        assert "exit_sales_multiple (10.0)" in base_pt.notes
