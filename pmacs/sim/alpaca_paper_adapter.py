"""Alpaca paper trading adapter (Architecture.md §4.1).

Wraps the synchronous alpaca-py SDK with asyncio.to_thread so all broker
calls are non-blocking from the event loop's perspective.

Only this file imports alpaca. All other code uses BrokerAdapter ABC.

Spec constraints:
  - Architecture.md §4.1: broker adapter is behind ABC
  - Architecture.md §16.7: broker gets ONLY catastrophe-net (15% below entry)
  - Quantity stays int per D3
  - Fill polling timeout: 30s per D5
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from pmacs.execution.adapter import BrokerAdapter
from pmacs.logsys.debug_log import log_debug
from pmacs.schemas.trade import TradeDirection, TradePlan, TradeResult

logger = logging.getLogger(__name__)

# Alpaca order status mapping to PMACS status strings
_FILLED_STATUSES = {"filled", "partially_filled"}
_DONE_STATUSES = {"filled", "partially_filled", "canceled", "rejected", "expired"}


def _direction_to_side(direction: TradeDirection) -> str:
    """Map PMACS TradeDirection to Alpaca OrderSide value."""
    return "buy" if direction == TradeDirection.BUY else "sell"


class AlpacaPaperAdapter(BrokerAdapter):
    """Alpaca paper trading adapter.

    Uses alpaca-py TradingClient in paper=True mode.
    All SDK calls are synchronous, wrapped with asyncio.to_thread.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        from alpaca.trading.client import TradingClient

        self._client = TradingClient(
            api_key=api_key,
            secret_key=api_secret,
            paper=True,
        )

    async def submit_order(self, plan: TradePlan) -> str:
        """Submit order via Alpaca, return broker order ID."""
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        side = OrderSide.BUY if plan.direction == TradeDirection.BUY else OrderSide.SELL

        if plan.order_type.value == "LIMIT" and plan.price_usd:
            request = LimitOrderRequest(
                symbol=plan.ticker,
                qty=plan.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=plan.price_usd,
            )
        elif plan.order_type.value == "MARKET_ON_OPEN":
            # Alpaca Market-On-Open: use OPG time-in-force if available
            tif = self._resolve_moo_tif()
            request = MarketOrderRequest(
                symbol=plan.ticker,
                qty=plan.quantity,
                side=side,
                time_in_force=tif,
            )
        else:
            request = MarketOrderRequest(
                symbol=plan.ticker,
                qty=plan.quantity,
                side=side,
                time_in_force=TimeInForce.DAY,
            )

        order = await asyncio.to_thread(self._client.submit_order, request)
        logger.info(
            "Alpaca order submitted: id=%s ticker=%s side=%s qty=%d",
            order.id,
            plan.ticker,
            plan.direction.value,
            plan.quantity,
        )
        return str(order.id)

    async def poll_fill(
        self, broker_order_id: str, timeout: float = 30.0
    ) -> TradeResult:
        """Poll Alpaca for order fill status."""
        import time

        start = time.monotonic()
        while (time.monotonic() - start) < timeout:
            order = await asyncio.to_thread(
                self._client.get_order_by_id, broker_order_id
            )
            status = str(order.status).lower()

            if status == "filled":
                return self._order_to_fill(order, broker_order_id)
            if status == "partially_filled":
                return self._order_to_fill(order, broker_order_id, status="PARTIAL")
            if status in ("canceled", "rejected", "expired"):
                return TradeResult(
                    id=f"fill-{broker_order_id}",
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
            f"Fill not received within {timeout}s for order {broker_order_id}"
        )

    async def place_stop_order(
        self, ticker: str, stop_price: float, qty: int
    ) -> str:
        """Place a stop-market (catastrophe-net) sell order."""
        from alpaca.trading.requests import StopOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderType

        request = StopOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,
            type=OrderType.STOP,
            time_in_force=TimeInForce.GTC,
            stop_price=stop_price,
        )
        order = await asyncio.to_thread(self._client.submit_order, request)
        logger.info(
            "Alpaca stop order placed: id=%s ticker=%s stop=%.2f qty=%d",
            order.id,
            ticker,
            stop_price,
            qty,
        )
        return str(order.id)

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an order on Alpaca."""
        try:
            await asyncio.to_thread(
                self._client.cancel_order_by_id, broker_order_id
            )
            logger.info("Alpaca order cancelled: id=%s", broker_order_id)
            return True
        except Exception as exc:
            log_debug(
                "ALPACA_CANCEL_FAILED",
                payload={"broker_order_id": broker_order_id, "error": str(exc)},
                level="ERROR",
                error_code="ALPACA_ADAPTER_ERROR",
                msg=f"Alpaca cancel failed: id={broker_order_id} error={exc}",
            )
            return False

    async def get_position(self, ticker: str) -> dict | None:
        """Get current position for ticker from Alpaca."""
        try:
            pos = await asyncio.to_thread(
                self._client.get_open_position, ticker
            )
            return {
                "shares": int(pos.qty),
                "avg_entry_price": float(pos.avg_entry_price),
                "current_price": float(pos.current_price),
            }
        except Exception:
            return None

    @staticmethod
    def _resolve_moo_tif():
        """Resolve TimeInForce for Market-On-Open orders.

        Alpaca SDK exposes OPG (at-open) as a TimeInForce value in newer
        versions.  If unavailable, fall back to DAY and log a warning.
        """
        from alpaca.trading.enums import TimeInForce

        # OPG was added in alpaca-py >= 0.20.0
        opg = getattr(TimeInForce, "OPG", None)
        if opg is not None:
            return opg

        logger.warning(
            "TimeInForce.OPG not available in alpaca-py; "
            "falling back to DAY for MARKET_ON_OPEN order"
        )
        return TimeInForce.DAY

    @staticmethod
    def _order_to_fill(
        order, broker_order_id: str, status: str = "FILLED"
    ) -> TradeResult:
        """Convert an Alpaca order object to PMACS TradeResult."""
        filled_qty = int(order.filled_qty) if order.filled_qty else 0
        filled_price = float(order.filled_avg_price) if order.filled_avg_price else 0.0
        side_str = str(order.side).lower() if order.side else "buy"

        return TradeResult(
            id=f"fill-{broker_order_id}",
            trade_plan_id="",
            ticker=str(order.symbol) if order.symbol else "",
            direction=(
                TradeDirection.BUY if side_str == "buy" else TradeDirection.SELL
            ),
            filled_quantity=filled_qty,
            filled_price_usd=filled_price,
            status=status,
            broker_order_id=broker_order_id,
            filled_at=datetime.now(timezone.utc),
        )
