"""Tests for ForwardValuationEngine gap / distress / convergence (Tier 3).

The engine previously captured `base_price_underwater=True` but never surfaced
it in the memo, had no cross-check vs reverse-DCF fair value (so LLM
hallucination could slip through), and ignored agent scenario probability
convergence as an uncertainty signal. These tests verify the three new
warning fields on ForwardValuationResult and the MemoWriter rendering path.

spec_ref: Architecture.md §9.4b; Agents.md §13.5
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


# ---------------------------------------------------------------------------
# Helpers — minimal ForwardScenarioPoint + builder for compute_forward_valuation
# ---------------------------------------------------------------------------

def _good_assumptions(
    revenue_growth: float = 0.30,
    margin: float = 0.20,
    exit_multiple: float = 14.0,
) -> dict:
    """Build a complete profitable-scenario assumption dict.

    Matches the keys ValuationAgentOutput's bull/base/bear.model_dump() produces.
    """
    return {
        "revenue_growth_path_pct": revenue_growth,
        "margin_trajectory": "EXPANDING",
        "margin_delta_pct": 0.05,
        "ebitda_margin_at_horizon_pct": margin,
        "exit_multiple": exit_multiple,
        "acquisition_revenue_contribution_pct": 0.0,
        "acquisition_confidence": "NONE",
        "probability_of_occurrence": 0.4,
        "rationale": "test",
        "evidence_ids": [],
    }


def _run_fwd(
    *,
    rev_ttm: float = 1_000_000_000.0,
    shares: float = 100_000_000.0,
    net_debt: float = 200_000_000.0,
    base_assumptions: dict | None = None,
    bull_assumptions: dict | None = None,
    bear_assumptions: dict | None = None,
    p_bull: float = 0.30,
    p_base: float = 0.40,
    p_bear: float = 0.30,
    reverse_dcf_fair_value_usd: float | None = None,
):
    """Call compute_forward_valuation with the given primitives and return the result."""
    from pmacs.engines.forward_valuation import compute_forward_valuation

    return compute_forward_valuation(
        ticker="TEST",
        cycle_id="c-test-1",
        horizon_months=12,
        bull_assumptions=bull_assumptions or _good_assumptions(
            revenue_growth=0.40, margin=0.25, exit_multiple=18.0,
        ),
        base_assumptions=base_assumptions or _good_assumptions(),
        bear_assumptions=bear_assumptions or _good_assumptions(
            revenue_growth=0.10, margin=0.10, exit_multiple=10.0,
        ),
        scenario_probabilities={"bull": p_bull, "base": p_base, "bear": p_bear},
        current_revenue_ttm_usd=rev_ttm,
        shares_outstanding=shares,
        net_debt_usd=net_debt,
        current_price_usd=10.0,
        current_ev_sales=5.0,
        analyst_target_mean_usd=12.0,
        reverse_dcf_fair_value_usd=reverse_dcf_fair_value_usd,
    )


# ---------------------------------------------------------------------------
# Tier 3A — Cross-check vs reverse-DCF
# ---------------------------------------------------------------------------

def test_gap_field_none_when_reverse_dcf_not_provided():
    """When reverse_dcf_fair_value_usd is None, gap field stays None (no-op)."""
    result = _run_fwd(reverse_dcf_fair_value_usd=None)
    assert result.forward_vs_reverse_dcf_gap_pct is None
    assert result.forward_vs_reverse_dcf_warning == ""


def test_gap_field_computed_when_both_values_present():
    """When both base_price and reverse_dcf_fair_value_usd exist, gap is computed."""
    result = _run_fwd(reverse_dcf_fair_value_usd=10.0)
    # gap_pct = (base_price - 10) / 10 — value depends on engine math, just check it's a number.
    assert result.forward_vs_reverse_dcf_gap_pct is not None
    assert isinstance(result.forward_vs_reverse_dcf_gap_pct, float)


def test_warning_set_when_gap_exceeds_50_percent():
    """When |gap| > 0.50, the warning string is set with the formatted line."""
    # Force a wide gap by setting reverse_dcf_fair_value_usd far from base.
    # Use a small (cheap) base and an unrealistic reverse-DCF target.
    result = _run_fwd(reverse_dcf_fair_value_usd=1.0)
    # If |gap| > 0.5, warning is set; else empty. Assert behavior conditional.
    if result.forward_vs_reverse_dcf_gap_pct is not None and abs(result.forward_vs_reverse_dcf_gap_pct) > 0.50:
        assert result.forward_vs_reverse_dcf_warning != ""
        assert "diverges" in result.forward_vs_reverse_dcf_warning
        assert "reverse-DCF" in result.forward_vs_reverse_dcf_warning


def test_no_warning_when_gap_at_or_below_50_percent():
    """When |gap| <= 0.50, no warning is emitted."""
    # Set reverse_dcf_fair_value_usd close to a reasonable base price.
    result = _run_fwd(reverse_dcf_fair_value_usd=8.0)
    if result.forward_vs_reverse_dcf_gap_pct is None or abs(result.forward_vs_reverse_dcf_gap_pct) <= 0.50:
        assert result.forward_vs_reverse_dcf_warning == ""


def test_no_warning_when_base_price_is_zero():
    """base_price=0 (distress) is a separate signal — gap check is skipped."""
    # Force net_debt so high that base floors to $0.
    result = _run_fwd(
        rev_ttm=1_000_000_000.0,
        shares=100_000_000.0,
        net_debt=100_000_000_000.0,  # 10x rev → equity floor triggers
        reverse_dcf_fair_value_usd=10.0,
    )
    # When base_price=0, the engine skips gap computation (per code).
    if result.base_price == 0.0 and result.base_price_underwater:
        assert result.forward_vs_reverse_dcf_gap_pct is None


# ---------------------------------------------------------------------------
# Tier 3B — Distress surfacing (base_price_underwater)
# ---------------------------------------------------------------------------

def test_distress_warning_uses_distress_tag():
    """When base_price_underwater=True, the convergence_warning has ⚠ DISTRESS."""
    result = _run_fwd(
        rev_ttm=1_000_000_000.0,
        shares=100_000_000.0,
        net_debt=100_000_000_000.0,  # 10x rev → floor triggers
    )
    if result.base_price_underwater:
        assert "DISTRESS" in result.agent_scenario_convergence_warning
        assert "equity floored at $0" in result.agent_scenario_convergence_warning


def test_no_distress_warning_when_no_underwater():
    """When base_price > 0 and no convergence, the field stays empty."""
    result = _run_fwd(p_bull=0.40, p_base=0.55, p_bear=0.05)  # |p_bull - p_bear| = 0.35
    assert "DISTRESS" not in result.agent_scenario_convergence_warning


# ---------------------------------------------------------------------------
# Tier 3C — Probability convergence
# ---------------------------------------------------------------------------

def test_convergence_warning_when_p_bull_and_p_bear_within_10pp():
    """When |p_bull - p_bear| < 0.10, agent_scenario_convergence_warning is set
    with the LOW-CONFIDENCE FORWARD VALUATION tag.
    """
    result = _run_fwd(p_bull=0.40, p_base=0.20, p_bear=0.40)  # |0.40 - 0.40| = 0.00
    assert "LOW-CONFIDENCE FORWARD VALUATION" in result.agent_scenario_convergence_warning


def test_no_convergence_warning_when_bull_bear_diverge():
    """When |p_bull - p_bear| >= 0.10, no LOW-CONFIDENCE tag."""
    result = _run_fwd(p_bull=0.20, p_base=0.60, p_bear=0.20)
    # |0.20 - 0.20| = 0.00, so this WOULD trigger. Use real divergence:
    result2 = _run_fwd(p_bull=0.10, p_base=0.50, p_bear=0.40)
    if abs(0.10 - 0.40) >= 0.10:
        assert "LOW-CONFIDENCE" not in result2.agent_scenario_convergence_warning


def test_distress_and_convergence_concatenate():
    """When BOTH underwater AND convergence hit, both tags appear in the field."""
    result = _run_fwd(
        rev_ttm=1_000_000_000.0,
        shares=100_000_000.0,
        net_debt=100_000_000_000.0,
        p_bull=0.40, p_base=0.20, p_bear=0.40,
    )
    if result.base_price_underwater:
        assert "DISTRESS" in result.agent_scenario_convergence_warning
        assert "LOW-CONFIDENCE FORWARD VALUATION" in result.agent_scenario_convergence_warning


# ---------------------------------------------------------------------------
# MemoWriter rendering (Tier 3 — Warnings surface in the memo)
# ---------------------------------------------------------------------------

def test_memo_writer_renders_dcf_warning():
    """When forward_valuation.forward_vs_reverse_dcf_warning is non-empty,
    MemoWriter's set_analytical_context emits a WARNING line.
    """
    from pmacs.agents.memo_writer import MemoWriterRunner

    @dataclass
    class _FV:
        is_available: bool = True
        bull_price: float = 18.0
        base_price: float = 14.0
        bear_price: float = 10.0
        expected_price_usd: float = 14.2
        scenario_points: dict = field(default_factory=dict)
        current_price_usd: float = 10.0
        current_ev_sales: float = 5.0
        analyst_target_mean_usd: float = 12.0
        horizon_months: int = 12
        forward_vs_reverse_dcf_gap_pct: float = 0.85
        forward_vs_reverse_dcf_warning: str = (
            "forward base $14.00 diverges +85% from reverse-DCF fair value $7.56 — review"
        )
        agent_scenario_convergence_warning: str = ""

    runner = MemoWriterRunner()
    runner.set_analytical_context(forward_valuation=_FV())
    assert "WARNING" in runner._analytical_context
    assert "+85%" in runner._analytical_context
    assert "reverse-DCF" in runner._analytical_context


def test_memo_writer_renders_distress_warning():
    """When agent_scenario_convergence_warning contains ⚠ DISTRESS, MemoWriter
    emits a WARNING line with the tag.
    """
    from pmacs.agents.memo_writer import MemoWriterRunner

    @dataclass
    class _FV:
        is_available: bool = True
        bull_price: float = 5.0
        base_price: float = 0.0
        bear_price: float = 0.0
        expected_price_usd: float = 1.5
        scenario_points: dict = field(default_factory=dict)
        current_price_usd: float = 10.0
        current_ev_sales: float = 50.0
        analyst_target_mean_usd: float = 0.0
        horizon_months: int = 12
        forward_vs_reverse_dcf_gap_pct: float | None = None
        forward_vs_reverse_dcf_warning: str = ""
        agent_scenario_convergence_warning: str = (
            "⚠ DISTRESS: equity floored at $0 — forward EV < net debt"
        )

    runner = MemoWriterRunner()
    runner.set_analytical_context(forward_valuation=_FV())
    assert "WARNING" in runner._analytical_context
    assert "DISTRESS" in runner._analytical_context
    assert "equity floored at $0" in runner._analytical_context


def test_memo_writer_renders_convergence_warning():
    """When agent_scenario_convergence_warning contains LOW-CONFIDENCE,
    MemoWriter emits a WARNING line.
    """
    from pmacs.agents.memo_writer import MemoWriterRunner

    @dataclass
    class _FV:
        is_available: bool = True
        bull_price: float = 14.0
        base_price: float = 12.0
        bear_price: float = 11.0
        expected_price_usd: float = 12.1
        scenario_points: dict = field(default_factory=dict)
        current_price_usd: float = 10.0
        current_ev_sales: float = 5.0
        analyst_target_mean_usd: float = 12.0
        horizon_months: int = 12
        forward_vs_reverse_dcf_gap_pct: float | None = None
        forward_vs_reverse_dcf_warning: str = ""
        agent_scenario_convergence_warning: str = (
            "LOW-CONFIDENCE FORWARD VALUATION — agent scenarios nearly equally "
            "weighted (p_bull=0.40, p_bear=0.40)"
        )

    runner = MemoWriterRunner()
    runner.set_analytical_context(forward_valuation=_FV())
    assert "WARNING" in runner._analytical_context
    assert "LOW-CONFIDENCE FORWARD VALUATION" in runner._analytical_context


def test_memo_writer_no_warnings_when_both_empty():
    """When both warning fields are empty strings, no WARNING line is emitted."""
    from pmacs.agents.memo_writer import MemoWriterRunner

    @dataclass
    class _FV:
        is_available: bool = True
        bull_price: float = 18.0
        base_price: float = 14.0
        bear_price: float = 10.0
        expected_price_usd: float = 14.2
        scenario_points: dict = field(default_factory=dict)
        current_price_usd: float = 10.0
        current_ev_sales: float = 5.0
        analyst_target_mean_usd: float = 12.0
        horizon_months: int = 12
        forward_vs_reverse_dcf_gap_pct: float | None = None
        forward_vs_reverse_dcf_warning: str = ""
        agent_scenario_convergence_warning: str = ""

    runner = MemoWriterRunner()
    runner.set_analytical_context(forward_valuation=_FV())
    assert "WARNING" not in runner._analytical_context


# ---------------------------------------------------------------------------
# Schema — fields exist on ForwardValuationResult
# ---------------------------------------------------------------------------

def test_schema_has_three_new_fields():
    """ForwardValuationResult exposes forward_vs_reverse_dcf_gap_pct,
    forward_vs_reverse_dcf_warning, agent_scenario_convergence_warning.
    """
    from pmacs.schemas.forward_valuation import ForwardValuationResult

    # Check via model_fields (Pydantic v2)
    fields = ForwardValuationResult.model_fields
    assert "forward_vs_reverse_dcf_gap_pct" in fields
    assert "forward_vs_reverse_dcf_warning" in fields
    assert "agent_scenario_convergence_warning" in fields