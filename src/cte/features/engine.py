"""Feature engine coordinator.

Consumes normalized market events, computes technical indicators
via rolling windows, and emits feature vectors to Redis Streams.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import structlog
from prometheus_client import Counter, Gauge, Histogram

from cte.core.events import (
    STREAM_KEYS,
    FeatureVector,
    OrderbookSnapshotEvent,
    Symbol,
    TradeEvent,
)
from cte.core.settings import FeatureSettings
from cte.core.streams import StreamPublisher
from cte.features.indicators import (
    bid_ask_spread_bps,
    ema,
    orderbook_imbalance,
    price_change_pct,
    rsi,
    vwap,
)
from cte.features.window import WindowManager

logger = structlog.get_logger(__name__)

feature_compute_total = Counter(
    "cte_feature_compute_total", "Total feature computations", ["symbol"]
)
feature_compute_latency = Histogram(
    "cte_feature_compute_latency_seconds", "Feature compute time", ["symbol"]
)
feature_staleness = Gauge(
    "cte_feature_staleness_seconds", "Seconds since last feature update", ["symbol"]
)


class FeatureEngine:
    """Computes technical indicators from normalized market data."""

    def __init__(
        self,
        settings: FeatureSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._windows = WindowManager(window_minutes=settings.window_size_minutes)
        self._last_emit: dict[str, datetime] = {}

    async def handle_trade(self, event: TradeEvent) -> FeatureVector | None:
        """Process a normalized trade and potentially emit a feature vector."""
        window = self._windows.get_window(event.symbol.value)
        window.add_trade(
            time=event.trade_time,
            price=float(event.price),
            quantity=float(event.quantity),
            side=event.side.value,
        )

        if window.trade_count < self._settings.rsi_period + 1:
            return None

        return await self._compute_and_emit(event.symbol, event.trade_time)

    async def handle_orderbook(self, event: OrderbookSnapshotEvent) -> None:
        """Process a normalized orderbook snapshot."""
        if not event.bids or not event.asks:
            return

        window = self._windows.get_window(event.symbol.value)
        window.add_orderbook(
            time=event.snapshot_time,
            best_bid=float(event.bids[0].price),
            best_ask=float(event.asks[0].price),
            bid_quantities=[float(b.quantity) for b in event.bids],
            ask_quantities=[float(a.quantity) for a in event.asks],
        )

    async def _compute_and_emit(
        self, symbol: Symbol, now: datetime
    ) -> FeatureVector | None:
        """Compute all features and emit vector."""
        window = self._windows.get_window(symbol.value)
        prices = np.array(window.get_prices(), dtype=np.float64)
        volumes = np.array(window.get_volumes(), dtype=np.float64)

        ob = window.latest_orderbook

        feature_compute_total.labels(symbol=symbol.value).inc()

        vector = FeatureVector(
            symbol=symbol,
            window_start=window.trades[0].time if window.trades else now,
            window_end=now,
            rsi=rsi(prices, self._settings.rsi_period),
            ema_fast=ema(prices, self._settings.ema_fast_period),
            ema_slow=ema(prices, self._settings.ema_slow_period),
            vwap=vwap(prices, volumes),
            volume_24h=float(np.sum(volumes)) if len(volumes) > 0 else None,
            price_change_pct_1h=price_change_pct(prices, min(60, len(prices) - 1)),
            bid_ask_spread_bps=(
                bid_ask_spread_bps(ob.best_bid, ob.best_ask) if ob else None
            ),
            orderbook_imbalance=(
                orderbook_imbalance(
                    np.array(ob.bid_quantities, dtype=np.float64),
                    np.array(ob.ask_quantities, dtype=np.float64),
                )
                if ob
                else None
            ),
        )

        await self._publisher.publish(STREAM_KEYS["feature"], vector)
        self._last_emit[symbol.value] = now

        await logger.adebug(
            "feature_emitted",
            symbol=symbol.value,
            rsi=vector.rsi,
            ema_fast=vector.ema_fast,
            ema_slow=vector.ema_slow,
        )

        return vector
