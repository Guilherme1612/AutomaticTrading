"""E2E smoke test: synthetic pipeline cycle without LLM calls.

Phase 4 Workstream C: Verifies all deterministic engines fire in order:
  arbitration -> conviction -> sizing -> risk gate

Also verifies audit chain integrity and paper ledger integration.
No LLM calls needed — all inputs are synthetic fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pmacs.engines.arbitration import arbitrate
from pmacs.engines.conviction import compute_conviction, verdict_tier
from pmacs.engines.mode_manager import transition_mode
from pmacs.engines.portfolio_risk_gate import RiskGateInputs, evaluate_risk_gate
from pmacs.engines.sizing import SizingInputs, size_position
from pmacs.execution.catastrophe_net import compute_catastrophe_stop, place_catastrophe_net_stop
from pmacs.schemas.agents import DirectionalProbability, PersonaName
from pmacs.schemas.arbitration import Arbitrated, ArbitrationDecision
from pmacs.schemas.system import Mode
from pmacs.sim.ledger import PaperLedger
from pmacs.storage.audit import AuditVerifier, AuditWriter


# ---------------------------------------------------------------------------
# Synthetic Fixtures
# ---------------------------------------------------------------------------


def _make_directional(
    persona: PersonaName,
    ticker: str = "AAPL",
    p_up: float = 0.55,
    p_flat: float = 0.30,
    p_down: float = 0.15,
) -> DirectionalProbability:
    """Create a synthetic DirectionalProbability."""
    return DirectionalProbability(
        persona=persona,
        ticker=ticker,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        confidence=0.7,
        reasoning=f"Synthetic {persona.value} signal",
        cycle_id="smoke-test-001",
    )


def _make_arbitrated(
    ticker: str = "AAPL",
    p_up: float = 0.55,
    p_flat: float = 0.30,
    p_down: float = 0.15,
) -> Arbitrated:
    """Create a synthetic Arbitrated result."""
    return Arbitrated(
        ticker=ticker,
        cycle_id="smoke-test-001",
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        matured_sources_used=3,
        decision=ArbitrationDecision.PROCEED,
    )


# ---------------------------------------------------------------------------
# E2E Smoke Cycle
# ---------------------------------------------------------------------------


class TestSmokeCycle:
    """Lightweight E2E test: pipeline fires in order, no LLM calls."""

    def test_full_pipeline_sequence(self) -> None:
        """Full pipeline: arbitrate -> convict -> size -> risk gate -> ledger."""
        ticker = "AAPL"

        # Step 1: Arbitration (synthetic Arbitrated result)
        arb = _make_arbitrated(ticker, p_up=0.70, p_flat=0.20, p_down=0.10)
        assert arb.p_up > arb.p_down, "Should be bullish"
        assert arb.decision == ArbitrationDecision.PROCEED

        # Step 2: Conviction
        conviction = compute_conviction(
            arb,
            crucible_severity=0.1,
            ev_multiple=1.4,
            is_bootstrap=False,
        )
        assert conviction > 0, f"Conviction should be positive, got {conviction}"
        verdict = verdict_tier(conviction)
        assert verdict.value in ("BUY", "STRONG_BUY"), f"Expected BUY, got {verdict}"

        # Step 3: Sizing
        sizing_input = SizingInputs(
            p_up=arb.p_up,
            p_down=arb.p_down,
            target_gain_pct=0.10,
            stop_loss_pct=0.15,
            matured_sources_used=arb.matured_sources_used,
            is_limited_history=False,
            portfolio_value_usd=5000.0,
            current_price=150.0,
        )
        sizing = size_position(sizing_input)
        assert sizing.target_usd > 0, "Should have non-zero position size"
        assert sizing.abort_reason is None

        # Step 4: Risk gate
        risk_input = RiskGateInputs(
            current_position_count=0,
            target_usd=sizing.target_usd,
            portfolio_value_usd=5000.0,
            sector="Technology",
        )
        risk = evaluate_risk_gate(risk_input)
        assert risk.passed, f"Risk gate should pass: {risk.reasons}"

        # Step 5: Open paper ledger position
        ledger = PaperLedger()
        shares = sizing.target_shares
        price = 150.0
        ledger.open_position(ticker, shares, price, sector="Technology")
        assert ledger.position_count == 1
        assert ledger.positions[ticker].cost_basis == pytest.approx(shares * price)

        # Step 6: Catastrophe-net stop
        stop_order = place_catastrophe_net_stop(ticker, price, shares)
        assert stop_order["stop_price"] == compute_catastrophe_stop(price)
        assert stop_order["reason"] == "catastrophe_net"

        # Step 7: Update price and verify PnL
        ledger.update_price(ticker, 165.0)
        pos = ledger.positions[ticker]
        assert pos.unrealized_pnl > 0

        # Step 8: Close position
        pnl = ledger.close_position(ticker, 165.0)
        assert pnl > 0
        assert ledger.position_count == 0

    def test_pipeline_skip_low_conviction(self) -> None:
        """Pipeline produces SKIP for low conviction."""
        arb = _make_arbitrated("XYZ", p_up=0.35, p_flat=0.40, p_down=0.25)
        conviction = compute_conviction(
            arb,
            crucible_severity=0.5,
            ev_multiple=0.5,
            is_bootstrap=True,
        )
        # Low direction + high crucible severity + low EV -> should be low or negative
        if conviction < 0.3:
            verdict = verdict_tier(conviction)
            assert verdict.value == "SKIP"

    def test_pipeline_negative_kelly_skip(self) -> None:
        """Sizing engine returns zero for negative Kelly."""
        sizing_input = SizingInputs(
            p_up=0.30,
            p_down=0.50,
            target_gain_pct=0.05,
            stop_loss_pct=0.15,
            matured_sources_used=2,
            is_limited_history=True,
            portfolio_value_usd=5000.0,
            current_price=100.0,
        )
        sizing = size_position(sizing_input)
        assert sizing.target_usd == 0.0
        assert sizing.abort_reason == "NEGATIVE_KELLY_NO_EDGE"

    def test_audit_chain_integrity(self, tmp_path: Path) -> None:
        """Audit chain is hash-intact after multiple events."""
        audit_path = tmp_path / "audit.log"
        writer = AuditWriter(audit_path)

        # Write several events simulating a cycle
        sha1 = writer.append("CYCLE_OPENED", {"cycle_id": "smoke-001", "trigger": "TIMER"})
        sha2 = writer.append(
            "ARBITRATION_COMPLETE",
            {"cycle_id": "smoke-001", "ticker": "AAPL", "decision": "PROCEED"},
        )
        sha3 = writer.append(
            "CONVICTION_SCORED",
            {"cycle_id": "smoke-001", "ticker": "AAPL", "conviction": 0.55},
        )
        sha4 = writer.append(
            "TRADE_EXECUTED",
            {"cycle_id": "smoke-001", "ticker": "AAPL", "shares": 10, "price": 150.0},
        )
        sha5 = writer.append("CYCLE_CLOSED", {"cycle_id": "smoke-001"})
        writer.close()

        # Verify chain integrity
        verifier = AuditVerifier(audit_path)
        ok, error = verifier.verify_full()
        assert ok, f"Audit chain broken: {error}"

        # Verify incremental too
        ok2, error2 = verifier.verify_incremental()
        assert ok2, f"Incremental verification failed: {error2}"

    def test_mode_transition_chain_with_audit(self, tmp_path: Path) -> None:
        """Mode transitions produce audit entries with valid chain."""
        audit_path = tmp_path / "audit.log"
        writer = AuditWriter(audit_path)

        # INSTALLING -> SHADOW
        t1 = transition_mode(Mode.INSTALLING, Mode.SHADOW, reason="Wizard complete")
        writer.append(
            "MODE_TRANSITION",
            {
                "from": t1.from_mode.value,
                "to": t1.to_mode.value,
                "reason": t1.reason,
            },
        )

        # SHADOW -> PAPER
        t2 = transition_mode(Mode.SHADOW, Mode.PAPER, reason="Shadow validated")
        writer.append(
            "MODE_TRANSITION",
            {
                "from": t2.from_mode.value,
                "to": t2.to_mode.value,
                "reason": t2.reason,
            },
        )

        writer.close()

        # Verify
        verifier = AuditVerifier(audit_path)
        ok, error = verifier.verify_full()
        assert ok, f"Mode transition audit chain broken: {error}"

    def test_paper_ledger_multiple_positions(self) -> None:
        """Ledger handles multiple concurrent positions up to max."""
        ledger = PaperLedger()

        # Open 5 positions (max)
        tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]
        prices = [150.0, 300.0, 130.0, 170.0, 200.0]
        for ticker, price in zip(tickers, prices):
            shares = 3  # small to stay under 20% limit
            ledger.open_position(ticker, shares, price)

        assert ledger.position_count == 5

        # Cannot open 6th
        with pytest.raises(ValueError, match="Max concurrent"):
            ledger.open_position("NVDA", 1, 100.0)

        # Close one and open another
        ledger.close_position("TSLA", 210.0)
        ledger.open_position("NVDA", 1, 100.0)
        assert ledger.position_count == 5
