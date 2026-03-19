"""Position sizing engine.

Calculates appropriate position size based on signal confidence,
risk budget, and portfolio state. Supports fixed-fraction and Kelly methods.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from cte.core.events import (
    STREAM_KEYS,
    OrderType,
    RiskAssessmentEvent,
    RiskDecision,
    Side,
    SignalAction,
    SignalEvent,
    SizedOrderEvent,
)

if TYPE_CHECKING:
    from cte.core.settings import SizingSettings
    from cte.core.streams import StreamPublisher

logger = structlog.get_logger(__name__)


class SizingEngine:
    """Calculates position sizes respecting risk limits."""

    def __init__(
        self,
        settings: SizingSettings,
        publisher: StreamPublisher,
        portfolio_value: Decimal = Decimal("10000"),
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._portfolio_value = portfolio_value

    async def size_order(
        self,
        signal: SignalEvent,
        risk_assessment: RiskAssessmentEvent,
        current_price: Decimal,
    ) -> SizedOrderEvent | None:
        """Calculate position size for an approved signal."""
        if risk_assessment.decision != RiskDecision.APPROVED:
            return None

        if signal.action == SignalAction.HOLD:
            return None

        notional = self._calculate_notional(signal.confidence)
        notional = self._clamp_notional(notional)

        if current_price <= 0:
            await logger.awarning("sizing_skip_zero_price", symbol=signal.symbol.value)
            return None

        quantity = notional / current_price

        side = Side.BUY if signal.action == SignalAction.OPEN_LONG else Side.SELL

        order = SizedOrderEvent(
            signal_id=signal.event_id,
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            notional_usd=notional,
            leverage=1,
            reason=f"Sized via {self._settings.method.value}: confidence={signal.confidence:.2f}",
        )

        await self._publisher.publish(STREAM_KEYS["sized_order"], order)

        await logger.ainfo(
            "order_sized",
            symbol=signal.symbol.value,
            notional=str(notional),
            quantity=str(quantity),
            method=self._settings.method.value,
        )

        return order

    def _calculate_notional(self, confidence: float) -> Decimal:
        """Calculate base notional from risk budget and confidence."""
        if self._settings.method.value == "kelly":
            fraction = self._kelly_fraction(confidence)
        else:
            fraction = Decimal(str(self._settings.fixed_fraction_pct))

        base = self._portfolio_value * fraction
        scaled = base * Decimal(str(confidence))
        return scaled.quantize(Decimal("0.01"))

    def _kelly_fraction(self, confidence: float) -> Decimal:
        """Half-Kelly sizing based on estimated win probability."""
        win_prob = confidence
        loss_prob = 1.0 - win_prob
        win_loss_ratio = 1.5  # assumed average win/loss ratio for v1

        if loss_prob == 0:
            kelly = Decimal("0.1")
        else:
            kelly_full = win_prob - (loss_prob / win_loss_ratio)
            kelly = Decimal(str(max(0, kelly_full)))

        if self._settings.kelly_half:
            kelly = kelly / 2

        return min(kelly, Decimal(str(self._settings.fixed_fraction_pct)))

    def _clamp_notional(self, notional: Decimal) -> Decimal:
        """Clamp notional to configured min/max bounds."""
        min_usd = Decimal(str(self._settings.min_order_usd))
        max_usd = Decimal(str(self._settings.max_order_usd))

        if notional < min_usd:
            return min_usd
        if notional > max_usd:
            return max_usd
        return notional
