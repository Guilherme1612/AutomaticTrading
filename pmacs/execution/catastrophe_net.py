"""Catastrophe-net stop placement and cancellation -- broker-side safety net.

Spec ref: Architecture.md §9.3, §11.5, §16.7
PMACS manages tight stops internally. The broker receives only a catastrophe-net
stop at 15% below entry. This prevents catastrophic losses if PMACS stops responding.

Anti-pattern (Architecture.md §16.7): NEVER place tight stops on the broker side.
"""
from __future__ import annotations

from dataclasses import dataclass

from pmacs.constants import CATASTROPHE_NET_PCT
from pmacs.logsys import log_debug


def compute_catastrophe_stop(entry_price: float) -> float:
    """Compute the broker-side catastrophe-net stop price.

    The stop is placed at CATASTROPHE_NET_PCT (15%) below entry price.
    Rounded to 2 decimal places for broker compatibility.
    """
    if entry_price <= 0:
        raise ValueError(f"Entry price must be positive, got {entry_price}")
    return round(entry_price * (1 - CATASTROPHE_NET_PCT), 2)


def place_catastrophe_net_stop(
    ticker: str,
    entry_price: float,
    shares: float,
) -> dict:
    """Create a catastrophe-net stop order for broker submission.

    Returns an order dict. Actual submission to broker happens in
    pmacs.execution.service. This function does NOT submit the order.

    Args:
        ticker: Stock ticker symbol.
        entry_price: Entry price of the position.
        shares: Number of shares.

    Returns:
        Order dict with all fields needed for broker submission.
    """
    stop_price = compute_catastrophe_stop(entry_price)
    return {
        "ticker": ticker,
        "side": "SELL",
        "type": "STOP_MARKET",
        "stop_price": stop_price,
        "qty": shares,
        "time_in_force": "GTC",
        "reason": "catastrophe_net",
    }


def validate_stop_order(order: dict) -> bool:
    """Validate a catastrophe-net stop order has all required fields."""
    required_fields = {"ticker", "side", "type", "stop_price", "qty", "time_in_force", "reason"}
    if not required_fields.issubset(order.keys()):
        return False
    if order["side"] != "SELL":
        return False
    if order["type"] != "STOP_MARKET":
        return False
    if order["reason"] != "catastrophe_net":
        return False
    if order["stop_price"] <= 0 or order["qty"] <= 0:
        return False
    return True


@dataclass(frozen=True)
class CancelResult:
    """Result of a catastrophe-net stop cancellation."""
    success: bool
    order_id: str = ""


class BrokerError(Exception):
    """Raised when broker communication fails."""


async def cancel_catastrophe_net(order_id: str, broker=None) -> CancelResult:
    """Cancel a catastrophe-net stop order on the broker.

    Implements Architecture.md Section 11.5. On broker failure, engages
    kill switch and raises.

    Args:
        order_id: The broker order ID to cancel.
        broker: Broker adapter with cancel_order(order_id) method.
            In tests, can be a mock. In production, the real broker adapter.

    Returns:
        CancelResult(success=True) on success.

    Raises:
        BrokerError: If cancellation fails. Kill switch is engaged first.
    """
    if broker is None:
        # No broker configured (paper mode / testing) -- treat as success
        log_debug(
            "CATASTROPHE_CANCEL_NO_BROKER",
            payload={"order_id": order_id},
            level="INFO",
            msg=f"Catastrophe-net cancel skipped (no broker): order_id={order_id}",
        )
        return CancelResult(success=True, order_id=order_id)

    try:
        await broker.cancel_order(order_id)
        log_debug(
            "CATASTROPHE_NET_CANCELLED",
            payload={"order_id": order_id, "success": True},
            level="INFO",
            msg=f"Catastrophe-net cancelled: order_id={order_id}",
        )
        return CancelResult(success=True, order_id=order_id)
    except Exception as exc:
        log_debug(
            "CATASTROPHE_CANCEL_FAILED",
            payload={"order_id": order_id, "error": str(exc)},
            level="ERROR",
            error_code="CATASTROPHE_CANCEL_FAILED",
            msg=f"Catastrophe-net cancel FAILED: order_id={order_id}, error={exc}",
        )
        from pmacs.cortex.kill_switch import engage as engage_kill_switch
        engage_kill_switch(
            reason=f"Catastrophe-net cancel failed for order {order_id}: {exc}",
            trigger="CATASTROPHE_CANCEL_FAILED",
        )
        raise BrokerError(
            f"Catastrophe-net cancel failed for order {order_id}: {exc}"
        ) from exc


async def execute_exit(
    holding,  # Holding model
    exit_reason: str,
    cycle_id: str,
    broker=None,
    audit_path: str | None = None,
    catastrophe_order_id: str = "",
) -> dict:
    """Orchestrate a full exit: cancel catastrophe-net, submit SELL, audit.

    Sequence (Architecture.md Section 11.5):
    1. Cancel existing catastrophe-net stop.
    2. Submit primary exit order (SELL).
    3. Audit the cancellation and exit.

    Args:
        holding: Holding model with ticker, id, entry_price_usd, position_size_usd.
        exit_reason: Reason for exit (e.g., "TRAILING_STOP", "STOPPED_OUT").
        cycle_id: REQUIRED cycle ID (Architecture.md §16.5).
        broker: Broker adapter (or None for paper/testing).
        audit_path: Optional path to audit log.
        catastrophe_order_id: The broker order ID for the catastrophe-net stop.

    Returns:
        Dict with exit order details.

    Raises:
        ValueError: If cycle_id is empty (anti-pattern §16.5).
    """
    if not cycle_id:
        raise ValueError(
            "cycle_id is REQUIRED on audit-emitting functions (Architecture.md §16.5)"
        )

    # Step 1: Cancel catastrophe-net stop
    cancel_result = CancelResult(success=True)  # Default: no-op
    if catastrophe_order_id:
        cancel_result = await cancel_catastrophe_net(catastrophe_order_id, broker=broker)

    # Step 2: Submit primary exit order
    exit_order = {
        "ticker": holding.ticker,
        "side": "SELL",
        "type": "MARKET",
        "qty": 0,  # Will be filled by execution service from position_size_usd
    }
    if holding.position_size_usd and holding.entry_price_usd and holding.entry_price_usd > 0:
        exit_order["qty"] = holding.position_size_usd / holding.entry_price_usd

    # Step 3: Audit
    log_debug(
        "EXECUTE_EXIT",
        payload={
            "holding_id": holding.id,
            "ticker": holding.ticker,
            "exit_reason": exit_reason,
            "catastrophe_cancelled": cancel_result.success,
            "catastrophe_order_id": catastrophe_order_id,
        },
        level="INFO",
        cycle_id=cycle_id,
        msg=f"Exit executed: {holding.ticker} reason={exit_reason}",
    )

    if audit_path:
        from pmacs.storage.audit import AuditWriter
        writer = AuditWriter(audit_path)
        writer.append(
            "catastrophe_net_cancelled",
            {
                "holding_id": holding.id,
                "ticker": holding.ticker,
                "exit_reason": exit_reason,
                "catastrophe_order_id": catastrophe_order_id,
                "cancel_success": cancel_result.success,
            },
            cycle_id=cycle_id,
        )
        writer.close()

    return {
        "exit_order": exit_order,
        "cancel_result": cancel_result,
        "holding_id": holding.id,
        "exit_reason": exit_reason,
    }
