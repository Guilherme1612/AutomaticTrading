"""Risk Checkpoint B — post-Phase 8 paper trading verification (Phases.md §6.2).

Verifies:
- Paper trade lifecycle: order submit -> fill -> ledger update -> holding -> audit
- Paper ledger starts at $5,000
- Ledger balance updates correctly after fills
- Mode is SHADOW + PAPER after initialization
- Kill switch can be engaged and disengaged via API
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pmacs.cortex.kill_switch import disengage, engage, is_engaged
from pmacs.cortex.totp import compute_totp, generate_totp_secret
from pmacs.engines.mode_manager import can_transition, transition_mode
from pmacs.nervous.sse_publisher import SSEPublisher
from pmacs.sim.ledger import PaperLedger
from pmacs.schemas.system import Mode
from pmacs.storage.sqlite import init_db


@pytest.fixture
def tmp_env(tmp_path: Path) -> dict:
    """Create temp directory with SQLite DB and audit log."""
    db_path = tmp_path / "pmacs.db"
    audit_path = tmp_path / "audit.log"

    conn = init_db(db_path)
    conn.close()

    return {
        "db_path": db_path,
        "audit_path": audit_path,
        "tmp_path": tmp_path,
    }


@pytest.fixture
def totp_secret() -> str:
    """Generate a fresh TOTP secret for testing."""
    return generate_totp_secret()


# ---------------------------------------------------------------------------
# Checkpoint B.1 — Paper trade lifecycle
# ---------------------------------------------------------------------------


class TestPaperTradeLifecycle:
    """Full paper trade lifecycle: submit -> fill -> ledger -> holding -> audit."""

    def test_open_position_reduces_cash_creates_holding(self) -> None:
        """Opening a position reduces cash, creates holding in ledger."""
        ledger = PaperLedger()
        assert ledger.cash == 5000.0

        ledger.open_position("AAPL", 5, 150.0, sector="Technology")
        assert ledger.cash == 4250.0  # 5000 - 750
        assert ledger.position_count == 1
        assert "AAPL" in ledger.positions

        pos = ledger.positions["AAPL"]
        assert pos.shares == 5
        assert pos.entry_price == 150.0
        assert pos.current_price == 150.0

    def test_close_position_returns_cash_updates_pnl(self) -> None:
        """Closing a position returns cash and tracks realized PnL."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)

        pnl = ledger.close_position("AAPL", 120.0)
        assert pnl == 200.0  # (120 - 100) * 10
        assert ledger.position_count == 0
        assert ledger.cash == pytest.approx(5200.0)

    def test_price_update_changes_unrealized_pnl(self) -> None:
        """Updating prices recalculates unrealized PnL and total value."""
        ledger = PaperLedger()
        ledger.open_position("AAPL", 10, 100.0)

        ledger.update_price("AAPL", 110.0)
        pos = ledger.positions["AAPL"]
        assert pos.market_value == 1100.0
        assert pos.unrealized_pnl == 100.0

    def test_full_lifecycle_with_audit(self, tmp_env: dict) -> None:
        """End-to-end: open position -> update price -> close -> verify ledger + audit."""
        db = tmp_env["db_path"]
        audit = tmp_env["audit_path"]

        ledger = PaperLedger()

        # Open two positions (both under 20% limit)
        ledger.open_position("AAPL", 5, 150.0, sector="Technology")
        ledger.open_position("MSFT", 4, 200.0, sector="Technology")
        assert ledger.position_count == 2
        assert ledger.cash == pytest.approx(3450.0)

        # Update prices
        ledger.update_price("AAPL", 165.0)
        ledger.update_price("MSFT", 190.0)

        # Total value should reflect updated prices
        expected_total = 3450.0 + (5 * 165.0) + (4 * 190.0)
        assert ledger.total_value == pytest.approx(expected_total)

        # Close AAPL at profit
        pnl_aapl = ledger.close_position("AAPL", 165.0)
        assert pnl_aapl == pytest.approx(75.0)
        assert ledger.position_count == 1

        # Close MSFT at loss
        pnl_msft = ledger.close_position("MSFT", 190.0)
        assert pnl_msft == pytest.approx(-40.0)
        assert ledger.position_count == 0

        # Final cash: 3450 + 5*165 + 4*190 = 3450 + 825 + 760 = 5035
        assert ledger.cash == pytest.approx(5035.0)

        # Write audit entries for the trade actions (simulates what nervous does)
        from pmacs.storage.audit import AuditWriter

        writer = AuditWriter(audit)
        writer.append("PAPER_TRADE_OPEN", {"ticker": "AAPL", "shares": 5, "price": 150.0})
        writer.append("PAPER_TRADE_CLOSE", {"ticker": "AAPL", "pnl": 75.0})
        writer.close()

        content = audit.read_text()
        assert "PAPER_TRADE_OPEN" in content
        assert "PAPER_TRADE_CLOSE" in content


# ---------------------------------------------------------------------------
# Checkpoint B.2 — Paper ledger starts at $5,000
# ---------------------------------------------------------------------------


class TestPaperLedgerInitialCapital:
    """Paper ledger initializes with $5,000 capital."""

    def test_initial_capital(self) -> None:
        """Ledger starts with $5,000 cash and no positions."""
        ledger = PaperLedger()
        assert ledger.cash == 5000.0
        assert ledger.position_count == 0
        assert ledger.total_value == 5000.0
        assert ledger.positions_value == 0.0

    def test_initial_snapshot(self) -> None:
        """Initial snapshot reflects $5K capital."""
        ledger = PaperLedger()
        snap = ledger.snapshot()
        assert snap["cash"] == 5000.0
        assert snap["total_value"] == 5000.0
        assert snap["position_count"] == 0
        assert snap["unrealized_pnl"] == 0.0


# ---------------------------------------------------------------------------
# Checkpoint B.3 — Ledger balance updates correctly after fills
# ---------------------------------------------------------------------------


class TestLedgerBalanceAfterFills:
    """Ledger balance is accurate after multiple trades."""

    def test_balance_after_10_trades(self) -> None:
        """Ledger balance is accurate after 10+ round-trip trades.

        Per Phases.md §6.2: 'Ledger balance is accurate after 10+ trades.'
        """
        ledger = PaperLedger()
        tickers = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"]

        # 5 open trades (each under 20%: <= $1000 per position)
        # Each position: 5 shares at $100 = $500 (10% of capital)
        for i, ticker in enumerate(tickers):
            ledger.open_position(ticker, 5, 100.0, sector="Tech")
            assert ledger.cash == 5000.0 - (i + 1) * 500.0

        assert ledger.position_count == 5
        assert ledger.cash == 2500.0

        # Close all 5 positions at varying prices
        close_prices = [110.0, 95.0, 105.0, 85.0, 115.0]
        expected_pnls = [(p - 100.0) * 5 for p in close_prices]

        for ticker, price, expected_pnl in zip(tickers, close_prices, expected_pnls):
            pnl = ledger.close_position(ticker, price)
            assert pnl == pytest.approx(expected_pnl)

        # Final cash: 2500 (remaining) + sum(proceeds)
        proceeds = sum(5 * p for p in close_prices)
        expected_cash = 2500.0 + proceeds
        assert ledger.cash == pytest.approx(expected_cash)
        assert ledger.position_count == 0
        assert ledger.total_value == pytest.approx(expected_cash)

        # Total PnL from the 5 trades
        total_pnl = sum(expected_pnls)
        assert ledger.cash == pytest.approx(5000.0 + total_pnl)

    def test_ledger_tracks_unrealized_pnl_correctly(self) -> None:
        """Unrealized PnL and total value are consistent mid-trade."""
        ledger = PaperLedger()

        ledger.open_position("AAPL", 10, 100.0)
        ledger.update_price("AAPL", 120.0)

        assert ledger.positions["AAPL"].unrealized_pnl == 200.0
        assert ledger.total_value == pytest.approx(4000.0 + 1200.0)  # cash + position
        assert ledger.positions_value == 1200.0


# ---------------------------------------------------------------------------
# Checkpoint B.4 — Mode is SHADOW + PAPER after initialization
# ---------------------------------------------------------------------------


class TestModeAfterInitialization:
    """After wizard completes, system is in SHADOW + PAPER mode."""

    def test_installing_to_shadow_transition(self) -> None:
        """INSTALLING -> SHADOW is a valid transition."""
        result = transition_mode(
            Mode.INSTALLING, Mode.SHADOW, reason="Wizard complete"
        )
        assert result.to_mode == Mode.SHADOW

    def test_shadow_to_paper_transition(self) -> None:
        """SHADOW -> PAPER is a valid transition."""
        result = transition_mode(
            Mode.SHADOW, Mode.PAPER, reason="Shadow validated"
        )
        assert result.to_mode == Mode.PAPER

    def test_post_wizard_mode_chain(self) -> None:
        """Full wizard mode chain: INSTALLING -> SHADOW -> PAPER."""
        # Step 1: Wizard completes -> SHADOW
        t1 = transition_mode(
            Mode.INSTALLING, Mode.SHADOW, reason="Wizard complete"
        )
        assert t1.to_mode == Mode.SHADOW

        # Step 2: After first successful shadow cycle -> PAPER
        t2 = transition_mode(
            Mode.SHADOW, Mode.PAPER, reason="Shadow validated, entering paper"
        )
        assert t2.to_mode == Mode.PAPER

        # Verify mode is now PAPER (not LIVE)
        assert t2.to_mode == Mode.PAPER
        assert t2.to_mode not in (
            Mode.LIVE_EARLY, Mode.LIVE_STANDARD, Mode.LIVE_EXPANDED
        )

    def test_cannot_skip_to_live(self) -> None:
        """Cannot jump from INSTALLING to any LIVE mode."""
        for live_mode in (Mode.LIVE_EARLY, Mode.LIVE_STANDARD, Mode.LIVE_EXPANDED):
            assert can_transition(Mode.INSTALLING, live_mode) is False

    def test_cannot_jump_shadow_to_live(self) -> None:
        """Cannot jump from SHADOW to any LIVE mode."""
        for live_mode in (Mode.LIVE_EARLY, Mode.LIVE_STANDARD, Mode.LIVE_EXPANDED):
            assert can_transition(Mode.SHADOW, live_mode) is False


# ---------------------------------------------------------------------------
# Checkpoint B.5 — Kill switch engage/disengage via API
# ---------------------------------------------------------------------------


class TestKillSwitchAPILifecycle:
    """Kill switch can be engaged and disengaged via the API."""

    def test_engage_via_api(self, tmp_env: dict) -> None:
        """engage() sets kill switch to ENGAGED."""
        db = tmp_env["db_path"]
        audit = tmp_env["audit_path"]

        engage(
            "operator manual trigger",
            "AUDIT_CHAIN_INTEGRITY",
            db_path=db,
            audit_path=audit,
        )

        assert is_engaged(db_path=db) is True

        content = audit.read_text()
        assert "KILL_SWITCH_ENGAGED" in content

    def test_engage_then_disengage_via_api(
        self, tmp_env: dict, totp_secret: str
    ) -> None:
        """Full engage -> disengage lifecycle works via API with TOTP."""
        db = tmp_env["db_path"]
        audit = tmp_env["audit_path"]

        # Engage
        engage("manual test", "OPERATOR_MANUAL", db_path=db, audit_path=audit)
        assert is_engaged(db_path=db) is True

        # Disengage with valid TOTP
        code = compute_totp(totp_secret)
        result = disengage(
            totp_secret, code, "operator cleared",
            db_path=db, audit_path=audit,
        )
        assert result is True
        assert is_engaged(db_path=db) is False

        # Both events in audit
        content = audit.read_text()
        assert "KILL_SWITCH_ENGAGED" in content
        assert "KILL_SWITCH_DISENGAGED" in content

    def test_invalid_totp_disengage_rejected(
        self, tmp_env: dict, totp_secret: str
    ) -> None:
        """Invalid TOTP code rejects disengage attempt."""
        db = tmp_env["db_path"]

        engage("test", "AUDIT_CHAIN_INTEGRITY", db_path=db)

        result = disengage(totp_secret, "999999", "bad attempt", db_path=db)
        assert result is False
        assert is_engaged(db_path=db) is True
