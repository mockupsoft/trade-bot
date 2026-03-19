"""Execution engine coordinator.

Routes signals to the appropriate executor based on engine mode.
Supports paper, testnet (Binance/Bybit), and future live modes.
All backends implement the ExecutionAdapter interface.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import structlog

from cte.core.events import ScoredSignalEvent
from cte.core.settings import ExecutionMode, ExecutionSettings, ExitSettings
from cte.core.streams import StreamPublisher
from cte.execution.adapter import (
    ExecutionAdapter,
    OrderRequest,
    OrderResult,
    OrderSide,
    VenuePosition,
)
from cte.execution.fill_model import FillMode
from cte.execution.paper import PaperExecutionEngine
from cte.execution.position import PaperPosition

logger = structlog.get_logger(__name__)


class ExecutionEngine:
    """Dispatches signals to the active execution backend.

    In paper mode, uses PaperExecutionEngine directly (synchronous fills).
    In testnet/live mode, uses ExecutionAdapter (async venue I/O).
    """

    def __init__(
        self,
        exec_settings: ExecutionSettings,
        exit_settings: ExitSettings,
        publisher: StreamPublisher,
        adapter: ExecutionAdapter | None = None,
    ) -> None:
        self._exec_settings = exec_settings
        self._publisher = publisher
        self._adapter = adapter
        self._paper_backend: PaperExecutionEngine | None = None

        if exec_settings.mode == ExecutionMode.PAPER:
            fill_mode = FillMode(exec_settings.fill_model)
            self._paper_backend = PaperExecutionEngine(
                exec_settings, exit_settings, publisher, fill_mode,
            )
        elif adapter is not None:
            self._adapter = adapter
        else:
            raise ValueError(
                f"Mode '{exec_settings.mode.value}' requires an ExecutionAdapter"
            )

    @property
    def mode(self) -> ExecutionMode:
        return self._exec_settings.mode

    @property
    def is_paper(self) -> bool:
        return self._paper_backend is not None

    # ── Paper mode operations ─────────────────────────────────

    async def execute_signal(
        self,
        signal: ScoredSignalEvent,
        quantity: Decimal,
        notional_usd: Decimal,
        event_time: datetime,
    ) -> PaperPosition | None:
        """Execute a scored signal (paper mode)."""
        if self._paper_backend:
            return self._paper_backend.open_position(
                signal, quantity, notional_usd, event_time
            )
        raise NotImplementedError("Use place_order() for venue execution")

    def update_book(self, symbol: str, best_bid: Decimal, best_ask: Decimal) -> None:
        if self._paper_backend:
            self._paper_backend.update_book(symbol, best_bid, best_ask)

    def update_price_and_evaluate(
        self, symbol: str, price: Decimal, event_time: datetime
    ) -> list[PaperPosition]:
        if self._paper_backend:
            self._paper_backend.update_price(symbol, price)
            return self._paper_backend.evaluate_exits(symbol, price, event_time)
        return []

    # ── Venue mode operations ─────────────────────────────────

    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place an order on the venue (testnet/live mode)."""
        if self._adapter:
            return await self._adapter.place_order(request)
        raise NotImplementedError("Not in venue mode")

    async def cancel_order(self, symbol: str, client_order_id: str) -> OrderResult:
        if self._adapter:
            return await self._adapter.cancel_order(symbol, client_order_id)
        raise NotImplementedError("Not in venue mode")

    async def get_positions(self, symbol: str | None = None) -> list[VenuePosition]:
        if self._adapter:
            return await self._adapter.get_positions(symbol)
        raise NotImplementedError("Not in venue mode")

    async def close_venue_position(
        self, symbol: str, quantity: Decimal, side: OrderSide
    ) -> OrderResult:
        if self._adapter:
            return await self._adapter.close_position(symbol, quantity, side)
        raise NotImplementedError("Not in venue mode")

    # ── Lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        if self._adapter:
            await self._adapter.start()

    async def stop(self) -> None:
        if self._adapter:
            await self._adapter.stop()

    @property
    def paper_backend(self) -> PaperExecutionEngine | None:
        return self._paper_backend

    @property
    def adapter(self) -> ExecutionAdapter | None:
        return self._adapter
