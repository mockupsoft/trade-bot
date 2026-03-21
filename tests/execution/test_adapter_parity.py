import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from cte.execution.adapter import OrderRequest, OrderSide, OrderRequestType
from cte.execution.binance_adapter import BinanceTestnetAdapter
from cte.execution.bybit_adapter import BybitDemoAdapter

class TestBinanceTestnetAdapterParity:
    @pytest.mark.asyncio
    async def test_place_short_order(self):
        adapter = BinanceTestnetAdapter("key", "secret")
        adapter._signed_request = AsyncMock(return_value={
            "clientOrderId": "abc", "orderId": "123", "symbol": "BTCUSDT",
            "side": "SELL", "status": "NEW", "origQty": "1.0", "executedQty": "0", "positionSide": "SHORT"
        })

        req = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, direction="short",
            order_type=OrderRequestType.MARKET, quantity=Decimal("1")
        )
        res = await adapter.place_order(req)

        adapter._signed_request.assert_called_once()
        params = adapter._signed_request.call_args[0][2]

        assert params["side"] == "SELL"
        assert params["positionSide"] == "SHORT"

    @pytest.mark.asyncio
    async def test_close_short_position(self):
        adapter = BinanceTestnetAdapter("key", "secret")
        adapter.place_order = AsyncMock()

        await adapter.close_position("BTCUSDT", Decimal("1"), OrderSide.SELL, direction="short")

        adapter.place_order.assert_called_once()
        req = adapter.place_order.call_args[0][0]

        assert req.side == OrderSide.BUY  # Closing a short is a BUY
        assert req.direction == "short"
        assert req.reduce_only is True

class TestBybitDemoAdapterParity:
    @pytest.mark.asyncio
    async def test_place_short_order(self):
        adapter = BybitDemoAdapter("key", "secret")
        adapter._signed_request = AsyncMock(return_value={
            "result": {"orderId": "123", "orderLinkId": "abc"}
        })

        req = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, direction="short",
            order_type=OrderRequestType.MARKET, quantity=Decimal("1")
        )
        res = await adapter.place_order(req)

        adapter._signed_request.assert_called_once()
        body = adapter._signed_request.call_args[0][2]

        assert body["side"] == "Sell"
        assert body["positionIdx"] == 2  # 2 is short

    @pytest.mark.asyncio
    async def test_close_short_position(self):
        adapter = BybitDemoAdapter("key", "secret")
        adapter.place_order = AsyncMock()

        await adapter.close_position("BTCUSDT", Decimal("1"), OrderSide.SELL, direction="short")

        adapter.place_order.assert_called_once()
        req = adapter.place_order.call_args[0][0]

        assert req.side == OrderSide.BUY
        assert req.direction == "short"
        assert req.reduce_only is True
