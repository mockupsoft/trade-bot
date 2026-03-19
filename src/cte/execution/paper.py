"""Paper trading execution engine.

Simulates order fills with configurable slippage and latency.
No real exchange connection — purely internal simulation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import structlog
from prometheus_client import Counter, Gauge, Histogram

from cte.core.events import (
    STREAM_KEYS,
    OrderEvent,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    Side,
    SizedOrderEvent,
    Symbol,
    Venue,
)
from cte.core.settings import ExecutionSettings
from cte.core.streams import StreamPublisher

logger = structlog.get_logger(__name__)

orders_total = Counter("cte_orders_total", "Total orders processed", ["symbol", "status"])
positions_open = Gauge("cte_positions_open", "Currently open positions", ["symbol"])
fill_latency = Histogram(
    "cte_order_fill_latency_seconds", "Simulated fill latency", ["venue"]
)


class PaperPosition:
    """In-memory tracking of a paper position."""

    def __init__(
        self,
        position_id: UUID,
        symbol: Symbol,
        side: Side,
        entry_price: Decimal,
        quantity: Decimal,
        signal_id: UUID,
        leverage: int = 1,
    ) -> None:
        self.position_id = position_id
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.quantity = quantity
        self.signal_id = signal_id
        self.leverage = leverage
        self.opened_at = datetime.now(timezone.utc)
        self.highest_price = entry_price
        self.lowest_price = entry_price
        self.current_price = entry_price

    def update_price(self, price: Decimal) -> None:
        self.current_price = price
        if price > self.highest_price:
            self.highest_price = price
        if price < self.lowest_price:
            self.lowest_price = price

    @property
    def unrealized_pnl(self) -> Decimal:
        if self.side == Side.BUY:
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    def to_snapshot(self) -> PositionSnapshot:
        return PositionSnapshot(
            position_id=self.position_id,
            symbol=self.symbol,
            side=self.side,
            entry_price=self.entry_price,
            current_price=self.current_price,
            quantity=self.quantity,
            unrealized_pnl=self.unrealized_pnl,
            leverage=self.leverage,
            opened_at=self.opened_at,
            signal_id=self.signal_id,
            highest_price=self.highest_price,
            lowest_price=self.lowest_price,
        )


class PaperExecutionEngine:
    """Simulates order execution for paper trading mode."""

    def __init__(
        self,
        settings: ExecutionSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._positions: dict[UUID, PaperPosition] = {}
        self._last_prices: dict[str, Decimal] = {}

    async def execute_order(self, order: SizedOrderEvent) -> OrderEvent:
        """Simulate order fill with slippage and latency."""
        await asyncio.sleep(self._settings.fill_delay_ms / 1000)

        last_price = self._last_prices.get(order.symbol.value, order.notional_usd / order.quantity)
        fill_price = self._apply_slippage(last_price, order.side)

        position_id = uuid4()
        position = PaperPosition(
            position_id=position_id,
            symbol=order.symbol,
            side=order.side,
            entry_price=fill_price,
            quantity=order.quantity,
            signal_id=order.signal_id,
            leverage=order.leverage,
        )
        self._positions[position_id] = position
        positions_open.labels(symbol=order.symbol.value).inc()

        order_event = OrderEvent(
            signal_id=order.signal_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            status=OrderStatus.FILLED,
            requested_quantity=order.quantity,
            filled_quantity=order.quantity,
            average_price=fill_price,
            venue=Venue.BINANCE,
            reason=f"Paper fill: {order.reason}",
        )

        await self._publisher.publish(STREAM_KEYS["order"], order_event)

        snapshot = position.to_snapshot()
        await self._publisher.publish(STREAM_KEYS["position"], snapshot)

        orders_total.labels(symbol=order.symbol.value, status="filled").inc()
        fill_latency.labels(venue="paper").observe(self._settings.fill_delay_ms / 1000)

        await logger.ainfo(
            "paper_fill",
            symbol=order.symbol.value,
            side=order.side.value,
            price=str(fill_price),
            quantity=str(order.quantity),
            position_id=str(position_id),
        )

        return order_event

    async def close_position(
        self,
        position_id: UUID,
        exit_price: Decimal,
        reason: str,
    ) -> OrderEvent | None:
        """Close a paper position."""
        position = self._positions.pop(position_id, None)
        if position is None:
            await logger.awarning("position_not_found", position_id=str(position_id))
            return None

        position.update_price(exit_price)
        positions_open.labels(symbol=position.symbol.value).dec()

        close_side = Side.SELL if position.side == Side.BUY else Side.BUY

        order_event = OrderEvent(
            signal_id=position.signal_id,
            symbol=position.symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            status=OrderStatus.FILLED,
            requested_quantity=position.quantity,
            filled_quantity=position.quantity,
            average_price=exit_price,
            venue=Venue.BINANCE,
            reason=f"Paper close: {reason}",
        )

        await self._publisher.publish(STREAM_KEYS["order"], order_event)

        orders_total.labels(symbol=position.symbol.value, status="filled").inc()

        return order_event

    def update_market_price(self, symbol: str, price: Decimal) -> None:
        """Update market price for all positions of a symbol."""
        self._last_prices[symbol] = price
        for pos in self._positions.values():
            if pos.symbol.value == symbol:
                pos.update_price(price)

    def _apply_slippage(self, price: Decimal, side: Side) -> Decimal:
        """Apply simulated slippage."""
        slippage_factor = Decimal(str(self._settings.slippage_bps)) / Decimal("10000")
        if side == Side.BUY:
            return price * (1 + slippage_factor)
        return price * (1 - slippage_factor)

    @property
    def open_positions(self) -> dict[UUID, PaperPosition]:
        return dict(self._positions)
