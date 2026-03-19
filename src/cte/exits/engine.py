"""Smart exit engine.

Monitors open positions and triggers exits based on configurable conditions:
trailing stop, take profit, stop loss, timeout, and invalidation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import structlog
from prometheus_client import Counter

from cte.core.events import (
    STREAM_KEYS,
    ExitEvent,
    ExitReason,
)
from cte.core.settings import ExitSettings
from cte.core.streams import StreamPublisher
from cte.execution.paper import PaperPosition

logger = structlog.get_logger(__name__)

exits_total = Counter(
    "cte_exits_total", "Total exits triggered", ["symbol", "reason"]
)


class ExitCondition:
    """Evaluation result for a single exit condition."""

    def __init__(self, triggered: bool, reason: ExitReason, detail: str = "") -> None:
        self.triggered = triggered
        self.reason = reason
        self.detail = detail


class SmartExitEngine:
    """Monitors positions and triggers exits when conditions are met."""

    def __init__(
        self,
        settings: ExitSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._trailing_highs: dict[UUID, Decimal] = {}

    async def evaluate_position(
        self,
        position: PaperPosition,
        current_price: Decimal,
        now: datetime | None = None,
    ) -> ExitEvent | None:
        """Check all exit conditions for a position. Returns ExitEvent if any triggers."""
        if now is None:
            now = datetime.now(timezone.utc)

        position.update_price(current_price)

        if position.position_id not in self._trailing_highs:
            self._trailing_highs[position.position_id] = position.entry_price
        if current_price > self._trailing_highs[position.position_id]:
            self._trailing_highs[position.position_id] = current_price

        conditions = [
            self._check_stop_loss(position, current_price),
            self._check_take_profit(position, current_price),
            self._check_trailing_stop(position, current_price),
            self._check_timeout(position, now),
        ]

        triggered = [c for c in conditions if c.triggered]
        if not triggered:
            return None

        exit_condition = triggered[0]
        hold_seconds = int((now - position.opened_at).total_seconds())

        pnl = (current_price - position.entry_price) * position.quantity

        event = ExitEvent(
            position_id=position.position_id,
            symbol=position.symbol,
            exit_reason=exit_condition.reason,
            exit_price=current_price,
            pnl=pnl,
            hold_duration_seconds=hold_seconds,
            reason_detail=exit_condition.detail,
        )

        await self._publisher.publish(STREAM_KEYS["exit"], event)

        exits_total.labels(
            symbol=position.symbol.value, reason=exit_condition.reason.value
        ).inc()

        self._trailing_highs.pop(position.position_id, None)

        await logger.ainfo(
            "exit_triggered",
            position_id=str(position.position_id),
            symbol=position.symbol.value,
            reason=exit_condition.reason.value,
            pnl=str(pnl),
            hold_seconds=hold_seconds,
        )

        return event

    def _check_stop_loss(
        self, position: PaperPosition, price: Decimal
    ) -> ExitCondition:
        loss_pct = float((position.entry_price - price) / position.entry_price)
        threshold = self._settings.stop_loss_pct

        if loss_pct >= threshold:
            return ExitCondition(
                triggered=True,
                reason=ExitReason.STOP_LOSS,
                detail=f"Loss {loss_pct:.2%} exceeded stop loss {threshold:.2%}",
            )
        return ExitCondition(triggered=False, reason=ExitReason.STOP_LOSS)

    def _check_take_profit(
        self, position: PaperPosition, price: Decimal
    ) -> ExitCondition:
        gain_pct = float((price - position.entry_price) / position.entry_price)
        threshold = self._settings.take_profit_pct

        if gain_pct >= threshold:
            return ExitCondition(
                triggered=True,
                reason=ExitReason.TAKE_PROFIT,
                detail=f"Gain {gain_pct:.2%} reached take profit {threshold:.2%}",
            )
        return ExitCondition(triggered=False, reason=ExitReason.TAKE_PROFIT)

    def _check_trailing_stop(
        self, position: PaperPosition, price: Decimal
    ) -> ExitCondition:
        trailing_high = self._trailing_highs.get(
            position.position_id, position.entry_price
        )

        if trailing_high <= 0:
            return ExitCondition(triggered=False, reason=ExitReason.TRAILING_STOP)

        drawdown = float((trailing_high - price) / trailing_high)
        threshold = self._settings.trailing_stop_pct

        if drawdown >= threshold and price > position.entry_price:
            return ExitCondition(
                triggered=True,
                reason=ExitReason.TRAILING_STOP,
                detail=f"Trailing drawdown {drawdown:.2%} from high {trailing_high}",
            )
        return ExitCondition(triggered=False, reason=ExitReason.TRAILING_STOP)

    def _check_timeout(
        self, position: PaperPosition, now: datetime
    ) -> ExitCondition:
        hold_minutes = (now - position.opened_at).total_seconds() / 60
        threshold = self._settings.max_hold_minutes

        if hold_minutes >= threshold:
            return ExitCondition(
                triggered=True,
                reason=ExitReason.TIMEOUT,
                detail=f"Held {hold_minutes:.0f} min, exceeds max {threshold} min",
            )
        return ExitCondition(triggered=False, reason=ExitReason.TIMEOUT)
