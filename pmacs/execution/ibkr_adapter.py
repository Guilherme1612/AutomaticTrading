"""Interactive Brokers live trading adapter (Architecture.md §4.1).

Skeleton implementation for LIVE_EARLY, LIVE_STANDARD, and LIVE_EXPANDED modes.
Uses ib_insync (ibapi wrapper) for order submission and fill polling.

Spec constraints:
  - Architecture.md §4.1: broker adapter is behind BrokerAdapter ABC
  - Architecture.md §16.7: broker gets ONLY catastrophe-net (15% below entry)
  - Architecture.md §5.5: all errors carry canonical error_code
  - Source.md §5: catastrophe-net stop = 15% below entry (non-negotiable)

Wiring:
  1. Operator completes wizard → broker credentials stored in Keychain
  2. Mode promoted to LIVE_EARLY via TOTP-gated promotion
  3. Nervous creates IBKRLiveAdapter via create_adapter(mode="LIVE_EARLY", ...)
  4. All trades signed by Ed25519 before submission

Prerequisites (must be installed before LIVE mode):
  - ib_insync package (pip install ib_insync)
  - TWS or IB Gateway running on localhost:7497 (paper) or 7496 (live)
  - API key/secret stored in macOS Keychain via pmacs installer
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from pmacs.execution.adapter import BrokerAdapter
from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.trade import TradeDirection, TradePlan, TradeResult

logger = logging.getLogger(__name__)


class IBKRLiveAdapter(BrokerAdapter):
    """Interactive Brokers live adapter via ib_insync.

    Args:
        host: TWS/IB Gateway host (default localhost).
        port: TWS/IB Gateway port (7497=paper, 7496=live).
        client_id: Unique client ID for this connection.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
    ) -> None:
        try:
            from ib_insync import IB
        except ImportError:
            raise ImportError(
                "ib_insync is required for LIVE trading. "
                "Install with: pip install ib_insync"
            )

        self._ib = IB()
        self._host = host
        self._port = port
        self._client_id = client_id
        self._connected = False

    async def _ensure_connected(self) -> None:
        """Connect to TWS/IB Gateway if not already connected."""
        if not self._connected:
            await self._ib.connectAsync(
                self._host, self._port, clientId=self._client_id
            )
            self._connected = True

    async def submit_order(self, plan: TradePlan) -> str:
        """Submit order via IBKR, return broker order ID."""
        from ib_insync import Stock, Order

        await self._ensure_connected()

        contract = Stock(plan.ticker, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        action = "BUY" if plan.direction == TradeDirection.BUY else "SELL"
        quantity = max(1, int(plan.quantity))

        if plan.order_type.value == "LIMIT" and plan.price_usd:
            order = Order(
                action=action,
                quantity=quantity,
                orderType="LMT",
                lmtPrice=plan.price_usd,
                tif="DAY",
            )
        elif plan.order_type.value == "MARKET_ON_OPEN":
            order = Order(
                action=action,
                quantity=quantity,
                orderType="MKT",
                tif="OPG",
            )
        else:
            order = Order(
                action=action,
                quantity=quantity,
                orderType="MKT",
                tif="DAY",
            )

        trade = self._ib.placeOrder(contract, order)

        log_debug(
            "IBKR_ORDER_SUBMITTED",
            payload={
                "ticker": plan.ticker,
                "direction": action,
                "quantity": quantity,
                "order_type": plan.order_type.value,
                "order_id": str(trade.order.orderId),
            },
            level="INFO",
            cycle_id=plan.cycle_id,
            msg=f"IBKR order submitted: {action} {quantity} {plan.ticker}",
        )

        return str(trade.order.orderId)

    async def poll_fill(
        self, broker_order_id: str, timeout: float = 30.0
    ) -> TradeResult:
        """Poll for fill, return TradeResult."""
        await self._ensure_connected()

        order_id = int(broker_order_id)
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            for trade in self._ib.openTrades():
                if trade.order.orderId == order_id:
                    status = trade.orderStatus.status
                    if status == "Filled":
                        return TradeResult(
                            id=f"ibkr-fill-{order_id}",
                            trade_plan_id="",
                            ticker=trade.contract.symbol,
                            direction=TradeDirection.BUY
                            if trade.order.action == "BUY"
                            else TradeDirection.SELL,
                            filled_quantity=int(trade.orderStatus.filled),
                            filled_price_usd=float(
                                trade.orderStatus.avgFillPrice
                            ),
                            status="FILLED",
                            broker_order_id=broker_order_id,
                            filled_at=datetime.now(timezone.utc),
                        )
                    if status in ("Cancelled", "ApiCancelled", "Inactive"):
                        return TradeResult(
                            id=f"ibkr-rejected-{order_id}",
                            trade_plan_id="",
                            ticker="",
                            direction=TradeDirection.BUY,
                            filled_quantity=0,
                            filled_price_usd=0.0,
                            status="REJECTED",
                            broker_order_id=broker_order_id,
                            filled_at=datetime.now(timezone.utc),
                        )
            await asyncio.sleep(0.5)

        raise TimeoutError(
            f"IBKR fill poll timed out after {timeout}s for order {order_id}"
        )

    async def place_stop_order(
        self, ticker: str, stop_price: float, qty: int
    ) -> str:
        """Place catastrophe-net stop order."""
        from ib_insync import Stock, Order

        await self._ensure_connected()

        contract = Stock(ticker, "SMART", "USD")
        await self._ib.qualifyContractsAsync(contract)

        order = Order(
            action="SELL",
            quantity=qty,
            orderType="STP",
            stopPrice=stop_price,
            tif="GTC",
        )

        trade = self._ib.placeOrder(contract, order)

        log_debug(
            "IBKR_STOP_ORDER_PLACED",
            payload={
                "ticker": ticker,
                "stop_price": stop_price,
                "qty": qty,
                "order_id": str(trade.order.orderId),
            },
            level="INFO",
            msg=f"IBKR stop order placed: SELL {qty} {ticker} @ ${stop_price}",
        )

        return str(trade.order.orderId)

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order by ID."""
        await self._ensure_connected()

        order_id = int(broker_order_id)
        for trade in self._ib.openTrades():
            if trade.order.orderId == order_id:
                self._ib.cancelOrder(trade.order)
                return True
        return False

    async def get_position(self, ticker: str) -> dict | None:
        """Get current position for ticker."""
        await self._ensure_connected()

        positions = self._ib.positions()
        for pos in positions:
            if pos.contract.symbol == ticker:
                return {
                    "shares": pos.position,
                    "avg_entry_price": pos.avgCost,
                    "current_price": 0.0,  # Requires separate market data request
                }
        return None
