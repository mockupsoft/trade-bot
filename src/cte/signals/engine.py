"""Signal engine coordinator.

Consumes feature vectors, applies strategies, enforces cooldowns,
and emits signal events to Redis Streams.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog
from prometheus_client import Counter, Gauge, Histogram

from cte.core.events import (
    STREAM_KEYS,
    FeatureVector,
    SignalAction,
    SignalEvent,
    Symbol,
)
from cte.core.settings import SignalSettings
from cte.core.streams import StreamPublisher
from cte.signals.strategies import (
    ema_crossover_strategy,
    rsi_reversal_strategy,
)

logger = structlog.get_logger(__name__)

signal_generated_total = Counter(
    "cte_signal_generated_total", "Total signals generated", ["symbol", "action"]
)
signal_confidence_hist = Histogram(
    "cte_signal_confidence", "Signal confidence distribution", ["symbol"]
)
signal_cooldown_active = Gauge(
    "cte_signal_cooldown_active", "Whether cooldown is active", ["symbol"]
)


class SignalEngine:
    """Evaluates feature vectors against strategies and emits signals."""

    def __init__(
        self,
        settings: SignalSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._last_signal_time: dict[str, float] = {}
        self._signal_count_hour: dict[str, int] = {}
        self._hour_start: dict[str, float] = {}
        self._prev_features: dict[str, FeatureVector] = {}

    async def handle_feature_vector(self, vector: FeatureVector) -> SignalEvent | None:
        """Process a feature vector and potentially emit a signal."""
        symbol = vector.symbol.value

        if self._is_on_cooldown(symbol):
            signal_cooldown_active.labels(symbol=symbol).set(1)
            return None
        signal_cooldown_active.labels(symbol=symbol).set(0)

        if self._hourly_limit_reached(symbol):
            return None

        result = None
        prev = self._prev_features.get(symbol)

        ema_result = ema_crossover_strategy(
            current=vector,
            prev_ema_fast=prev.ema_fast if prev else None,
            prev_ema_slow=prev.ema_slow if prev else None,
        )
        if ema_result and ema_result.confidence >= self._settings.min_confidence:
            result = ema_result

        if result is None:
            rsi_result = rsi_reversal_strategy(vector)
            if rsi_result and rsi_result.confidence >= self._settings.min_confidence:
                result = rsi_result

        self._prev_features[symbol] = vector

        if result is None:
            return None

        signal = SignalEvent(
            symbol=vector.symbol,
            action=result.action,
            confidence=result.confidence,
            reason=result.reason,
            features_snapshot={
                "rsi": vector.rsi,
                "ema_fast": vector.ema_fast,
                "ema_slow": vector.ema_slow,
                "vwap": vector.vwap,
                "volume_24h": vector.volume_24h,
                "orderbook_imbalance": vector.orderbook_imbalance,
            },
        )

        await self._publisher.publish(STREAM_KEYS["signal"], signal)

        signal_generated_total.labels(
            symbol=symbol, action=result.action.value
        ).inc()
        signal_confidence_hist.labels(symbol=symbol).observe(result.confidence)

        self._last_signal_time[symbol] = time.monotonic()
        self._increment_hourly_count(symbol)

        await logger.ainfo(
            "signal_emitted",
            symbol=symbol,
            action=result.action.value,
            confidence=result.confidence,
            primary_trigger=result.reason.primary_trigger,
        )

        return signal

    def _is_on_cooldown(self, symbol: str) -> bool:
        last = self._last_signal_time.get(symbol)
        if last is None:
            return False
        return (time.monotonic() - last) < self._settings.cooldown_seconds

    def _hourly_limit_reached(self, symbol: str) -> bool:
        now = time.monotonic()
        start = self._hour_start.get(symbol, 0)

        if now - start > 3600:
            self._hour_start[symbol] = now
            self._signal_count_hour[symbol] = 0
            return False

        return self._signal_count_hour.get(symbol, 0) >= self._settings.max_signals_per_hour

    def _increment_hourly_count(self, symbol: str) -> None:
        self._signal_count_hour[symbol] = self._signal_count_hour.get(symbol, 0) + 1
