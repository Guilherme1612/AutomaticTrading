"""Broker adapter ABC, MockAdapter, and factory (Architecture.md §4.1, §4.5).

Spec ref: Architecture.md §4.1 — pmacs-execution signs trades, submits via adapter.
          Architecture.md §16.9 — Mutation A/B runs SHADOW-only, never PAPER.
          Architecture.md §16.10 — No mutation auto-applying.

Only alpaca_paper.py imports the alpaca SDK. All other code uses this ABC.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from pmacs.schemas.system import Mode
from pmacs.schemas.trade import TradeDirection, TradePlan, TradeResult

logger = logging.getLogger(__name__)


class BrokerAdapter(ABC):
    """Abstract broker adapter (Architecture.md §4.1).

    All methods are async so callers can uniformly await regardless of backend.
    Synchronous broker SDKs (e.g. alpaca-py) are wrapped with asyncio.to_thread
    inside their concrete adapter.
    """

    @abstractmethod
    async def submit_order(self, plan: TradePlan) -> str:
        """Submit order, return broker_order_id."""

    @abstractmethod
    async def poll_fill(
        self, broker_order_id: str, timeout: float = 30.0
    ) -> TradeResult:
        """Poll for fill, return TradeResult.

        Args:
            broker_order_id: Order ID returned by submit_order.
            timeout: Max seconds to wait (default 30 per Architecture.md §D5).

        Returns:
            TradeResult with filled price/quantity.

        Raises:
            TimeoutError: If fill not received within timeout.
        """

    @abstractmethod
    async def place_stop_order(
        self, ticker: str, stop_price: float, qty: int
    ) -> str:
        """Place stop-loss order, return stop_order_id.

        Used for catastrophe-net stops (15% below entry).
        """

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel order, return success."""

    @abstractmethod
    async def get_position(self, ticker: str) -> dict | None:
        """Get current position for ticker.

        Returns dict with keys: shares, avg_entry_price, current_price.
        Returns None if no position.
        """


class MockAdapter(BrokerAdapter):
    """Deterministic mock for tests. Mirrors current mock fill behavior.

    Thread-safe: all state is local to each call. No shared mutable state.
    """

    async def submit_order(self, plan: TradePlan) -> str:
        """Return deterministic mock order ID."""
        return f"mock-{plan.id}"

    async def poll_fill(
        self, broker_order_id: str, timeout: float = 30.0
    ) -> TradeResult:
        """Instant fill at plan price/quantity.

        The caller is responsible for setting ticker/direction/quantity from
        the TradePlan since poll_fill only receives broker_order_id.
        The TradeResult fields are populated with defaults that the caller
        should override from the plan.
        """
        return TradeResult(
            id=f"fill-{broker_order_id}",
            trade_plan_id=broker_order_id.replace("mock-", ""),
            ticker="UNKNOWN",
            direction=TradeDirection.BUY,
            filled_quantity=0,
            filled_price_usd=0.0,
            status="FILLED",
            broker_order_id=broker_order_id,
            filled_at=datetime.now(timezone.utc),
        )

    async def place_stop_order(
        self, ticker: str, stop_price: float, qty: int
    ) -> str:
        """Return deterministic mock stop order ID."""
        return f"mock-stop-{ticker}-{stop_price}"

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Always succeeds in mock."""
        logger.info("MockAdapter: cancel_order(%s) -> True", broker_order_id)
        return True

    async def get_position(self, ticker: str) -> dict | None:
        """No positions in mock."""
        return None


def create_adapter(
    mode: str,
    api_key: str = "",
    api_secret: str = "",
) -> BrokerAdapter:
    """Factory: select adapter by mode (Architecture.md §4.1).

    Args:
        mode: Mode string value (e.g. "SHADOW", "PAPER").
        api_key: Broker API key (required for PAPER+ modes).
        api_secret: Broker API secret (required for PAPER+ modes).

    Returns:
        MockAdapter for INSTALLING/SHADOW modes.
        AlpacaPaperAdapter for PAPER/PAPER_VALIDATED modes.

    Raises:
        NotImplementedError: For LIVE modes (not yet supported).
        ValueError: For unknown mode values.
    """
    if mode in (Mode.INSTALLING.value, Mode.SHADOW.value):
        return MockAdapter()
    if mode in (Mode.PAPER.value, Mode.PAPER_VALIDATED.value):
        if not api_key or not api_secret:
            raise ValueError(
                "api_key and api_secret required for PAPER/PAPER_VALIDATED mode"
            )
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        return AlpacaPaperAdapter(api_key, api_secret)
    if mode in (
        Mode.LIVE_EARLY.value,
        Mode.LIVE_STANDARD.value,
        Mode.LIVE_EXPANDED.value,
    ):
        raise NotImplementedError(f"LIVE modes not yet supported: {mode}")
    raise ValueError(f"Unknown mode: {mode}")
