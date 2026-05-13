"""Unit tests for pmacs.execution.adapter — BrokerAdapter ABC, MockAdapter, factory."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pmacs.execution.adapter import BrokerAdapter, MockAdapter, create_adapter
from pmacs.schemas.system import Mode
from pmacs.schemas.trade import TradeDirection, TradePlan


def _make_plan(
    plan_id: str = "tp-test",
    ticker: str = "AAPL",
    direction: TradeDirection = TradeDirection.BUY,
    quantity: int = 10,
    price_usd: float = 150.0,
) -> TradePlan:
    return TradePlan(
        id=plan_id,
        ticker=ticker,
        direction=direction,
        quantity=quantity,
        price_usd=price_usd,
        cycle_id="cycle-test",
    )


class TestMockAdapter:
    """MockAdapter deterministic behavior."""

    @pytest.mark.asyncio()
    async def test_submit_order_returns_deterministic_id(self) -> None:
        adapter = MockAdapter()
        plan = _make_plan(plan_id="tp-42")
        result = await adapter.submit_order(plan)
        assert result == "mock-tp-42"

    @pytest.mark.asyncio()
    async def test_poll_fill_returns_filled_status(self) -> None:
        adapter = MockAdapter()
        fill = await adapter.poll_fill("mock-tp-42")
        assert fill.status == "FILLED"
        assert fill.broker_order_id == "mock-tp-42"
        assert fill.id == "fill-mock-tp-42"

    @pytest.mark.asyncio()
    async def test_place_stop_order_returns_deterministic_id(self) -> None:
        adapter = MockAdapter()
        stop_id = await adapter.place_stop_order("AAPL", 127.50, 10)
        assert stop_id == "mock-stop-AAPL-127.5"

    @pytest.mark.asyncio()
    async def test_cancel_order_returns_true(self) -> None:
        adapter = MockAdapter()
        result = await adapter.cancel_order("mock-tp-42")
        assert result is True

    @pytest.mark.asyncio()
    async def test_get_position_returns_none(self) -> None:
        adapter = MockAdapter()
        result = await adapter.get_position("AAPL")
        assert result is None


class TestCreateAdapter:
    """Factory function tests."""

    def test_shadow_mode_returns_mock(self) -> None:
        adapter = create_adapter(Mode.SHADOW.value)
        assert isinstance(adapter, MockAdapter)

    def test_installing_mode_returns_mock(self) -> None:
        adapter = create_adapter(Mode.INSTALLING.value)
        assert isinstance(adapter, MockAdapter)

    def test_paper_mode_returns_alpaca(self) -> None:
        # Patch the import inside create_adapter's lazy import
        with patch("pmacs.execution.alpaca_paper.AlpacaPaperAdapter") as patched:
            mock_instance = MagicMock()
            patched.return_value = mock_instance
            adapter = create_adapter(
                Mode.PAPER.value, api_key="key", api_secret="secret"
            )
            patched.assert_called_once_with("key", "secret")
            assert adapter is mock_instance

    def test_paper_mode_without_credentials_raises(self) -> None:
        with pytest.raises(ValueError, match="api_key and api_secret required"):
            create_adapter(Mode.PAPER.value)

    def test_live_mode_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="LIVE modes not yet supported"):
            create_adapter(Mode.LIVE_EARLY.value)

    def test_unknown_mode_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown mode"):
            create_adapter("INVALID_MODE")


class TestBrokerAdapterABC:
    """Verify ABC cannot be instantiated."""

    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            BrokerAdapter()  # type: ignore[abstract]


class TestAlpacaPaperAdapterWithMock:
    """AlpacaPaperAdapter unit tests with mocked TradingClient."""

    @pytest.mark.asyncio()
    async def test_submit_order_market(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_order = MagicMock()
        mock_order.id = "alpaca-order-123"
        mock_client = MagicMock()
        mock_client.submit_order.return_value = mock_order

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        plan = _make_plan(order_type="MARKET")
        result = await adapter.submit_order(plan)
        assert result == "alpaca-order-123"
        mock_client.submit_order.assert_called_once()

    @pytest.mark.asyncio()
    async def test_submit_order_limit(self) -> None:
        from pmacs.schemas.trade import OrderType

        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_order = MagicMock()
        mock_order.id = "alpaca-limit-456"
        mock_client = MagicMock()
        mock_client.submit_order.return_value = mock_order

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        plan = _make_plan(order_type=OrderType.LIMIT)
        result = await adapter.submit_order(plan)
        assert result == "alpaca-limit-456"

    @pytest.mark.asyncio()
    async def test_poll_fill_filled(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_order = MagicMock()
        mock_order.status = "filled"
        mock_order.filled_qty = 10
        mock_order.filled_avg_price = 150.50
        mock_order.side = "buy"
        mock_order.symbol = "AAPL"

        mock_client = MagicMock()
        mock_client.get_order_by_id.return_value = mock_order

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        fill = await adapter.poll_fill("alpaca-order-123", timeout=1.0)
        assert fill.status == "FILLED"
        assert fill.filled_quantity == 10
        assert fill.filled_price_usd == 150.50
        assert fill.ticker == "AAPL"

    @pytest.mark.asyncio()
    async def test_poll_fill_rejected(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_order = MagicMock()
        mock_order.status = "rejected"

        mock_client = MagicMock()
        mock_client.get_order_by_id.return_value = mock_order

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        fill = await adapter.poll_fill("alpaca-order-rej", timeout=1.0)
        assert fill.status == "REJECTED"
        assert fill.filled_quantity == 0

    @pytest.mark.asyncio()
    async def test_poll_fill_timeout(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        # Order stays in "new" status (never fills)
        mock_order = MagicMock()
        mock_order.status = "new"

        mock_client = MagicMock()
        mock_client.get_order_by_id.return_value = mock_order

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        with pytest.raises(TimeoutError, match="Fill not received"):
            await adapter.poll_fill("alpaca-order-slow", timeout=0.5)

    @pytest.mark.asyncio()
    async def test_place_stop_order(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_order = MagicMock()
        mock_order.id = "stop-order-789"
        mock_client = MagicMock()
        mock_client.submit_order.return_value = mock_order

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        stop_id = await adapter.place_stop_order("AAPL", 127.50, 10)
        assert stop_id == "stop-order-789"

    @pytest.mark.asyncio()
    async def test_cancel_order_success(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_client = MagicMock()
        mock_client.cancel_order_by_id.return_value = None

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        result = await adapter.cancel_order("order-123")
        assert result is True

    @pytest.mark.asyncio()
    async def test_cancel_order_failure(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_client = MagicMock()
        mock_client.cancel_order_by_id.side_effect = Exception("not found")

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        result = await adapter.cancel_order("order-bad")
        assert result is False

    @pytest.mark.asyncio()
    async def test_get_position_exists(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_pos = MagicMock()
        mock_pos.qty = "15"
        mock_pos.avg_entry_price = "148.50"
        mock_pos.current_price = "155.00"
        mock_client = MagicMock()
        mock_client.get_open_position.return_value = mock_pos

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        pos = await adapter.get_position("AAPL")
        assert pos is not None
        assert pos["shares"] == 15
        assert pos["avg_entry_price"] == 148.50
        assert pos["current_price"] == 155.00

    @pytest.mark.asyncio()
    async def test_get_position_not_exists(self) -> None:
        from pmacs.execution.alpaca_paper import AlpacaPaperAdapter

        mock_client = MagicMock()
        mock_client.get_open_position.side_effect = Exception("no position")

        adapter = AlpacaPaperAdapter.__new__(AlpacaPaperAdapter)
        adapter._client = mock_client

        pos = await adapter.get_position("NOPE")
        assert pos is None


def _make_plan(
    plan_id: str = "tp-test",
    ticker: str = "AAPL",
    direction: TradeDirection = TradeDirection.BUY,
    quantity: int = 10,
    price_usd: float = 150.0,
    order_type: str = "MARKET",
) -> TradePlan:
    from pmacs.schemas.trade import OrderType

    return TradePlan(
        id=plan_id,
        ticker=ticker,
        direction=direction,
        quantity=quantity,
        price_usd=price_usd,
        order_type=OrderType(order_type),
        cycle_id="cycle-test",
    )
