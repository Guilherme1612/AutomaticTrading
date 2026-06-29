"""Unit tests for the ScenarioPriceEngine (Architecture.md §9.4b)."""
from __future__ import annotations

from pmacs.engines.scenario_price import compute_scenario_price


class TestScenarioPriceMath:
    def test_expected_price_is_probability_weighted(self):
        r = compute_scenario_price(
            ticker="X", cycle_id="c1",
            p_up=0.6, p_flat=0.3, p_down=0.1,
            bull_price=120.0, base_price=100.0, bear_price=70.0,
        )
        assert r.expected_price_usd is not None
        expected = 0.6 * 120.0 + 0.3 * 100.0 + 0.1 * 70.0  # 109.0
        assert abs(r.expected_price_usd - expected) < 1e-6
        assert r.is_available

    def test_expected_return_vs_current(self):
        r = compute_scenario_price(
            ticker="X", cycle_id="c1",
            p_up=0.6, p_flat=0.3, p_down=0.1,
            bull_price=120.0, base_price=100.0, bear_price=70.0,
            current_price_usd=100.0,
        )
        # expected 109 / 100 - 1 = +9.0%
        assert r.expected_return_pct is not None
        assert abs(r.expected_return_pct - 9.0) < 1e-6

    def test_probs_normalized_when_within_tolerance(self):
        r = compute_scenario_price(
            ticker="X", cycle_id="c1",
            p_up=0.60, p_flat=0.30, p_down=0.09,  # sum 0.99
            bull_price=120.0, base_price=100.0, bear_price=70.0,
        )
        # Normalized to 1.0 then weighted: 0.606..*120 + 0.303..*100 + 0.0909..*70
        assert r.expected_price_usd is not None
        assert r.is_available
        assert abs((r.p_up + r.p_flat + r.p_down) - 1.0) < 1e-6


class TestScenarioPriceDegradation:
    def test_missing_bull_price_degrades(self):
        r = compute_scenario_price(
            ticker="X", cycle_id="c1",
            p_up=0.6, p_flat=0.3, p_down=0.1,
            bull_price=None, base_price=100.0, bear_price=70.0,
        )
        assert not r.is_available
        assert r.expected_price_usd is None
        assert "bull" in r.notes

    def test_bad_probabilities_degrade(self):
        r = compute_scenario_price(
            ticker="X", cycle_id="c1",
            p_up=0.3, p_flat=0.1, p_down=0.1,  # sum 0.5
            bull_price=120.0, base_price=100.0, bear_price=70.0,
        )
        assert not r.is_available
        assert r.expected_price_usd is None
        assert "probabilities" in r.notes