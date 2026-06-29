"""Unit tests for the forward-valuation reconciliation block in the memo.

Phase 7c valuation-improvement work (Jun 24): the memo's Forward Valuation
section now surfaces a **Reconciliation** line — the non-obvious multi-way gap
most memos omit:

  market pays <X>x EV/Sales today; agent assumes <Y>x at horizon (<p>% vs market);
  model base <+/-pct> vs current $<px>; model base <+/-pct> vs analyst PT $<pt>

This requires two new fields on ``ForwardValuationResult``
(``current_ev_sales``, ``analyst_target_mean_usd``) populated by the orchestrator
from the same evidence the ValuationAgent saw, plus the per-scenario
``valuation_path`` / ``exit_sales_multiple`` on ``ForwardScenarioPoint``.

These tests verify the wiring — that the reconciliation line renders with all
four gaps when the anchors are present, degrades gracefully when they are not,
and that the new schema fields round-trip through the engine.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pmacs.agents.memo_writer import MemoWriterRunner
from pmacs.engines.forward_valuation import compute_forward_valuation
from pmacs.schemas.forward_valuation import ForwardScenarioPoint, ForwardValuationResult


def _result(
    *,
    base_price=12.07,
    bull_price=21.70,
    bear_price=5.19,
    expected=12.90,
    current_price=7.71,
    current_ev_sales=36.3,
    analyst_pt=20.12,
    exit_sales=30.0,
    path="ev_sales",
) -> ForwardValuationResult:
    return ForwardValuationResult(
        ticker="ONDS",
        cycle_id="t",
        horizon_months=12,
        bull_price=bull_price,
        base_price=base_price,
        bear_price=bear_price,
        expected_price_usd=expected,
        scenario_points={
            "base": ForwardScenarioPoint(
                scenario="base",
                revenue_growth_path_pct=1.0,
                ebitda_margin_at_horizon_pct=-0.50,
                exit_multiple=None,
                exit_sales_multiple=exit_sales,
                valuation_path=path,
                forward_revenue_usd=193_210_000.0,
                forward_ev_usd=5_796_300_000.0,
                equity_value_usd=6_353_230_000.0,
                price_usd=base_price,
            ),
        },
        current_price_usd=current_price,
        shares_outstanding=526_540_800.0,
        net_debt_usd=-556_930_000.0,
        current_revenue_ttm_usd=96_605_000.0,
        current_ev_sales=current_ev_sales,
        analyst_target_mean_usd=analyst_pt,
    )


class TestReconciliationBlock:
    def test_full_reconciliation_line_renders(self):
        runner = MemoWriterRunner()
        runner.set_analytical_context(forward_valuation=_result())
        ctx = runner._analytical_context
        assert "## Forward Valuation (12mo)" in ctx
        assert "Reconciliation:" in ctx
        # All four gaps present
        assert "market pays 36.3x EV/Sales today" in ctx
        assert "agent assumes 30.0x at horizon" in ctx
        assert "vs market" in ctx
        assert "model base" in ctx and "vs current $7.71" in ctx
        assert "vs analyst PT $20.12" in ctx

    def test_pre_profit_path_labelled_in_assumptions(self):
        runner = MemoWriterRunner()
        runner.set_analytical_context(forward_valuation=_result())
        ctx = runner._analytical_context
        assert "exit EV/Sales 30.0x (pre-profit path)" in ctx
        assert "revenue growth 100.0% to horizon" in ctx
        assert "EBITDA margin -50.0%" in ctx

    def test_ev_ebitda_path_shown_when_profitable(self):
        runner = MemoWriterRunner()
        res = _result(path="ev_ebitda")
        # swap the base point to a profitable EV/EBITDA shape
        res = res.model_copy(update={
            "scenario_points": {
                "base": ForwardScenarioPoint(
                    scenario="base",
                    revenue_growth_path_pct=0.10,
                    ebitda_margin_at_horizon_pct=0.28,
                    exit_multiple=18.0,
                    exit_sales_multiple=None,
                    valuation_path="ev_ebitda",
                    price_usd=120.0,
                )
            },
        })
        runner.set_analytical_context(forward_valuation=res)
        ctx = runner._analytical_context
        assert "exit EV/EBITDA 18.0x" in ctx
        # No pre-profit label on the EV/EBITDA path
        assert "pre-profit path" not in ctx

    def test_degrades_when_anchors_missing(self):
        runner = MemoWriterRunner()
        res = _result(current_ev_sales=None, analyst_pt=None, current_price=None)
        runner.set_analytical_context(forward_valuation=res)
        ctx = runner._analytical_context
        # Prices still render
        assert "## Forward Valuation (12mo)" in ctx
        assert "Base=$12.07" in ctx
        # Reconciliation either omitted or only shows the multiple-vs-market part
        # (no current price / analyst PT gaps possible)
        assert "vs current" not in ctx
        assert "vs analyst PT" not in ctx

    def test_unavailable_forward_valuation_shows_notes(self):
        runner = MemoWriterRunner()
        res = ForwardValuationResult(
            ticker="X", cycle_id="t", base_price=None,
            notes="TTM revenue unavailable",
        )
        runner.set_analytical_context(forward_valuation=res)
        ctx = runner._analytical_context
        assert "Forward Valuation" in ctx
        assert "Unavailable" in ctx
        assert "TTM revenue unavailable" in ctx


class TestEnginePassesAnchorsThrough:
    """compute_forward_valuation carries current_ev_sales + analyst PT to the result."""

    def _base_assumptions(self, **over):
        a = {
            "revenue_growth_path_pct": 1.0,
            "margin_trajectory": "STABLE",
            "margin_delta_pct": 0.0,
            "ebitda_margin_at_horizon_pct": -0.50,
            "acquisition_revenue_contribution_pct": 0.0,
            "acquisition_confidence": "NONE",
            "exit_multiple": None,
            "exit_sales_multiple": 30.0,
            "rationale": "anchored to 36x market; evidence_id e1",
            "probability_of_occurrence": 0.40,
            "evidence_ids": ["e1"],
        }
        a.update(over)
        return a

    def test_anchors_round_trip(self):
        res = compute_forward_valuation(
            ticker="ONDS", cycle_id="t", horizon_months=12,
            bull_assumptions=self._base_assumptions(exit_sales_multiple=45.0, probability_of_occurrence=0.30),
            base_assumptions=self._base_assumptions(probability_of_occurrence=0.40),
            bear_assumptions=self._base_assumptions(exit_sales_multiple=15.0, probability_of_occurrence=0.30),
            scenario_probabilities={"bull": 0.30, "base": 0.40, "bear": 0.30},
            current_revenue_ttm_usd=96_605_000.0,
            shares_outstanding=526_540_800.0,
            net_debt_usd=-556_930_000.0,
            current_price_usd=7.71,
            current_ev_sales=36.3,
            analyst_target_mean_usd=20.12,
        )
        assert res.is_available
        assert res.current_ev_sales == 36.3
        assert res.analyst_target_mean_usd == 20.12
        assert res.current_price_usd == 7.71
        # base priced via EV/Sales (pre-profit)
        bp = res.scenario_points["base"]
        assert bp.valuation_path == "ev_sales"
        assert bp.exit_sales_multiple == 30.0
        assert bp.price_usd is not None and bp.price_usd > 0

    def test_degraded_result_still_carries_anchors(self):
        """When primitives are missing the result degrades but still echoes anchors."""
        res = compute_forward_valuation(
            ticker="X", cycle_id="t",
            bull_assumptions=self._base_assumptions(),
            base_assumptions=self._base_assumptions(),
            bear_assumptions=self._base_assumptions(),
            scenario_probabilities={"bull": 0.3, "base": 0.4, "bear": 0.3},
            current_revenue_ttm_usd=None,  # forces degradation
            shares_outstanding=None,
            net_debt_usd=None,
            current_ev_sales=36.3,
            analyst_target_mean_usd=20.12,
        )
        assert not res.is_available
        # Anchors still present for memo fallback rendering
        assert res.current_ev_sales == 36.3
        assert res.analyst_target_mean_usd == 20.12

def _metrics_ev(ticker, *, revenue_ttm, total_debt, cash, ebitda_margin_ttm, rev_growth_ttm_yoy):
    return SimpleNamespace(
        id=f"fundamentals_{ticker}_metrics",
        data={
            "revenueTTM": revenue_ttm,
            "annual_total_debt": [{"period": "2025-12-31", "v": total_debt}],
            "annual_cash": [{"period": "2025-12-31", "v": cash}],
            "ebitdaMarginTTM": ebitda_margin_ttm,
            "revenueGrowthTTMYoy": rev_growth_ttm_yoy,
        },
    )


def _profile_ev(ticker, *, shares_millions):
    return SimpleNamespace(
        id=f"fundamentals_{ticker}_profile",
        data={"shareOutstanding": shares_millions},
    )


def _price_target_ev(ticker, *, current_price, target_mean, upside):
    return SimpleNamespace(
        id=f"yahoo_{ticker}_price_target",
        data={"current_price": current_price, "target_mean": target_mean, "upside_to_mean_pct": upside},
    )


class TestOrchestratorAnchor:
    """_build_current_valuation_anchor returns (str, current_ev_sales, analyst_pt)
    and injects the AUTHORITATIVE TTM revenue growth so the agent doesn't average
    it against conflicting quarterly YoY figures (ONDS 1079% TTM vs 10.8% quarterly)."""

    def _orch(self, tmp_path):
        from pmacs.nervous.orchestrator import CycleOrchestrator
        from pmacs.nervous.sse_publisher import SSEPublisher
        from pmacs.storage.sqlite import init_db
        db = tmp_path / "a.db"
        init_db(db)
        return CycleOrchestrator(
            db_path=db, audit_path=tmp_path / "audit.log",
            sse_publisher=SSEPublisher(), config={"lock_path": str(tmp_path / "l.lock")},
        )

    def test_returns_tuple_with_anchors_and_growth(self, tmp_path):
        orch = self._orch(tmp_path)
        packets = [SimpleNamespace(evidence=[
            _metrics_ev("ONDS", revenue_ttm=96_605_000, total_debt=15_564_000,
                        cash=572_494_000, ebitda_margin_ttm=-150.0, rev_growth_ttm_yoy=1079.9),
            _profile_ev("ONDS", shares_millions=526.5408),
            _price_target_ev("ONDS", current_price=7.71, target_mean=20.12, upside=161.1),
        ])]
        anchor, ev_sales, pt = orch._build_current_valuation_anchor(
            ticker="ONDS", current_price_usd=7.71, revenue_ttm=96_605_000,
            shares=526_540_800, net_debt=-556_930_000, evidence_packets=packets,
        )
        assert isinstance(anchor, str) and anchor
        assert "EV/Sales" in anchor
        assert "TTM revenue growth +1079.9%" in anchor
        assert "AUTHORITATIVE" in anchor
        assert ev_sales == round((526_540_800 * 7.71 - 556_930_000) / 96_605_000, 2)
        assert pt == 20.12

    def test_empty_anchor_when_no_observables(self, tmp_path):
        orch = self._orch(tmp_path)
        anchor, ev_sales, pt = orch._build_current_valuation_anchor(
            ticker="X", current_price_usd=None, revenue_ttm=None,
            shares=None, net_debt=None, evidence_packets=[SimpleNamespace(evidence=[])],
        )
        assert anchor == ""
        assert ev_sales is None
        assert pt is None
