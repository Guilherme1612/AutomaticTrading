"""Unit tests for GrowthHunter, InsiderActivity, ShortInterest, Forensics personas.

Tests:
- Output model instantiation with valid data
- Probability sum validators (reject sums != 1.0)
- Forensics red_flag_count vs len(red_flags) consistency
- InsiderActivity CLUSTER_BUY sanity check
- Sanity validators for persona-specific checks
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from pmacs.agents.sanity.forensics import ForensicsSanity
from pmacs.agents.sanity.growth_hunter import GrowthHunterSanity
from pmacs.agents.sanity.insider_activity import InsiderActivitySanity
from pmacs.agents.sanity.short_interest import ShortInterestSanity
from pmacs.agents.sanity.base import SanityResult
from pmacs.schemas.personas import (
    ForensicsOutput,
    GrowthHunterOutput,
    InsiderActivityOutput,
    InsiderTransaction,
    RedFlag,
    ShortInterestOutput,
)


def _make_evidence(evidence_ids: list[str]) -> list[Any]:
    """Create mock evidence packets with the given IDs."""
    packets = []
    for eid in evidence_ids:
        ev = SimpleNamespace(id=eid, content=f"content for {eid}")
        packet = SimpleNamespace(ticker="TEST", evidence=[ev])
        packets.append(packet)
    return packets


# ---------------------------------------------------------------------------
# GrowthHunterOutput
# ---------------------------------------------------------------------------

class TestGrowthHunterOutput:
    """Tests for GrowthHunterOutput schema."""

    def test_valid_output(self):
        out = GrowthHunterOutput(
            ticker="AAPL",
            revenue_yoy_pct=15.3,
            revenue_acceleration="STABLE",
            gross_margin_pct=46.2,
            gross_margin_trend="EXPANDING",
            tam_penetration_pct=8.5,
            growth_durability="HIGH",
            growth_durability_reasoning="Strong recurring revenue and ecosystem lock-in",
            key_risk_to_growth="Regulatory pressure on App Store",
            p_up=0.5,
            p_flat=0.3,
            p_down=0.2,
            evidence_ids=["ev1"],
        )
        assert out.ticker == "AAPL"
        assert out.revenue_yoy_pct == 15.3

    def test_unknown_metrics(self):
        out = GrowthHunterOutput(
            ticker="UNKNOWN_TICK",
            revenue_yoy_pct=None,
            revenue_acceleration="UNKNOWN",
            gross_margin_pct=None,
            gross_margin_trend="UNKNOWN",
            tam_penetration_pct=None,
            growth_durability="UNKNOWN",
            growth_durability_reasoning="No fundamentals data available",
            key_risk_to_growth="Cannot assess without data",
            p_up=0.34,
            p_flat=0.33,
            p_down=0.33,
            evidence_ids=["ev1"],
        )
        assert out.revenue_yoy_pct is None

    def test_prob_sum_rejects_invalid(self):
        with pytest.raises(ValidationError, match="probabilities sum"):
            GrowthHunterOutput(
                ticker="AAPL",
                revenue_yoy_pct=15.0,
                revenue_acceleration="STABLE",
                gross_margin_pct=46.0,
                gross_margin_trend="STABLE",
                tam_penetration_pct=5.0,
                growth_durability="MODERATE",
                growth_durability_reasoning="Reasonable growth",
                key_risk_to_growth="Competition",
                p_up=0.5,
                p_flat=0.3,
                p_down=0.05,  # sums to 0.85 (>0.10 off -> rejected, not auto-normalized)
                evidence_ids=["ev1"],
            )

    def test_evidence_ids_min_length(self):
        with pytest.raises(ValidationError):
            GrowthHunterOutput(
                ticker="AAPL",
                revenue_acceleration="STABLE",
                gross_margin_trend="STABLE",
                growth_durability="MODERATE",
                growth_durability_reasoning="x",
                key_risk_to_growth="x",
                p_up=0.4,
                p_flat=0.3,
                p_down=0.3,
                evidence_ids=[],  # min_length=1
            )


# ---------------------------------------------------------------------------
# InsiderActivityOutput
# ---------------------------------------------------------------------------

class TestInsiderActivityOutput:
    """Tests for InsiderActivityOutput schema."""

    def _make_transaction(self, **overrides):
        defaults = dict(
            insider_name="Jane Doe",
            insider_role="CFO",
            transaction_type="OPEN_MARKET_BUY",
            amount_usd=250000.0,
            shares=1000,
            date="2026-04-15",
            evidence_id="ev1",
        )
        defaults.update(overrides)
        return InsiderTransaction(**defaults)

    def test_valid_output(self):
        out = InsiderActivityOutput(
            ticker="TSLA",
            transactions=[self._make_transaction()],
            signal="LARGE_BUY",
            signal_reasoning="CFO bought $250K on open market",
            p_up=0.45,
            p_flat=0.35,
            p_down=0.20,
            evidence_ids=["ev1"],
        )
        assert out.signal == "LARGE_BUY"

    def test_no_signal_near_uniform(self):
        out = InsiderActivityOutput(
            ticker="AAPL",
            transactions=[],
            signal="NO_SIGNAL",
            signal_reasoning="Only routine 10b5-1 sales detected",
            p_up=0.34,
            p_flat=0.33,
            p_down=0.33,
            evidence_ids=["ev1"],
        )
        assert out.signal == "NO_SIGNAL"

    def test_prob_sum_rejects_invalid(self):
        with pytest.raises(ValidationError, match="probabilities sum"):
            InsiderActivityOutput(
                ticker="AAPL",
                transactions=[],
                signal="NO_SIGNAL",
                signal_reasoning="Routine",
                p_up=0.5,
                p_flat=0.3,
                p_down=0.05,  # 0.85 (>0.10 off -> rejected)
                evidence_ids=["ev1"],
            )


# ---------------------------------------------------------------------------
# ShortInterestOutput
# ---------------------------------------------------------------------------

class TestShortInterestOutput:
    """Tests for ShortInterestOutput schema."""

    def test_valid_output(self):
        out = ShortInterestOutput(
            ticker="GME",
            short_pct_float=22.5,
            days_to_cover=4.2,
            short_change_pct=-15.3,
            anomaly="NORMAL",
            anomaly_reasoning="Short interest within historical range",
            p_up=0.35,
            p_flat=0.40,
            p_down=0.25,
            evidence_ids=["ev1"],
        )
        assert out.anomaly == "NORMAL"

    def test_insufficient_data(self):
        out = ShortInterestOutput(
            ticker="UNKNOWN",
            short_pct_float=None,
            days_to_cover=None,
            short_change_pct=None,
            anomaly="INSUFFICIENT_DATA",
            anomaly_reasoning="No short interest data available",
            p_up=0.34,
            p_flat=0.33,
            p_down=0.33,
            evidence_ids=["ev1"],
        )
        assert out.short_pct_float is None

    def test_prob_sum_rejects_invalid(self):
        with pytest.raises(ValidationError, match="probabilities sum"):
            ShortInterestOutput(
                ticker="GME",
                anomaly="NORMAL",
                anomaly_reasoning="Normal range",
                p_up=0.6,
                p_flat=0.3,
                p_down=0.3,  # 1.2 (>0.10 off -> rejected)
                evidence_ids=["ev1"],
            )


# ---------------------------------------------------------------------------
# ForensicsOutput
# ---------------------------------------------------------------------------

class TestForensicsOutput:
    """Tests for ForensicsOutput schema."""

    def test_clean_output(self):
        out = ForensicsOutput(
            ticker="MSFT",
            red_flags=[],
            red_flag_count=0,
            overall_accounting_quality="CLEAN",
            p_up=0.40,
            p_flat=0.35,
            p_down=0.25,
            evidence_ids=["ev1"],
        )
        assert out.red_flag_count == 0

    def test_with_red_flags(self):
        flag = RedFlag(
            category="CASH_FLOW_DIVERGENCE",
            severity=0.6,
            description="Net income growing while OCF declining for 2 quarters",
            evidence_ids=["ev1"],
        )
        out = ForensicsOutput(
            ticker="XYZ",
            red_flags=[flag],
            red_flag_count=1,
            overall_accounting_quality="MATERIAL_CONCERNS",
            p_up=0.20,
            p_flat=0.40,
            p_down=0.40,
            evidence_ids=["ev1"],
        )
        assert out.red_flag_count == 1

    def test_red_flag_count_mismatch_rejects(self):
        flag = RedFlag(
            category="REVENUE_QUALITY",
            severity=0.5,
            description="Test flag",
            evidence_ids=["ev1"],
        )
        with pytest.raises(ValidationError, match="red_flag_count"):
            ForensicsOutput(
                ticker="XYZ",
                red_flags=[flag],  # 1 flag
                red_flag_count=2,  # mismatch
                overall_accounting_quality="MINOR_CONCERNS",
                p_up=0.30,
                p_flat=0.40,
                p_down=0.30,
                evidence_ids=["ev1"],
            )

    def test_prob_sum_rejects_invalid(self):
        with pytest.raises(ValidationError, match="probabilities sum"):
            ForensicsOutput(
                ticker="MSFT",
                red_flags=[],
                red_flag_count=0,
                overall_accounting_quality="CLEAN",
                p_up=0.5,
                p_flat=0.3,
                p_down=0.3,  # 1.1
                evidence_ids=["ev1"],
            )


# ---------------------------------------------------------------------------
# Sanity Validators — test _persona_checks directly to isolate persona logic
# from the base validator's generic checks (reasoning, evidence_ids).
# ---------------------------------------------------------------------------

class TestGrowthHunterSanity:
    """Tests for GrowthHunterSanity _persona_checks."""

    def test_valid_passes(self):
        validator = GrowthHunterSanity()
        result = validator._persona_checks({
            "revenue_yoy_pct": 15.0,
            "gross_margin_pct": 46.0,
            "growth_durability_reasoning": "Solid growth",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
        }, [])
        assert result.passed

    def test_revenue_out_of_range_fails(self):
        validator = GrowthHunterSanity()
        result = validator._persona_checks({
            "revenue_yoy_pct": 9999,  # outside widened [-100, 2000] range
            "gross_margin_pct": 46.0,
            "growth_durability_reasoning": "Solid growth",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
        }, [])
        assert not result.passed
        assert "revenue_yoy_pct" in result.reason

    def test_negative_revenue_fails(self):
        validator = GrowthHunterSanity()
        result = validator._persona_checks({
            "revenue_yoy_pct": -150,
            "gross_margin_pct": 46.0,
            "growth_durability_reasoning": "Solid growth",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
        }, [])
        assert not result.passed
        assert "revenue_yoy_pct" in result.reason

    def test_margin_out_of_range_fails(self):
        validator = GrowthHunterSanity()
        result = validator._persona_checks({
            "revenue_yoy_pct": 15.0,
            "gross_margin_pct": 120,
            "growth_durability_reasoning": "Solid growth",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
        }, [])
        assert not result.passed
        assert "gross_margin_pct" in result.reason

    def test_negative_margin_fails(self):
        validator = GrowthHunterSanity()
        result = validator._persona_checks({
            "revenue_yoy_pct": 15.0,
            "gross_margin_pct": -60,
            "growth_durability_reasoning": "Solid growth",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
        }, [])
        assert not result.passed

    def test_empty_durability_reasoning_fails(self):
        validator = GrowthHunterSanity()
        result = validator._persona_checks({
            "revenue_yoy_pct": 15.0,
            "gross_margin_pct": 46.0,
            "growth_durability_reasoning": "",
            "p_up": 0.5,
            "p_flat": 0.3,
            "p_down": 0.2,
        }, [])
        assert not result.passed
        assert "growth_durability_reasoning" in result.reason

    def test_null_metrics_pass(self):
        """None values for revenue/margin should not trigger range checks."""
        validator = GrowthHunterSanity()
        result = validator._persona_checks({
            "revenue_yoy_pct": None,
            "gross_margin_pct": None,
            "growth_durability_reasoning": "No data available",
            "p_up": 0.4,
            "p_flat": 0.35,
            "p_down": 0.25,
        }, [])
        assert result.passed


class TestInsiderActivitySanity:
    """Tests for InsiderActivity sanity _persona_checks."""

    def test_cluster_buy_with_enough_buys_passes(self):
        validator = InsiderActivitySanity()
        txs = [
            {"insider_name": f"Buyer{i}", "insider_role": "VP", "transaction_type": "OPEN_MARKET_BUY",
             "amount_usd": 100000.0, "shares": 500, "date": "2026-04-20", "evidence_id": f"ev{i}"}
            for i in range(3)
        ]
        result = validator._persona_checks({
            "transactions": txs,
            "signal": "CLUSTER_BUY",
            "p_up": 0.45,
            "p_flat": 0.30,
            "p_down": 0.25,
        }, [])
        assert result.passed

    def test_cluster_buy_without_enough_buys_fails(self):
        validator = InsiderActivitySanity()
        txs = [
            {"insider_name": "Buyer1", "insider_role": "VP", "transaction_type": "OPEN_MARKET_BUY",
             "amount_usd": 100000.0, "shares": 500, "date": "2026-04-20", "evidence_id": "ev1"},
            {"insider_name": "Seller1", "insider_role": "Dir", "transaction_type": "OPEN_MARKET_SELL",
             "amount_usd": 200000.0, "shares": 1000, "date": "2026-04-18", "evidence_id": "ev2"},
        ]
        result = validator._persona_checks({
            "transactions": txs,
            "signal": "CLUSTER_BUY",
            "p_up": 0.45,
            "p_flat": 0.30,
            "p_down": 0.25,
        }, [])
        assert not result.passed
        assert "CLUSTER_BUY" in result.reason

    def test_no_signal_near_uniform_passes(self):
        validator = InsiderActivitySanity()
        result = validator._persona_checks({
            "transactions": [],
            "signal": "NO_SIGNAL",
            "p_up": 0.34,
            "p_flat": 0.33,
            "p_down": 0.33,
        }, [])
        assert result.passed

    def test_no_signal_skewed_fails(self):
        validator = InsiderActivitySanity()
        result = validator._persona_checks({
            "transactions": [],
            "signal": "NO_SIGNAL",
            "p_up": 0.60,
            "p_flat": 0.20,
            "p_down": 0.20,
        }, [])
        assert not result.passed
        assert "near-uniform" in result.reason

    def test_insufficient_data_near_uniform_passes(self):
        validator = InsiderActivitySanity()
        result = validator._persona_checks({
            "transactions": [],
            "signal": "INSUFFICIENT_DATA",
            "p_up": 0.34,
            "p_flat": 0.33,
            "p_down": 0.33,
        }, [])
        assert result.passed

    def test_zero_amount_fails(self):
        validator = InsiderActivitySanity()
        txs = [
            {"insider_name": "Buyer1", "insider_role": "VP", "transaction_type": "OPEN_MARKET_BUY",
             "amount_usd": 0, "shares": 0, "date": "2026-04-20", "evidence_id": "ev1"},
        ]
        result = validator._persona_checks({
            "transactions": txs,
            "signal": "LARGE_BUY",
            "p_up": 0.45,
            "p_flat": 0.30,
            "p_down": 0.25,
        }, [])
        assert not result.passed
        assert "amount_usd" in result.reason


class TestShortInterestSanity:
    """Tests for ShortInterest sanity _persona_checks."""

    def test_valid_passes(self):
        validator = ShortInterestSanity()
        result = validator._persona_checks({
            "short_pct_float": 2.5,
            "days_to_cover": 1.2,
            "anomaly": "NORMAL",
            "p_up": 0.35,
            "p_flat": 0.40,
            "p_down": 0.25,
        }, [])
        assert result.passed

    def test_short_pct_out_of_range(self):
        validator = ShortInterestSanity()
        result = validator._persona_checks({
            "short_pct_float": 150.0,
            "days_to_cover": 1.0,
            "anomaly": "NORMAL",
            "p_up": 0.4,
            "p_flat": 0.3,
            "p_down": 0.3,
        }, [])
        assert not result.passed
        assert "short_pct_float" in result.reason

    def test_negative_short_pct_fails(self):
        validator = ShortInterestSanity()
        result = validator._persona_checks({
            "short_pct_float": -5.0,
            "days_to_cover": 1.0,
            "anomaly": "NORMAL",
            "p_up": 0.4,
            "p_flat": 0.3,
            "p_down": 0.3,
        }, [])
        assert not result.passed

    def test_days_to_cover_out_of_range(self):
        validator = ShortInterestSanity()
        result = validator._persona_checks({
            "short_pct_float": 10.0,
            "days_to_cover": 150.0,
            "anomaly": "NORMAL",
            "p_up": 0.4,
            "p_flat": 0.3,
            "p_down": 0.3,
        }, [])
        assert not result.passed
        assert "days_to_cover" in result.reason

    def test_insufficient_data_near_uniform_passes(self):
        validator = ShortInterestSanity()
        result = validator._persona_checks({
            "short_pct_float": None,
            "days_to_cover": None,
            "anomaly": "INSUFFICIENT_DATA",
            "p_up": 0.34,
            "p_flat": 0.33,
            "p_down": 0.33,
        }, [])
        assert result.passed

    def test_insufficient_data_skewed_fails(self):
        validator = ShortInterestSanity()
        result = validator._persona_checks({
            "short_pct_float": None,
            "days_to_cover": None,
            "anomaly": "INSUFFICIENT_DATA",
            "p_up": 0.60,
            "p_flat": 0.20,
            "p_down": 0.20,
        }, [])
        assert not result.passed
        assert "near-uniform" in result.reason

    def test_null_metrics_pass(self):
        """None values should not trigger range checks."""
        validator = ShortInterestSanity()
        result = validator._persona_checks({
            "short_pct_float": None,
            "days_to_cover": None,
            "anomaly": "NORMAL",
            "p_up": 0.4,
            "p_flat": 0.3,
            "p_down": 0.3,
        }, [])
        assert result.passed


class TestForensicsSanity:
    """Tests for Forensics sanity _persona_checks."""

    def test_clean_passes(self):
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [],
            "overall_accounting_quality": "CLEAN",
            "p_up": 0.40,
            "p_flat": 0.35,
            "p_down": 0.25,
        }, [])
        assert result.passed

    def test_clean_with_red_flags_fails(self):
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [{"category": "REVENUE_QUALITY", "severity": 0.3, "description": "x"}],
            "overall_accounting_quality": "CLEAN",
            "p_up": 0.35,
            "p_flat": 0.35,
            "p_down": 0.30,
        }, [])
        assert not result.passed
        assert "CLEAN" in result.reason

    def test_severe_risk_high_severity_passes(self):
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [{"category": "REVENUE_QUALITY", "severity": 0.8, "description": "x"}],
            "overall_accounting_quality": "SEVERE_RISK",
            "p_up": 0.10,
            "p_flat": 0.30,
            "p_down": 0.60,
        }, [])
        assert result.passed

    def test_severe_risk_low_severity_fails(self):
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [{"category": "REVENUE_QUALITY", "severity": 0.3, "description": "x"}],
            "overall_accounting_quality": "SEVERE_RISK",
            "p_up": 0.10,
            "p_flat": 0.30,
            "p_down": 0.60,
        }, [])
        assert not result.passed
        assert "SEVERE_RISK" in result.reason

    def test_material_concerns_p_up_too_high_fails(self):
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [],
            "overall_accounting_quality": "MATERIAL_CONCERNS",
            "p_up": 0.60,
            "p_flat": 0.20,
            "p_down": 0.20,
        }, [])
        assert not result.passed
        assert "p_up" in result.reason

    def test_material_concerns_balanced_passes(self):
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [],
            "overall_accounting_quality": "MATERIAL_CONCERNS",
            "p_up": 0.20,
            "p_flat": 0.40,
            "p_down": 0.40,
        }, [])
        assert result.passed

    def test_severe_risk_p_up_too_high_fails(self):
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [{"category": "CASH_FLOW_DIVERGENCE", "severity": 0.9, "description": "x"}],
            "overall_accounting_quality": "SEVERE_RISK",
            "p_up": 0.60,
            "p_flat": 0.20,
            "p_down": 0.20,
        }, [])
        assert not result.passed
        assert "p_up" in result.reason

    def test_minor_concerns_no_p_up_check(self):
        """MINOR_CONCERNS should not enforce p_up <= p_flat + p_down."""
        validator = ForensicsSanity()
        result = validator._persona_checks({
            "red_flags": [],
            "overall_accounting_quality": "MINOR_CONCERNS",
            "p_up": 0.50,
            "p_flat": 0.25,
            "p_down": 0.25,
        }, [])
        assert result.passed
