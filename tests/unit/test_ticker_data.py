"""Unit tests for the Ticker Data route helpers.

Covers deterministic extraction of primitives from stored evidence and the
analyst-consensus normalization. The full page render is tested via integration
fixtures; this module stays fast and offline.
"""
from __future__ import annotations

from pmacs.web.routes.ticker_data import _extract_analyst, _build_evidence_text


def test_build_evidence_text_joins_all_evidence():
    ev = {
        "fundamentals_TST_metrics": {"peNormalizedAnnual": 20.0, "forwardPE": 18.0},
        "technical_TST_moving_averages": {"current_price": 150.0},
        "agent_TST_value": {"analysis": "NRR is 118% and ARR grew to $1.2B."},
    }
    evidence_text, agent_text = _build_evidence_text("TST", ev)
    assert "peNormalizedAnnual: 20.0" in evidence_text
    assert "current_price: 150.0" in evidence_text
    assert "NRR is 118%" in agent_text


def test_extract_analyst_prefers_yahoo_price_target():
    ev = {
        "yahoo_TST_price_target": {
            "target_mean": 180.0,
            "target_high": 200.0,
            "target_low": 150.0,
            "target_median": 175.0,
            "num_analysts": 15,
            "current_price": 160.0,
            "upside_to_mean_pct": 12.5,
        },
        "finnhub_TST_price_target": {
            "target_mean": 170.0,
            "analyst_count": 10,
        },
        "finnhub_TST_analyst_recommendations": {
            "strong_buy": 3,
            "buy": 6,
            "hold": 5,
            "sell": 1,
            "strong_sell": 0,
            "total_analysts": 15,
            "consensus": "Buy",
        },
    }
    a = _extract_analyst("TST", ev)
    assert a["target_mean"] == 180.0
    assert a["num_analysts"] == 15
    assert a["buy"] == 6
    assert a["total_analysts"] == 15
    assert a["consensus"] == "Buy"


def test_extract_analyst_falls_back_to_finnhub():
    ev = {
        "finnhub_TST_price_target": {
            "target_mean": 170.0,
            "target_high": 190.0,
            "target_low": 140.0,
            "target_median": 165.0,
            "analyst_count": 10,
        },
    }
    a = _extract_analyst("TST", ev)
    assert a["target_mean"] == 170.0
    assert a["num_analysts"] == 10


def test_extract_analyst_returns_empty_on_no_data():
    assert _extract_analyst("TST", {}) == {
        "target_mean": None,
        "target_median": None,
        "target_high": None,
        "target_low": None,
        "num_analysts": None,
        "current_price": None,
        "upside_to_mean_pct": None,
        "strong_buy": None,
        "buy": None,
        "hold": None,
        "sell": None,
        "strong_sell": None,
        "total_analysts": None,
        "consensus": None,
    }
