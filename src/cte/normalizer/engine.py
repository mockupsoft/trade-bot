"""Canonical event normalizer.

Consumes raw venue events from Redis Streams, validates and transforms
them into venue-agnostic canonical CTE events.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter, Histogram

from cte.core.events import (
    STREAM_KEYS,
    OrderbookLevel,
    OrderbookSnapshotEvent,
    RawOrderbookEvent,
    RawTradeEvent,
    Side,
    Symbol,
    TradeEvent,
)
from cte.core.exceptions import DataValidationError, NormalizationError

if TYPE_CHECKING:
    from cte.core.streams import StreamPublisher

logger = structlog.get_logger(__name__)

normalize_total = Counter(
    "cte_normalize_total", "Total normalization attempts", ["venue", "event_type"]
)
normalize_errors = Counter(
    "cte_normalize_errors_total", "Total normalization errors", ["venue", "error_type"]
)
normalize_latency = Histogram(
    "cte_normalize_latency_seconds", "Normalization processing time", ["event_type"]
)

SYMBOL_MAP: dict[str, Symbol] = {
    "BTCUSDT": Symbol.BTCUSDT,
    "ETHUSDT": Symbol.ETHUSDT,
}


class EventNormalizer:
    """Transforms raw venue events into canonical CTE events."""

    def __init__(self, publisher: StreamPublisher) -> None:
        self._publisher = publisher

    async def normalize_trade(self, raw: RawTradeEvent) -> TradeEvent | None:
        """Normalize a raw trade event into canonical format."""
        normalize_total.labels(venue=raw.venue.value, event_type="trade").inc()

        symbol = self._resolve_symbol(raw.symbol_raw)
        if symbol is None:
            normalize_errors.labels(venue=raw.venue.value, error_type="unknown_symbol").inc()
            return None

        try:
            price = Decimal(raw.price)
            quantity = Decimal(raw.quantity)
        except (InvalidOperation, ValueError) as e:
            normalize_errors.labels(venue=raw.venue.value, error_type="decimal_parse").inc()
            raise DataValidationError(
                f"Invalid decimal in trade: price={raw.price}, qty={raw.quantity}",
                context={"venue": raw.venue.value, "symbol": raw.symbol_raw},
            ) from e

        if price <= 0 or quantity <= 0:
            normalize_errors.labels(venue=raw.venue.value, error_type="invalid_value").inc()
            raise DataValidationError(
                f"Non-positive value: price={price}, qty={quantity}",
                context={"venue": raw.venue.value},
            )

        side = Side.SELL if raw.is_buyer_maker else Side.BUY
        trade_time = datetime.fromtimestamp(raw.trade_time / 1000, tz=UTC)

        event = TradeEvent(
            venue=raw.venue,
            symbol=symbol,
            price=price,
            quantity=quantity,
            side=side,
            trade_time=trade_time,
            venue_trade_id=raw.trade_id,
        )

        await self._publisher.publish(STREAM_KEYS["trade"], event)
        return event

    async def normalize_orderbook(
        self, raw: RawOrderbookEvent
    ) -> OrderbookSnapshotEvent | None:
        """Normalize a raw orderbook event into canonical format."""
        normalize_total.labels(venue=raw.venue.value, event_type="orderbook").inc()

        symbol = self._resolve_symbol(raw.symbol_raw)
        if symbol is None:
            normalize_errors.labels(venue=raw.venue.value, error_type="unknown_symbol").inc()
            return None

        try:
            bids = [
                OrderbookLevel(price=Decimal(b[0]), quantity=Decimal(b[1]))
                for b in raw.bids
                if len(b) >= 2
            ]
            asks = [
                OrderbookLevel(price=Decimal(a[0]), quantity=Decimal(a[1]))
                for a in raw.asks
                if len(a) >= 2
            ]
        except (InvalidOperation, ValueError, IndexError) as e:
            normalize_errors.labels(venue=raw.venue.value, error_type="orderbook_parse").inc()
            raise NormalizationError(
                f"Failed to parse orderbook levels: {e}",
                context={"venue": raw.venue.value, "symbol": raw.symbol_raw},
            ) from e

        snapshot_time = datetime.fromtimestamp(
            raw.venue_timestamp / 1000, tz=UTC
        ) if raw.venue_timestamp > 0 else raw.timestamp

        event = OrderbookSnapshotEvent(
            venue=raw.venue,
            symbol=symbol,
            bids=bids,
            asks=asks,
            sequence=raw.update_id,
            snapshot_time=snapshot_time,
        )

        await self._publisher.publish(STREAM_KEYS["orderbook"], event)
        return event

    @staticmethod
    def _resolve_symbol(raw_symbol: str) -> Symbol | None:
        """Map venue symbol string to canonical Symbol enum."""
        cleaned = raw_symbol.upper().replace("-", "").replace("_", "")
        return SYMBOL_MAP.get(cleaned)
