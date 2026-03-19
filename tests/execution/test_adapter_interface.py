"""Tests verifying the common ExecutionAdapter interface contract.

Uses a mock adapter to validate that the interface is complete
and that both paper and venue modes of the ExecutionEngine work.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from cte.core.settings import ExecutionMode, ExecutionSettings, ExitSettings
from cte.core.streams import StreamPublisher
from cte.execution.adapter import (
    AdapterHealth,
    ExecutionAdapter,
    OrderRequest,
    OrderResult,
    OrderSide,
    VenueOrderStatus,
    VenuePosition,
)
from cte.execution.engine import ExecutionEngine


class MockAdapter(ExecutionAdapter):
    """Mock adapter implementing the full interface for testing."""

    def __init__(self):
        self.orders_placed: list[OrderRequest] = []
        self.orders_cancelled: list[str] = []
        self._started = False

    @property
    def venue_name(self) -> str:
        return "mock"

    async def place_order(self, request: OrderRequest) -> OrderResult:
        self.orders_placed.append(request)
        return OrderResult(
            client_order_id=request.client_order_id,
            venue_order_id="venue-123",
            symbol=request.symbol,
            side=request.side,
            status=VenueOrderStatus.FILLED,
            requested_quantity=request.quantity,
            filled_quantity=request.quantity,
            average_price=Decimal("50000"),
        )

    async def cancel_order(self, symbol: str, client_order_id: str) -> OrderResult:
        self.orders_cancelled.append(client_order_id)
        return OrderResult(
            client_order_id=client_order_id,
            status=VenueOrderStatus.CANCELLED,
        )

    async def get_order(self, symbol: str, client_order_id: str) -> OrderResult | None:
        return OrderResult(client_order_id=client_order_id, status=VenueOrderStatus.FILLED)

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        return []

    async def get_positions(self, symbol: str | None = None) -> list[VenuePosition]:
        return [VenuePosition(symbol="BTCUSDT", side="long", quantity=Decimal("1"))]

    async def close_position(
        self, symbol: str, quantity: Decimal, side: OrderSide
    ) -> OrderResult:
        return OrderResult(status=VenueOrderStatus.FILLED)

    async def health(self) -> AdapterHealth:
        return AdapterHealth(connected=self._started)

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False


class TestAdapterInterface:
    @pytest.mark.asyncio
    async def test_place_order(self):
        adapter = MockAdapter()
        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, quantity=Decimal("0.01"))
        result = await adapter.place_order(req)
        assert result.status == VenueOrderStatus.FILLED
        assert len(adapter.orders_placed) == 1

    @pytest.mark.asyncio
    async def test_cancel_order(self):
        adapter = MockAdapter()
        result = await adapter.cancel_order("BTCUSDT", "order-1")
        assert result.status == VenueOrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_get_positions(self):
        adapter = MockAdapter()
        positions = await adapter.get_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_close_position(self):
        adapter = MockAdapter()
        result = await adapter.close_position("BTCUSDT", Decimal("1"), OrderSide.BUY)
        assert result.status == VenueOrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_health(self):
        adapter = MockAdapter()
        h = await adapter.health()
        assert not h.connected
        await adapter.start()
        h = await adapter.health()
        assert h.connected

    @pytest.mark.asyncio
    async def test_lifecycle(self):
        adapter = MockAdapter()
        await adapter.start()
        assert adapter._started
        await adapter.stop()
        assert not adapter._started


class TestEngineWithAdapter:
    @pytest.mark.asyncio
    async def test_venue_mode_place_order(self):
        adapter = MockAdapter()
        settings = ExecutionSettings(mode=ExecutionMode.TESTNET)
        engine = ExecutionEngine(settings, ExitSettings(), AsyncMock(), adapter=adapter)

        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, quantity=Decimal("1"))
        result = await engine.place_order(req)
        assert result.status == VenueOrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_venue_mode_cancel(self):
        adapter = MockAdapter()
        settings = ExecutionSettings(mode=ExecutionMode.TESTNET)
        engine = ExecutionEngine(settings, ExitSettings(), AsyncMock(), adapter=adapter)

        result = await engine.cancel_order("BTCUSDT", "order-1")
        assert result.status == VenueOrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_venue_mode_positions(self):
        adapter = MockAdapter()
        settings = ExecutionSettings(mode=ExecutionMode.TESTNET)
        engine = ExecutionEngine(settings, ExitSettings(), AsyncMock(), adapter=adapter)

        positions = await engine.get_positions()
        assert len(positions) == 1

    @pytest.mark.asyncio
    async def test_paper_mode_still_works(self):
        settings = ExecutionSettings(mode=ExecutionMode.PAPER)
        publisher = AsyncMock(spec=StreamPublisher, publish=AsyncMock(return_value="x"))
        engine = ExecutionEngine(settings, ExitSettings(), publisher)
        assert engine.is_paper
        assert engine.paper_backend is not None

    @pytest.mark.asyncio
    async def test_venue_mode_start_stop(self):
        adapter = MockAdapter()
        settings = ExecutionSettings(mode=ExecutionMode.TESTNET)
        engine = ExecutionEngine(settings, ExitSettings(), AsyncMock(), adapter=adapter)

        await engine.start()
        h = await adapter.health()
        assert h.connected

        await engine.stop()
        h = await adapter.health()
        assert not h.connected


class TestOrderRequestIdempotency:
    def test_unique_client_order_ids(self):
        r1 = OrderRequest(symbol="BTCUSDT")
        r2 = OrderRequest(symbol="BTCUSDT")
        assert r1.client_order_id != r2.client_order_id

    def test_custom_idempotency_key(self):
        r = OrderRequest(symbol="BTCUSDT", idempotency_key="my-dedup-key")
        assert r.idempotency_key == "my-dedup-key"
