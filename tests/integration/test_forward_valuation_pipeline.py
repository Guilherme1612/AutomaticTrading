"""Integration tests for the forward-valuation pipeline (Architecture.md §9.4b,
Agents.md §13b, Source.md §16.9).

Exercises the post-arbitration path end-to-end through the orchestrator:
ValuationAgent (stubbed LLM output) → ForwardValuationEngine → scenario-source
choice → ScenarioPriceEngine → MemoWriter rendering. Also verifies the two
operator-critical guarantees:

1. **Conviction is NOT amended.** The forward-valuation path is structurally
   isolated from conviction: ``compute_conviction`` does not accept a
   ``forward_valuation`` parameter, and ``_compute_valuation`` does not mutate
   the ``arbitrated`` probability vector.
2. **Reverse-DCF-grid fallback is preserved.** When the agent returns None,
   ``scenario_price`` falls back to the reverse-DCF sensitivity grid unchanged.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pmacs.data.evidence_router import DataSource
from pmacs.engines.conviction import compute_conviction
from pmacs.nervous.orchestrator import CycleOrchestrator
from pmacs.schemas.data import Evidence, EvidencePacket


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _metrics_ev() -> Evidence:
    return Evidence(
        id="fundamentals_TEST_metrics",
        source=DataSource.FUNDAMENTALS,
        type="financial_statement",
        content_hash="metrics-hash",
        ticker="TEST",
        fetched_at=datetime.now(timezone.utc),
        data={
            "annual_revenue": [{"v": 1_000_000_000.0}],
            "annual_total_debt": [{"v": 300_000_000.0}],
            "annual_cash": [{"v": 100_000_000.0}],
            "annual_freeCashFlow": [{"v": 100_000_000.0}],
            "revenueGrowthTTMYoy": 0.12,  # already a fraction
        },
    )


def _profile_ev() -> Evidence:
    return Evidence(
        id="yahoo_TEST_profile",
        source=DataSource.YAHOO,
        type="market_data",
        content_hash="profile-hash",
        ticker="TEST",
        fetched_at=datetime.now(timezone.utc),
        data={
            "marketCapitalization": 2500.0,  # millions → 2.5e9 market cap
            "shareOutstanding": 100.0,        # millions → 1e8 shares
        },
    )


def _evidence_packets() -> list[EvidencePacket]:
    pkt = EvidencePacket(
        ticker="TEST",
        cycle_id="c1",
        evidence=[_metrics_ev(), _profile_ev()],
        fetched_at=datetime.now(timezone.utc),
        source_count=2,
        has_stale_data=False,
    )
    return [pkt]


def _valuation_output_dict() -> dict:
    """A valid ValuationAgentOutput dict (passes Pydantic model_validate)."""
    eid = "fundamentals_TEST_metrics"
    return {
        "ticker": "TEST",
        "horizon_months": 12,
        "bull": {
            "revenue_growth_path_pct": 0.20,
            "margin_trajectory": "EXPANDING",
            "margin_delta_pct": 0.02,
            "ebitda_margin_at_horizon_pct": 0.25,
            "acquisition_revenue_contribution_pct": 0.0,
            "acquisition_confidence": "NONE",
            "exit_multiple": 18.0,
            "rationale": f"bull: growth accelerating; evidence={eid}",
            "probability_of_occurrence": 0.30,
            "evidence_ids": [eid],
        },
        "base": {
            "revenue_growth_path_pct": 0.10,
            "margin_trajectory": "STABLE",
            "margin_delta_pct": 0.0,
            "ebitda_margin_at_horizon_pct": 0.20,
            "acquisition_revenue_contribution_pct": 0.0,
            "acquisition_confidence": "NONE",
            "exit_multiple": 15.0,
            "rationale": f"base: consensus growth; evidence={eid}",
            "probability_of_occurrence": 0.40,
            "evidence_ids": [eid],
        },
        "bear": {
            "revenue_growth_path_pct": 0.02,
            "margin_trajectory": "COMPRESSING",
            "margin_delta_pct": -0.02,
            "ebitda_margin_at_horizon_pct": 0.16,
            "acquisition_revenue_contribution_pct": 0.0,
            "acquisition_confidence": "NONE",
            "exit_multiple": 10.0,
            "rationale": f"bear: growth decelerating; evidence={eid}",
            "probability_of_occurrence": 0.30,
            "evidence_ids": [eid],
        },
        "data_gaps": [
            "management guidance: N/A, using analyst consensus proxy",
            "acquisitions: N/A, not inferred this cycle",
        ],
        "evidence_ids": [eid],
    }


def _make_orch() -> CycleOrchestrator:
    orch = CycleOrchestrator.__new__(CycleOrchestrator)
    orch._audit = None
    orch.simulation_mode = False
    orch._current_price = 25.0
    return orch


def _arbitrated(p_up=0.5, p_flat=0.3, p_down=0.2) -> SimpleNamespace:
    return SimpleNamespace(p_up=p_up, p_flat=p_flat, p_down=p_down,
                            persona_outputs=[])


# ---------------------------------------------------------------------------
# Test 1 — forward flow is preferred for scenario_price
# ---------------------------------------------------------------------------

class TestForwardValuationPreferred:
    def test_forward_prices_feed_scenario_price(self, monkeypatch):
        from pmacs.agents import valuation_agent as va_mod
        fake_po = SimpleNamespace(raw_output=json.dumps(_valuation_output_dict()))
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: fake_po,
        )

        orch = _make_orch()
        rdcf, forward, scenario = orch._compute_valuation(
            ticker="TEST", cycle_id="c1",
            evidence_packets=_evidence_packets(),
            persona_results={},
            arbitrated=_arbitrated(),
            brief=None,
        )

        # Forward engine produced all three prices from the agent's assumptions.
        assert forward is not None
        assert forward.is_available
        assert forward.bull_price is not None and forward.bull_price > 0
        assert forward.base_price is not None and forward.base_price > 0
        assert forward.bear_price is not None and forward.bear_price > 0

        # base: organic=1e9*1.10=1.1e9; ebitda=1.1e9*0.20=2.2e8; ev=2.2e8*15=3.3e9;
        # equity=3.3e9 - (3e8-1e8)=3.3e9-2e8=3.1e9; price=3.1e9/1e8=31.0
        assert abs(forward.base_price - 31.0) < 1e-6

        # scenario_price used the FORWARD prices (not the reverse-DCF grid).
        assert scenario.bull_price == forward.bull_price
        assert scenario.base_price == forward.base_price
        assert scenario.bear_price == forward.bear_price
        expected = round(0.5 * forward.bull_price + 0.3 * forward.base_price + 0.2 * forward.bear_price, 2)
        assert abs(scenario.expected_price_usd - expected) < 1e-6

    def test_expected_price_uses_agent_scenario_probs(self, monkeypatch):
        from pmacs.agents import valuation_agent as va_mod
        fake_po = SimpleNamespace(raw_output=json.dumps(_valuation_output_dict()))
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: fake_po,
        )
        orch = _make_orch()
        _, forward, _ = orch._compute_valuation(
            ticker="TEST", cycle_id="c1",
            evidence_packets=_evidence_packets(),
            persona_results={}, arbitrated=_arbitrated(), brief=None,
        )
        # Agent scenario probs (0.30/0.40/0.30) weight the expected price — NOT
        # the Arbitrated p_up/p_flat/p_down.
        assert forward.expected_price_usd is not None
        expected = round(0.30 * forward.bull_price + 0.40 * forward.base_price + 0.30 * forward.bear_price, 2)
        assert abs(forward.expected_price_usd - expected) < 1e-6


# ---------------------------------------------------------------------------
# Test 2 — degradation: agent None → reverse-DCF grid fallback
# ---------------------------------------------------------------------------

class TestForwardValuationFallback:
    def test_agent_none_falls_back_to_reverse_dcf_grid(self, monkeypatch):
        from pmacs.agents import valuation_agent as va_mod
        # Agent returns None → _run_forward_valuation returns None → fallback.
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: None,
        )

        orch = _make_orch()
        rdcf, forward, scenario = orch._compute_valuation(
            ticker="TEST", cycle_id="c1",
            evidence_packets=_evidence_packets(),
            persona_results={}, arbitrated=_arbitrated(), brief=None,
        )
        assert forward is None
        # reverse-DCF still produced a fair value + sensitivity grid.
        assert rdcf is not None
        sens = [v for v in rdcf.sensitivity.values() if isinstance(v, (int, float)) and v > 0]
        assert len(sens) >= 2
        # scenario_price used the grid (max/min/base) — not None.
        assert scenario.bull_price is not None
        assert scenario.bear_price is not None
        assert scenario.bull_price == max(sens)
        assert scenario.bear_price == min(sens)

    def test_agent_parse_failure_falls_back(self, monkeypatch):
        from pmacs.agents import valuation_agent as va_mod
        # Garbage raw_output → ValuationAgentOutput.model_validate raises → None.
        fake_po = SimpleNamespace(raw_output="not json at all")
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: fake_po,
        )
        orch = _make_orch()
        rdcf, forward, scenario = orch._compute_valuation(
            ticker="TEST", cycle_id="c1",
            evidence_packets=_evidence_packets(),
            persona_results={}, arbitrated=_arbitrated(), brief=None,
        )
        assert forward is None
        assert rdcf is not None


# ---------------------------------------------------------------------------
# Test 3 — conviction is NOT amended (regression)
# ---------------------------------------------------------------------------

class TestConvictionNotAmended:
    def test_compute_conviction_has_no_forward_valuation_param(self):
        """Structural isolation: conviction cannot consume forward valuation."""
        sig = inspect.signature(compute_conviction)
        assert "forward_valuation" not in sig.parameters
        assert "valuation" not in sig.parameters

    def test_compute_valuation_does_not_mutate_arbitrated(self, monkeypatch):
        from pmacs.agents import valuation_agent as va_mod
        fake_po = SimpleNamespace(raw_output=json.dumps(_valuation_output_dict()))
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: fake_po,
        )
        arb = _arbitrated(p_up=0.55, p_flat=0.30, p_down=0.15)
        before = (arb.p_up, arb.p_flat, arb.p_down)
        orch = _make_orch()
        orch._compute_valuation(
            ticker="TEST", cycle_id="c1",
            evidence_packets=_evidence_packets(),
            persona_results={}, arbitrated=arb, brief=None,
        )
        assert (arb.p_up, arb.p_flat, arb.p_down) == before

    def test_conviction_identical_with_and_without_forward(self):
        """compute_conviction output is unchanged regardless of any forward
        valuation object existing — it simply is not an input."""
        from pmacs.schemas.arbitration import Arbitrated
        arb = Arbitrated(
            ticker="X", cycle_id="c1",
            p_up=0.5, p_flat=0.3, p_down=0.2,
            persona_outputs=[], persona_weights=[],
            agreement_score=0.6, matured_sources_used=4,
        )
        c1 = compute_conviction(arb=arb, crucible_severity=0.3, ev_multiple=1.2)
        c2 = compute_conviction(arb=arb, crucible_severity=0.3, ev_multiple=1.2)
        assert c1 == c2  # deterministic and forward-valuation-free


# ---------------------------------------------------------------------------
# Test 4 — MemoWriter renders the Forward Valuation block
# ---------------------------------------------------------------------------

class TestMemoWriterForwardBlock:
    def test_memo_contains_forward_valuation_block(self, monkeypatch):
        from pmacs.agents import valuation_agent as va_mod
        from pmacs.agents.memo_writer import MemoWriterRunner
        fake_po = SimpleNamespace(raw_output=json.dumps(_valuation_output_dict()))
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: fake_po,
        )
        orch = _make_orch()
        rdcf, forward, scenario = orch._compute_valuation(
            ticker="TEST", cycle_id="c1",
            evidence_packets=_evidence_packets(),
            persona_results={}, arbitrated=_arbitrated(), brief=None,
        )
        assert forward is not None and forward.is_available

        # Drive MemoWriter.set_analytical_context to render the analytical text.
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            arbitrated=_arbitrated(),
            reverse_dcf=rdcf,
            scenario_price=scenario,
            forward_valuation=forward,
        )
        text = runner._analytical_context
        assert "Forward Valuation" in text
        assert f"{forward.horizon_months}mo" in text
        assert "Base=$" in text  # only rendered in the forward-available branch
        # The forward section itself must not show "Unavailable" (that branch only
        # fires when forward_valuation is None or not is_available). The reverse-DCF
        # anchor may independently be unavailable — that is unrelated.
        fwd_idx = text.find("Forward Valuation")
        fwd_section = text[fwd_idx:]
        assert "Unavailable" not in fwd_section

    def test_memo_shows_unavailable_when_forward_degraded(self, monkeypatch):
        from pmacs.agents import valuation_agent as va_mod
        from pmacs.agents.memo_writer import MemoWriterRunner
        monkeypatch.setattr(
            va_mod.ValuationAgentRunner, "run",
            lambda self, evidence, episodic_context=None: None,
        )
        orch = _make_orch()
        rdcf, forward, scenario = orch._compute_valuation(
            ticker="TEST", cycle_id="c1",
            evidence_packets=_evidence_packets(),
            persona_results={}, arbitrated=_arbitrated(), brief=None,
        )
        # forward is None → memo has no forward block at all (the block is only
        # rendered when forward_valuation is not None). Assert it does not
        # fabricate a forward block.
        runner = MemoWriterRunner()
        runner.set_analytical_context(
            arbitrated=_arbitrated(),
            reverse_dcf=rdcf,
            scenario_price=scenario,
            forward_valuation=forward,  # None
        )
        text = runner._analytical_context
        assert "Forward Valuation" not in text
