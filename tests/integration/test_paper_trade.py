"""Integration tests for paper trading pipeline — ledger, mode management, catastrophe stops, wizard.

Phase 4 Workstream C: Paper Trading + Sim Ledger + Wizard + Mode Management.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pmacs.engines.mode_manager import can_transition, transition_mode
from pmacs.execution.catastrophe_net import (
    compute_catastrophe_stop,
    place_catastrophe_net_stop,
    validate_stop_order,
)
from pmacs.installer.steps.check_system import run as check_system_step
from pmacs.installer.steps.configure_broker import run as configure_broker_step
from pmacs.installer.steps.configure_data import run as configure_data_step
from pmacs.installer.steps.configure_llm import run as configure_llm_step
from pmacs.installer.steps.create_dirs import run as create_dirs_step
from pmacs.installer.steps.generate_keys import run as generate_keys_step
from pmacs.installer.steps.smoke_test import run as smoke_test_step
from pmacs.installer.wizard import Wizard, WizardStep
from pmacs.sim.ledger import PaperLedger
from pmacs.schemas.system import Mode


# ---------------------------------------------------------------------------
# Paper Ledger Tests
# ---------------------------------------------------------------------------


class TestPaperLedger:
    """Test paper ledger lifecycle: open -> update -> close -> verify PnL."""

    def test_initial_capital(self) -> None:
        """Ledger starts with $5,000 cash and no positions."""
        ledger = PaperLedger()
        assert ledger.cash == 5000.0
        assert ledger.position_count == 0
        assert ledger.total_value == 5000.0

    def test_open_position(self) -> None:
        """Opening a position reduces cash and creates the position."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 5, 150.0, sector="Technology")
        assert ledger.cash == 5000.0 - 750.0
        assert ledger.position_count == 1
        assert "AAPL" in ledger.positions
        pos = ledger.positions["AAPL"]
        assert pos.shares == 5
        assert pos.entry_price == 150.0
        assert pos.current_price == 150.0
        assert pos.sector == "Technology"

    def test_open_position_insufficient_cash(self) -> None:
        """Opening a position that exceeds cash raises ValueError."""
        ledger = PaperLedger()
        with pytest.raises(ValueError, match="Insufficient cash"):
            ledger.open_position("AAPL", 100, 100.0)  # $10,000 > $5,000

    def test_open_position_max_single_size(self) -> None:
        """Opening a position > 20% of capital raises ValueError."""
        ledger = PaperLedger()
        with pytest.raises(ValueError, match="max single position"):
            # $1,001 > $1,000 (20% of $5,000)
            ledger.open_position("AAPL", 1, 1001.0)

    def test_open_position_duplicate_ticker(self) -> None:
        """Opening a duplicate position raises ValueError."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 5, 100.0)
        with pytest.raises(ValueError, match="already exists"):
            ledger.open_position("AAPL", 5, 100.0)

    def test_update_price(self) -> None:
        """Updating price changes market value and unrealized PnL."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)
        ledger.update_price("AAPL", 110.0)
        pos = ledger.positions["AAPL"]
        assert pos.market_value == 1100.0
        assert pos.unrealized_pnl == 100.0
        assert pos.unrealized_pnl_pct == pytest.approx(0.10)

    def test_update_price_nonexistent(self) -> None:
        """Updating price for non-existent ticker raises ValueError."""
        ledger = PaperLedger()
        with pytest.raises(ValueError, match="No position"):
            ledger.update_price("AAPL", 110.0)

    def test_close_position_profit(self) -> None:
        """Closing a position at profit returns positive realized PnL."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)
        pnl = ledger.close_position("AAPL", 120.0)
        assert pnl == 200.0  # (120 - 100) * 10
        assert ledger.cash == 5000.0 + 1200.0 - 1000.0  # initial - cost + proceeds
        assert ledger.position_count == 0

    def test_close_position_loss(self) -> None:
        """Closing a position at loss returns negative realized PnL."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)
        pnl = ledger.close_position("AAPL", 80.0)
        assert pnl == -200.0  # (80 - 100) * 10

    def test_close_position_nonexistent(self) -> None:
        """Closing a non-existent position raises ValueError."""
        ledger = PaperLedger()
        with pytest.raises(ValueError, match="No position"):
            ledger.close_position("AAPL", 100.0)

    def test_full_lifecycle(self) -> None:
        """Full lifecycle: open -> update -> close with PnL verification."""
        ledger = PaperLedger()

        # Open AAPL (5 * $150 = $750, under 20% limit of $1,000)
        ledger.open_position("AAPL", 5, 150.0, sector="Technology")
        assert ledger.cash == 4250.0
        assert ledger.total_value == pytest.approx(5000.0)

        # Open MSFT (4 * $200 = $800, under 20% limit)
        ledger.open_position("MSFT", 4, 200.0, sector="Technology")
        assert ledger.cash == 3450.0
        assert ledger.position_count == 2

        # Update prices
        ledger.update_price("AAPL", 165.0)
        ledger.update_price("MSFT", 210.0)
        assert ledger.total_value == pytest.approx(
            3450.0 + 825.0 + 840.0  # cash + AAPL + MSFT
        )

        # Close AAPL at profit
        pnl = ledger.close_position("AAPL", 165.0)
        assert pnl == pytest.approx(75.0)
        assert ledger.position_count == 1

        # Close MSFT at profit
        pnl2 = ledger.close_position("MSFT", 210.0)
        assert pnl2 == pytest.approx(40.0)
        assert ledger.position_count == 0
        assert ledger.cash == pytest.approx(5115.0)

    def test_snapshot(self) -> None:
        """Snapshot returns correct summary."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)
        ledger.update_price("AAPL", 110.0)
        snap = ledger.snapshot()
        assert snap["cash"] == 4000.0
        assert snap["positions_value"] == 1100.0
        assert snap["total_value"] == 5100.0
        assert snap["position_count"] == 1
        assert snap["unrealized_pnl"] == 100.0

    def test_update_prices_bulk(self) -> None:
        """Bulk update prices for multiple positions."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)
        ledger.open_position("MSFT", 5, 200.0)
        ledger.update_prices({"AAPL": 110.0, "MSFT": 220.0, "GOOGL": 999.0})
        assert ledger.positions["AAPL"].current_price == 110.0
        assert ledger.positions["MSFT"].current_price == 220.0
        assert "GOOGL" not in ledger.positions  # ignored

    def test_catastrophe_net_stop_default(self) -> None:
        """Opening a position without stop_price sets catastrophe-net default."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)
        assert ledger.positions["AAPL"].stop_price == 85.0  # 100 * (1 - 0.15)

    def test_catastrophe_net_stop_custom(self) -> None:
        """Opening a position with custom stop_price preserves it."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0, stop_price=90.0)
        assert ledger.positions["AAPL"].stop_price == 90.0


# ---------------------------------------------------------------------------
# Mode Transition Tests
# ---------------------------------------------------------------------------


class TestModeTransitions:
    """Test mode transition chain: INSTALLING -> SHADOW -> PAPER."""

    def test_full_ladder_promotion(self) -> None:
        """Valid promotion chain: INSTALLING -> SHADOW -> PAPER."""
        # INSTALLING -> SHADOW
        t1 = transition_mode(
            Mode.INSTALLING, Mode.SHADOW, reason="Wizard complete"
        )
        assert t1.to_mode == Mode.SHADOW

        # SHADOW -> PAPER
        t2 = transition_mode(
            Mode.SHADOW, Mode.PAPER, reason="Shadow validated"
        )
        assert t2.to_mode == Mode.PAPER

    def test_invalid_jump_raises(self) -> None:
        """Invalid jump from INSTALLING to LIVE raises."""
        with pytest.raises(ValueError, match="Invalid mode transition"):
            transition_mode(
                Mode.INSTALLING, Mode.LIVE_EARLY, reason="skip steps"
            )

    def test_demotion_paper_to_shadow(self) -> None:
        """Demotion from PAPER to SHADOW works without TOTP."""
        result = transition_mode(
            Mode.PAPER, Mode.SHADOW, reason="poor performance"
        )
        assert result.to_mode == Mode.SHADOW

    def test_live_promotion_requires_totp(self) -> None:
        """PAPER_VALIDATED -> LIVE_EARLY requires TOTP."""
        with pytest.raises(ValueError, match="TOTP"):
            transition_mode(
                Mode.PAPER_VALIDATED,
                Mode.LIVE_EARLY,
                reason="ready for live",
            )

    def test_live_promotion_with_totp(self) -> None:
        """PAPER_VALIDATED -> LIVE_EARLY succeeds with TOTP."""
        result = transition_mode(
            Mode.PAPER_VALIDATED,
            Mode.LIVE_EARLY,
            reason="ready for live",
            totp_verified=True,
        )
        assert result.to_mode == Mode.LIVE_EARLY
        assert result.operator_totp_verified is True


# ---------------------------------------------------------------------------
# Catastrophe-net Stop Tests
# ---------------------------------------------------------------------------


class TestCatastropheNetStop:
    """Test catastrophe-net stop computation and order generation."""

    def test_stop_price_15pct_below(self) -> None:
        """Stop price is exactly 15% below entry."""
        stop = compute_catastrophe_stop(100.0)
        assert stop == 85.0

    def test_stop_price_rounded(self) -> None:
        """Stop price is rounded to 2 decimal places."""
        stop = compute_catastrophe_stop(33.33)
        expected = round(33.33 * 0.85, 2)
        assert stop == expected

    def test_stop_price_high_entry(self) -> None:
        """Stop price for high-value entries."""
        stop = compute_catastrophe_stop(500.0)
        assert stop == 425.0

    def test_invalid_entry_price(self) -> None:
        """Zero or negative entry price raises ValueError."""
        with pytest.raises(ValueError, match="positive"):
            compute_catastrophe_stop(0.0)
        with pytest.raises(ValueError, match="positive"):
            compute_catastrophe_stop(-10.0)

    def test_place_stop_order_structure(self) -> None:
        """place_catastrophe_net_stop returns valid order dict."""
        order = place_catastrophe_net_stop("AAPL", 100.0, 10)
        assert order["ticker"] == "AAPL"
        assert order["side"] == "SELL"
        assert order["type"] == "STOP_MARKET"
        assert order["stop_price"] == 85.0
        assert order["qty"] == 10
        assert order["time_in_force"] == "GTC"
        assert order["reason"] == "catastrophe_net"

    def test_validate_stop_order(self) -> None:
        """validate_stop_order accepts valid orders."""
        order = place_catastrophe_net_stop("AAPL", 100.0, 10)
        assert validate_stop_order(order) is True

    def test_validate_stop_order_invalid(self) -> None:
        """validate_stop_order rejects malformed orders."""
        assert validate_stop_order({}) is False
        assert validate_stop_order({"side": "BUY", "type": "STOP_MARKET"}) is False


# ---------------------------------------------------------------------------
# Wizard Tests
# ---------------------------------------------------------------------------


class TestWizard:
    """Test wizard step progression."""

    def test_wizard_starts_at_welcome(self) -> None:
        wizard = Wizard()
        assert wizard.get_step() == WizardStep.WELCOME
        assert wizard.is_complete() is False

    def test_wizard_step_progression(self) -> None:
        wizard = Wizard()
        assert wizard.get_step() == WizardStep.WELCOME

        step = wizard.complete_step(WizardStep.WELCOME, {"welcome": True})
        assert step == WizardStep.INFERENCE_BACKEND
        assert wizard.get_step() == WizardStep.INFERENCE_BACKEND
        assert wizard.config["welcome"] is True

    def test_wizard_full_progression(self) -> None:
        wizard = Wizard()
        steps = [
            WizardStep.WELCOME,
            WizardStep.INFERENCE_BACKEND,
            WizardStep.MODEL_DOWNLOAD,
            WizardStep.KEYCHAIN_SETUP,
            WizardStep.DB_INIT,
            WizardStep.DATA_CONNECTIVITY,
            WizardStep.UNIVERSE_SEED,
            WizardStep.CYCLE_PREFERENCES,
            WizardStep.TOTP_ENROLLMENT,
            WizardStep.SMOKE_TEST,
            WizardStep.PROMOTE,
        ]
        for step in steps:
            wizard.complete_step(step)

        assert wizard.is_complete() is True
        assert wizard.get_step() == WizardStep.COMPLETE

    def test_wizard_progress(self) -> None:
        wizard = Wizard()
        completed, total = wizard.get_progress()
        assert completed == 0
        assert total == 11  # 11 actionable steps (Source.md §12.1)

        wizard.complete_step(WizardStep.WELCOME)
        completed, total = wizard.get_progress()
        assert completed == 1
        assert total == 11

    def test_wizard_cannot_complete_final_step(self) -> None:
        wizard = Wizard()
        with pytest.raises(ValueError, match="Cannot complete"):
            wizard.complete_step(WizardStep.COMPLETE)

    def test_wizard_config_accumulation(self) -> None:
        wizard = Wizard()
        wizard.complete_step(WizardStep.WELCOME, {"step": "welcome"})
        wizard.complete_step(WizardStep.INFERENCE_BACKEND, {"python_ok": True})
        assert wizard.config == {"step": "welcome", "python_ok": True}

    def test_check_system_step(self) -> None:
        """check_system step runs and returns valid results."""
        wizard = Wizard()
        result = check_system_step(wizard)
        assert "checks" in result
        assert "python_version" in result["checks"]

    def test_create_dirs_step(self, tmp_path: Path) -> None:
        """create_dirs step creates directory structure."""
        wizard = Wizard()
        result = create_dirs_step(wizard, base_path=tmp_path / "pmacs")
        assert result["all_ok"] is True
        assert len(result["created"]) > 0

    def test_generate_keys_step(self, tmp_path: Path) -> None:
        """generate_keys step creates keypair files."""
        wizard = Wizard()
        result = generate_keys_step(wizard, key_dir=tmp_path / "keys")
        assert result["all_ok"] is True
        assert result["private_key_path"]

    def test_configure_llm_step(self) -> None:
        """configure_llm step returns config."""
        wizard = Wizard()
        result = configure_llm_step(wizard)
        assert "llm_backend" in result

    def test_configure_data_step(self) -> None:
        """configure_data step returns config."""
        wizard = Wizard()
        result = configure_data_step(wizard, api_keys={"alpha_vantage": "test_key"})
        assert result["all_ok"] is True

    def test_configure_broker_step(self) -> None:
        """configure_broker step returns config."""
        wizard = Wizard()
        result = configure_broker_step(
            wizard, api_key="test_key", api_secret="test_secret"
        )
        assert result["all_ok"] is True
        assert result["paper"] is True

    def test_smoke_test_step(self) -> None:
        """smoke_test step returns results."""
        wizard = Wizard()
        result = smoke_test_step(wizard)
        assert "llm_test" in result
        assert "data_test" in result


# ---------------------------------------------------------------------------
# Full Trade Lifecycle with Adapter Tests
# ---------------------------------------------------------------------------


class TestTradeLifecycleWithAdapter:
    """Full lifecycle: sign TradePlan -> UDS submit -> adapter -> fill -> catastrophe-net -> cancel -> exit.

    Uses MockAdapter (no real API calls).
    """

    @pytest.fixture()
    def keypair(self):
        from pmacs.execution.signing import generate_keypair
        return generate_keypair()

    @pytest.fixture()
    def uds_paths(self):
        import os
        base = Path(f"/tmp/pmacs_lifecycle_{os.getpid()}")
        base.mkdir(parents=True, exist_ok=True)
        yield base / "lifecycle.sock", base / "audit"
        # cleanup
        for f in base.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            base.rmdir()
        except OSError:
            pass

    @pytest.fixture()
    def trade_plan(self):
        from pmacs.schemas.trade import TradeDirection, TradePlan
        return TradePlan(
            id="tp-lifecycle-001",
            ticker="AAPL",
            direction=TradeDirection.BUY,
            quantity=10,
            price_usd=150.0,
            cycle_id="cycle-lifecycle-001",
            holding_id="h-001",
        )

    @pytest.mark.asyncio()
    async def test_full_buy_lifecycle(
        self,
        keypair,
        uds_paths,
        trade_plan,
    ) -> None:
        """Full BUY lifecycle: sign -> submit -> fill -> catastrophe-net stop."""
        from pmacs.execution.adapter import MockAdapter
        from pmacs.execution.service import ExecutionService

        priv, pub = keypair
        sock_path, audit_dir = uds_paths

        adapter = MockAdapter()
        svc = ExecutionService(
            sock_path=sock_path,
            public_key=pub,
            audit_dir=audit_dir,
            adapter=adapter,
        )
        await svc.start()
        try:
            plan_bytes = trade_plan.model_dump_json().encode("utf-8")
            result = await ExecutionService.sign_and_send(sock_path, plan_bytes, priv)

            assert result["status"] == "ACCEPTED"
            fill = result["fill"]
            assert fill["price"] == 150.0
            assert fill["qty"] == 10
            assert fill["ticker"] == "AAPL"
            assert fill["direction"] == "BUY"
            assert fill["broker_order_id"] == "mock-tp-lifecycle-001"

            # Catastrophe-net stop placed
            stop_id = result["stop_order_id"]
            assert stop_id is not None
            assert "stop" in stop_id

            # Verify audit log
            audit_log = audit_dir / "exec_audit.log"
            assert audit_log.exists()
            content = audit_log.read_text()
            assert "EXEC_TRADE_ACCEPTED" in content
            assert "tp-lifecycle-001" in content
        finally:
            await svc.stop()

    @pytest.mark.asyncio()
    async def test_sell_exit_with_cancel(
        self,
        keypair,
        uds_paths,
    ) -> None:
        """Exit SELL: submit, verify fill, cancel stop order."""
        from pmacs.execution.adapter import MockAdapter
        from pmacs.execution.service import ExecutionService
        from pmacs.schemas.trade import TradeDirection, TradePlan

        priv, pub = keypair
        sock_path, audit_dir = uds_paths

        adapter = MockAdapter()
        svc = ExecutionService(
            sock_path=sock_path,
            public_key=pub,
            audit_dir=audit_dir,
            adapter=adapter,
        )
        await svc.start()
        try:
            sell_plan = TradePlan(
                id="tp-sell-001",
                ticker="AAPL",
                direction=TradeDirection.SELL,
                quantity=10,
                price_usd=160.0,
                cycle_id="cycle-exit-001",
            )
            plan_bytes = sell_plan.model_dump_json().encode("utf-8")
            result = await ExecutionService.sign_and_send(sock_path, plan_bytes, priv)

            assert result["status"] == "ACCEPTED"
            assert result["fill"]["direction"] == "SELL"
            assert result["fill"]["price"] == 160.0
            assert result["stop_order_id"] is not None

            # Cancel the stop order
            cancelled = await adapter.cancel_order(result["stop_order_id"])
            assert cancelled is True
        finally:
            await svc.stop()

    @pytest.mark.asyncio()
    async def test_catastrophe_stop_price_is_15pct_below(
        self,
        keypair,
        uds_paths,
    ) -> None:
        """Verify catastrophe-net stop is placed at 15% below entry."""
        from pmacs.execution.adapter import MockAdapter
        from pmacs.execution.service import ExecutionService
        from pmacs.schemas.trade import TradeDirection, TradePlan

        priv, pub = keypair
        sock_path, audit_dir = uds_paths

        adapter = MockAdapter()
        svc = ExecutionService(
            sock_path=sock_path,
            public_key=pub,
            audit_dir=audit_dir,
            adapter=adapter,
        )
        await svc.start()
        try:
            # Entry at $200, stop should be at $170 (15% below)
            plan = TradePlan(
                id="tp-cat-001",
                ticker="MSFT",
                direction=TradeDirection.BUY,
                quantity=5,
                price_usd=200.0,
                cycle_id="cycle-cat-001",
            )
            plan_bytes = plan.model_dump_json().encode("utf-8")
            result = await ExecutionService.sign_and_send(sock_path, plan_bytes, priv)

            assert result["status"] == "ACCEPTED"
            stop_id = result["stop_order_id"]
            # MockAdapter embeds stop_price in the ID: "mock-stop-{ticker}-{stop_price}"
            assert "170.0" in stop_id
        finally:
            await svc.stop()

    @pytest.mark.asyncio()
    async def test_rejected_signature_returns_no_fill(
        self,
        keypair,
        uds_paths,
    ) -> None:
        """Invalid signature returns REJECTED with no fill or stop."""
        from pmacs.execution.adapter import MockAdapter
        from pmacs.execution.service import ExecutionService
        from pmacs.execution.signing import generate_keypair as gen_kp
        from pmacs.schemas.trade import TradeDirection, TradePlan

        priv_server, pub_server = keypair
        sock_path, audit_dir = uds_paths

        svc = ExecutionService(
            sock_path=sock_path,
            public_key=pub_server,
            audit_dir=audit_dir,
            adapter=MockAdapter(),
        )
        await svc.start()
        try:
            # Sign with wrong key
            priv_wrong, _ = gen_kp()
            plan = TradePlan(
                id="tp-reject-001",
                ticker="AAPL",
                direction=TradeDirection.BUY,
                quantity=10,
                price_usd=150.0,
            )
            plan_bytes = plan.model_dump_json().encode("utf-8")
            result = await ExecutionService.sign_and_send(sock_path, plan_bytes, priv_wrong)

            assert result["status"] == "REJECTED"
            assert result["reason"] == "INVALID_SIGNATURE"
            assert "fill" not in result
            assert "stop_order_id" not in result
        finally:
            await svc.stop()

    @pytest.mark.asyncio()
    async def test_mock_adapter_get_position_returns_none(
        self,
    ) -> None:
        """MockAdapter.get_position returns None (no real positions)."""
        from pmacs.execution.adapter import MockAdapter
        adapter = MockAdapter()
        result = await adapter.get_position("AAPL")
        assert result is None
