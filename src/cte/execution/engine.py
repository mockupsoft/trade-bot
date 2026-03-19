"""Execution engine coordinator.

Routes signals to the appropriate executor based on engine mode.
In v1, only paper mode is available.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import structlog

from cte.core.events import ScoredSignalEvent
from cte.core.settings import ExecutionMode, ExecutionSettings, ExitSettings
from cte.core.streams import StreamPublisher
from cte.execution.fill_model import FillMode
from cte.execution.paper import PaperExecutionEngine
from cte.execution.position import PaperPosition

logger = structlog.get_logger(__name__)


class ExecutionEngine:
    """Dispatches signals to the active execution backend."""

    def __init__(
        self,
        exec_settings: ExecutionSettings,
        exit_settings: ExitSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._exec_settings = exec_settings
        self._publisher = publisher

        if exec_settings.mode == ExecutionMode.PAPER:
            fill_mode = FillMode(exec_settings.fill_model)
            self._backend = PaperExecutionEngine(
                exec_settings, exit_settings, publisher, fill_mode,
            )
        else:
            raise NotImplementedError(
                f"Execution mode '{exec_settings.mode.value}' not implemented in v1."
            )

    async def execute_signal(
        self,
        signal: ScoredSignalEvent,
        quantity: Decimal,
        notional_usd: Decimal,
        event_time: datetime,
    ) -> PaperPosition | None:
        """Execute a scored signal through the active backend."""
        if isinstance(self._backend, PaperExecutionEngine):
            return self._backend.open_position(signal, quantity, notional_usd, event_time)
        raise NotImplementedError

    def update_book(
        self,
        symbol: str,
        best_bid: Decimal,
        best_ask: Decimal,
    ) -> None:
        """Forward book updates to execution backend."""
        if isinstance(self._backend, PaperExecutionEngine):
            self._backend.update_book(symbol, best_bid, best_ask)

    def update_price_and_evaluate(
        self,
        symbol: str,
        price: Decimal,
        event_time: datetime,
    ) -> list[PaperPosition]:
        """Update price and evaluate exits. Returns closed positions."""
        if isinstance(self._backend, PaperExecutionEngine):
            self._backend.update_price(symbol, price)
            return self._backend.evaluate_exits(symbol, price, event_time)
        return []

    @property
    def backend(self) -> PaperExecutionEngine:
        return self._backend
