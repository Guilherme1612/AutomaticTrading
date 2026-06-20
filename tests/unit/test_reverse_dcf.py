"""Unit tests for the ReverseDcfEngine (Architecture.md §9.4b)."""
from __future__ import annotations

from pmacs.engines.reverse_dcf import compute_reverse_dcf


class TestReverseDcfMath:
    def test_implied_growth_round_trips(self):
        # mc = fcf*(1+g)/(r-g) with g=0.05, r=0.10, fcf=1e8 -> mc = 2.1e9
        # implied = (mc*r - fcf)/(mc+fcf) = 0.05 exactly.
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=0.05, discount_rate=0.10,
        )
        assert r.implied_growth_pct is not None
        assert abs(r.implied_growth_pct - 0.05) < 1e-6
        assert r.fair_value_usd is not None
        # fair value at assumed=0.05 equals the input market cap.
        assert abs(r.fair_value_usd - 2_100_000_000.0) < 1.0
        assert r.is_available

    def test_bullish_lean_when_market_under_pricing_growth(self):
        # implied=0.05, assumed=0.09 -> gap +0.04 -> BULLISH
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=0.09, discount_rate=0.10,
        )
        assert r.valuation_lean == "BULLISH"
        assert r.growth_gap_pct is not None and r.growth_gap_pct > 0

    def test_bearish_lean_when_market_over_pricing_growth(self):
        # implied=0.05, assumed=0.01 -> gap -0.04 -> BEARISH
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=0.01, discount_rate=0.10,
        )
        assert r.valuation_lean == "BEARISH"

    def test_neutral_lean_when_gap_within_threshold(self):
        # implied=0.05, assumed=0.06 -> gap +0.01 -> NEUTRAL (threshold 0.03)
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=0.06, discount_rate=0.10,
        )
        assert r.valuation_lean == "NEUTRAL"

    def test_sensitivity_populated(self):
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=0.05, discount_rate=0.10,
        )
        assert len(r.sensitivity) >= 2
        # every sensitivity value is a positive fair value
        assert all(v > 0 for v in r.sensitivity.values())


class TestReverseDcfDegradation:
    def test_fcf_non_positive_is_neutral_not_fabricated(self):
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=-50_000_000.0,
            assumed_growth_pct=0.09, discount_rate=0.10,
        )
        assert r.valuation_lean == "NEUTRAL"
        assert not r.is_available
        assert r.implied_growth_pct is None
        assert "FCF non-positive" in r.notes

    def test_zero_fcf_is_neutral(self):
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=0.0,
            assumed_growth_pct=0.09, discount_rate=0.10,
        )
        assert r.valuation_lean == "NEUTRAL"
        assert not r.is_available

    def test_missing_market_cap_is_neutral(self):
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=None, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=0.09, discount_rate=0.10,
        )
        assert r.valuation_lean == "NEUTRAL"
        assert "market cap unavailable" in r.notes

    def test_missing_assumed_growth_uses_terminal_floor(self):
        # No assumed growth -> engine falls back to terminal_growth_pct (0.02).
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=None, discount_rate=0.10,
        )
        assert r.assumed_growth_pct == 0.02
        assert r.implied_growth_pct is not None

    def test_assumed_growth_above_discount_undefines_fair_value(self):
        # assumed=0.12 >= r=0.10 -> fair value None, noted, but implied still solved.
        r = compute_reverse_dcf(
            ticker="X", cycle_id="c1",
            market_cap_usd=2_100_000_000.0, fcf_ttm_usd=100_000_000.0,
            assumed_growth_pct=0.12, discount_rate=0.10,
        )
        assert r.implied_growth_pct is not None
        assert r.fair_value_usd is None
        assert "assumed growth >= discount" in r.notes