"""Execution engine coordinator.

Routes orders to the appropriate executor based on engine mode
(paper, testnet, or live).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog

from cte.core.events import OrderEvent, SizedOrderEvent
from cte.core.settings import ExecutionMode, ExecutionSettings
from cte.core.streams import StreamPublisher
from cte.execution.paper import PaperExecutionEngine

logger = structlog.get_logger(__name__)


class ExecutionEngine:
    """Dispatches orders to the active execution backend."""

    def __init__(
        self,
        settings: ExecutionSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._settings = settings
        self._publisher = publisher

        if settings.mode == ExecutionMode.PAPER:
            self._backend = PaperExecutionEngine(settings, publisher)
        else:
            raise NotImplementedError(
                f"Execution mode '{settings.mode.value}' not implemented in v1. "
                "Only 'paper' mode is available."
            )

    async def execute(self, order: SizedOrderEvent) -> OrderEvent:
        """Execute a sized order through the active backend."""
        await logger.ainfo(
            "executing_order",
            symbol=order.symbol.value,
            side=order.side.value,
            quantity=str(order.quantity),
            mode=self._settings.mode.value,
        )
        return await self._backend.execute_order(order)

    async def close_position(
        self,
        position_id: UUID,
        exit_price: Decimal,
        reason: str,
    ) -> OrderEvent | None:
        """Close a position through the active backend."""
        if isinstance(self._backend, PaperExecutionEngine):
            return await self._backend.close_position(position_id, exit_price, reason)
        raise NotImplementedError

    def update_market_price(self, symbol: str, price: Decimal) -> None:
        """Forward price updates to the execution backend."""
        if isinstance(self._backend, PaperExecutionEngine):
            self._backend.update_market_price(symbol, price)
