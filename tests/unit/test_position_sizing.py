"""Tests for engines/position_sizing.py — pure math, LLMs never do this.

Five Non-Negotiables: position-sizing math must be Python, not LLM-emitted.
These tests pin the math so the hero sizing card stays honest.
"""

from __future__ import annotations

import pytest

from pmacs.engines.position_sizing import (
    SizingInputs,
    SizingResult,
    compute_rr_ratio,
    compute_shares_at_risk,
    compute_sizing,
)


# ── R:R formula ──────────────────────────────────────────────────────────────

class TestComputeRRRatio:
    def test_standard_1_to_3(self):
        # target=160, stop=80, current=100 → (60/20) = 3.0
        assert compute_rr_ratio(160.0, 80.0, 100.0) == pytest.approx(3.0)

    def test_standard_1_to_1(self):
        # target=120, stop=80, current=100 → (20/20) = 1.0
        assert compute_rr_ratio(120.0, 80.0, 100.0) == pytest.approx(1.0)

    def test_decimal(self):
        # target=143, stop=78, current=100 → (43/22) ≈ 1.95
        assert compute_rr_ratio(143.0, 78.0, 100.0) == pytest.approx(1.954, abs=0.01)

    def test_no_upside_returns_none(self):
        # target == current → 0 upside → None
        assert compute_rr_ratio(100.0, 80.0, 100.0) is None

    def test_stop_above_current_returns_none(self):
        # stop=110 > current=100 → stop is wrong side
        assert compute_rr_ratio(120.0, 110.0, 100.0) is None

    def test_stop_equal_current_returns_none(self):
        assert compute_rr_ratio(120.0, 100.0, 100.0) is None

    def test_negative_current_returns_none(self):
        assert compute_rr_ratio(120.0, 80.0, -1.0) is None

    def test_zero_current_returns_none(self):
        assert compute_rr_ratio(120.0, 80.0, 0.0) is None

    def test_zero_stop_returns_none(self):
        assert compute_rr_ratio(120.0, 0.0, 100.0) is None

    def test_zero_target_returns_none(self):
        assert compute_rr_ratio(0.0, 80.0, 100.0) is None


# ── Share count at risk budget ───────────────────────────────────────────────

class TestComputeSharesAtRisk:
    def test_one_percent_risk(self):
        # portfolio=5000, risk=1%=50$, loss_per_share=20 → 50/20 = 2 shares
        assert compute_shares_at_risk(100.0, 80.0, 5000.0, 0.01) == 2

    def test_two_percent_risk(self):
        # 100/20 = 5
        assert compute_shares_at_risk(100.0, 80.0, 5000.0, 0.02) == 5

    def test_five_percent_risk(self):
        # 250/20 = 12
        assert compute_shares_at_risk(100.0, 80.0, 5000.0, 0.05) == 12

    def test_minimum_one_share(self):
        # Tiny risk budget that would round to 0 → return 1 (paper-trade floor)
        assert compute_shares_at_risk(100.0, 99.0, 100.0, 0.01) == 1

    def test_zero_price_returns_none(self):
        assert compute_shares_at_risk(0.0, 80.0, 5000.0, 0.01) is None

    def test_stop_above_current_returns_none(self):
        assert compute_shares_at_risk(100.0, 110.0, 5000.0, 0.01) is None

    def test_zero_portfolio_returns_none(self):
        assert compute_shares_at_risk(100.0, 80.0, 0.0, 0.01) is None

    def test_zero_risk_returns_none(self):
        assert compute_shares_at_risk(100.0, 80.0, 5000.0, 0.0) is None


# ── Full SizingResult ────────────────────────────────────────────────────────

def _inputs(**overrides) -> SizingInputs:
    defaults = dict(
        target_price=160.0,
        stop_price=80.0,
        current_price=100.0,
        portfolio_value=5000.0,
        max_position_pct=0.20,
    )
    defaults.update(overrides)
    return SizingInputs(**defaults)


class TestComputeSizing:
    def test_standard_setup(self):
        r = compute_sizing(_inputs())
        assert r.is_available
        assert r.rr_ratio == pytest.approx(3.0)
        # 1% risk = 50$ / 20$ per share = 2
        assert r.shares_at_1pct == 2
        assert r.shares_at_2pct == 5
        # 5% risk = 250/20 = 12 shares @ $100 = $1200, but the 20% portfolio
        # cap binds tighter: 20% × $5000 = $1000 → max 10 shares. The
        # binding_constraint correctly identifies this.
        assert r.shares_at_5pct == 10
        assert r.binding_constraint == "position_cap"
        # Costs
        assert r.cost_at_1pct == pytest.approx(200.0)
        assert r.cost_at_2pct == pytest.approx(500.0)
        assert r.cost_at_5pct == pytest.approx(1000.0)

    def test_cap_binding(self):
        # Tiny portfolio → 5% risk is tiny, but cap is also tiny.
        # The 20% cap is 200$ (1 share at 100$); 5% risk on $5000 is 250$ = 12 shares.
        # Cap should NOT bind here.
        r = compute_sizing(_inputs(portfolio_value=5000.0))
        # Cap cost: 20% × 5000 = 1000$
        # 5% risk cost: 1200$ → exceeds cap → cap binding.
        assert r.binding_constraint == "position_cap"
        # 12 shares capped to 1000/100 = 10 shares max
        assert r.shares_at_5pct == 10

    def test_risk_binding_when_cap_loose(self):
        # Big portfolio where risk budget is the binding constraint.
        r = compute_sizing(_inputs(portfolio_value=50000.0))
        # 20% cap = $10,000; 5% risk = 5% × 50000 / 20 = 125 shares × 100$ = $12,500
        # Cap cost: 10000 < 12500 → cap binds again.
        # So we expect position_cap as the binding.
        assert r.binding_constraint == "position_cap"

    def test_cap_does_not_bind(self):
        # Configure so cap is loose: max_position_pct=0.80, portfolio=5000 → cap=$4000.
        # 5% risk on $5000 = $250 = 12 shares @ $100 = $1200. Below cap.
        r = compute_sizing(_inputs(max_position_pct=0.80, portfolio_value=5000.0))
        # Cap cost: 4000, 5% cost: 1200. 1200 < 4000 → cap does not bind.
        assert r.binding_constraint in ("risk_2pct", "risk_5pct")
        assert r.shares_at_5pct == 12

    def test_no_upside(self):
        # target == current → R:R None, but share counts still computed.
        r = compute_sizing(_inputs(target_price=100.0))
        assert r.rr_ratio is None
        assert r.shares_at_1pct == 2  # share count independent of R:R

    def test_zero_current_unavailable(self):
        r = compute_sizing(_inputs(current_price=0.0))
        assert not r.is_available
        assert "live price" in r.notes

    def test_stop_above_current_unavailable(self):
        r = compute_sizing(_inputs(stop_price=110.0))
        assert not r.is_available
        assert "stop" in r.notes

    def test_zero_portfolio_unavailable(self):
        r = compute_sizing(_inputs(portfolio_value=0.0))
        assert not r.is_available
        assert "portfolio" in r.notes

    def test_cost_rounded_to_cents(self):
        # Cost must be rounded to 2 decimals (display formatting).
        r = compute_sizing(_inputs())
        assert r.cost_at_1pct == 200.0
        assert r.cost_at_2pct == 500.0
        # 5% risk capped at 20% portfolio → $1000
        assert r.cost_at_5pct == 1000.0
        # Specifically not 200.000001 or 199.9999

    def test_extreme_high_target(self):
        # target=10000 → R:R = (10000-100)/(100-80) = 495
        r = compute_sizing(_inputs(target_price=10000.0))
        assert r.rr_ratio == pytest.approx(495.0)

    def test_results_are_immutable(self):
        # SizingResult is frozen — verify we can rely on it as a value.
        r = compute_sizing(_inputs())
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            r.rr_ratio = 99.0  # type: ignore[misc]
