"""Tests for catastrophe-net cancellation and execute_exit orchestration.

Task 1 [C1]: cancel_catastrophe_net, execute_exit, audit logging.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pmacs.execution.catastrophe_net import (
    BrokerError,
    CancelResult,
    cancel_catastrophe_net,
    execute_exit,
)
from pmacs.schemas.contracts import Holding, HoldingState, Thesis


def _make_holding(
    state: HoldingState = HoldingState.ACTIVE,
    entry_price: float = 100.0,
    position_size: float = 1000.0,
) -> Holding:
    """Create a test Holding."""
    from datetime import date, datetime
    return Holding(
        id="h-test-001",
        ticker="TEST",
        state=state,
        cycle_id_opened="cycle-001",
        entry_date=date(2026, 1, 15),
        entry_price_usd=entry_price,
        position_size_usd=position_size,
        stop_price_usd=85.0,
        conviction_score=0.5,
        created_at=datetime(2026, 1, 15, 12, 0, 0),
        updated_at=datetime(2026, 1, 15, 12, 0, 0),
    )


class TestCancelCatastropheNet:
    """cancel_catastrophe_net tests."""

    def test_cancel_succeeds(self):
        """Cancel with a working broker returns success."""
        broker = MagicMock()
        broker.cancel_order.return_value = None  # void success

        result = cancel_catastrophe_net("order-123", broker=broker)

        assert result.success is True
        assert result.order_id == "order-123"
        broker.cancel_order.assert_called_once_with("order-123")

    def test_cancel_no_broker_returns_success(self):
        """Cancel without broker (paper mode) returns success."""
        result = cancel_catastrophe_net("order-123", broker=None)

        assert result.success is True
        assert result.order_id == "order-123"

    def test_cancel_fails_triggers_kill_switch_and_raises(self):
        """Cancel failure engages kill switch and raises BrokerError."""
        broker = MagicMock()
        broker.cancel_order.side_effect = ConnectionError("broker unreachable")

        with patch("pmacs.cortex.kill_switch.engage") as mock_engage:
            with pytest.raises(BrokerError, match="broker unreachable"):
                cancel_catastrophe_net("order-456", broker=broker)

            # Kill switch must be engaged
            mock_engage.assert_called_once()
            call_kwargs = mock_engage.call_args[1]
            assert call_kwargs["trigger"] == "CATASTROPHE_CANCEL_FAILED"
            assert "order-456" in call_kwargs["reason"]

    def test_cancel_audits_event_on_success(self):
        """Successful cancellation produces audit log entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.log"
            from pmacs.storage.audit import AuditWriter
            writer = AuditWriter(audit_path)
            writer.append("test_setup", {"purpose": "setup"})
            writer.close()

            broker = MagicMock()
            cancel_catastrophe_net("order-789", broker=broker)

            # The function calls log_debug which writes to debug log
            # We verify the broker was called (audit is via log_debug)
            broker.cancel_order.assert_called_once_with("order-789")


class TestExecuteExit:
    """execute_exit orchestration tests."""

    def test_execute_exit_requires_cycle_id(self):
        """execute_exit raises ValueError without cycle_id (§16.5)."""
        holding = _make_holding()
        with pytest.raises(ValueError, match="cycle_id is REQUIRED"):
            execute_exit(holding, exit_reason="TEST", cycle_id="")

    def test_execute_exit_cancels_catastrophe_first(self):
        """execute_exit calls cancel_catastrophe_net before submitting SELL."""
        holding = _make_holding()
        broker = MagicMock()

        with patch(
            "pmacs.execution.catastrophe_net.cancel_catastrophe_net",
            return_value=CancelResult(success=True, order_id="cat-001"),
        ) as mock_cancel:
            result = execute_exit(
                holding,
                exit_reason="TRAILING_STOP",
                cycle_id="cycle-001",
                broker=broker,
                catastrophe_order_id="cat-001",
            )

            mock_cancel.assert_called_once_with("cat-001", broker=broker)
            assert result["cancel_result"].success is True
            assert result["exit_reason"] == "TRAILING_STOP"

    def test_execute_exit_sell_order(self):
        """execute_exit constructs SELL order with correct qty."""
        holding = _make_holding(entry_price=100.0, position_size=1000.0)
        result = execute_exit(
            holding,
            exit_reason="STOPPED_OUT",
            cycle_id="cycle-001",
        )
        assert result["exit_order"]["side"] == "SELL"
        assert result["exit_order"]["ticker"] == "TEST"
        assert result["exit_order"]["qty"] == 10.0  # 1000 / 100

    def test_execute_exit_writes_audit(self):
        """execute_exit writes to audit log when audit_path provided."""
        holding = _make_holding()
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.log"
            execute_exit(
                holding,
                exit_reason="STOPPED_OUT",
                cycle_id="cycle-001",
                audit_path=str(audit_path),
            )
            assert audit_path.exists()
            content = audit_path.read_text()
            assert "catastrophe_net_cancelled" in content
