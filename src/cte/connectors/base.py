"""Base WebSocket connector with reconnection and health tracking."""
from __future__ import annotations

import asyncio
import random
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter, Gauge, Histogram

if TYPE_CHECKING:
    from cte.core.events import BaseEvent
    from cte.core.streams import StreamPublisher

logger = structlog.get_logger(__name__)


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


# Prometheus metrics shared across all connectors
ws_messages_total = Counter(
    "cte_ws_messages_total", "Total WebSocket messages received", ["venue", "stream"]
)
ws_connection_state = Gauge(
    "cte_ws_connection_state", "WebSocket connection state (1=connected)", ["venue"]
)
ws_reconnect_total = Counter(
    "cte_ws_reconnects_total", "Total WebSocket reconnection attempts", ["venue"]
)
ws_message_latency = Histogram(
    "cte_ws_message_latency_seconds",
    "Latency between venue timestamp and receive time",
    ["venue"],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)


class BaseConnector(ABC):
    """Abstract base for all venue WebSocket connectors.

    Handles reconnection with exponential backoff + jitter,
    health tracking, and metric emission.
    """

    def __init__(
        self,
        venue_name: str,
        publisher: StreamPublisher,
        reconnect_base: float = 1.0,
        reconnect_max: float = 60.0,
        ping_interval: int = 180,
    ) -> None:
        self.venue_name = venue_name
        self.publisher = publisher
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.ping_interval = ping_interval

        self.state = ConnectionState.DISCONNECTED
        self._ws = None
        self._reconnect_count = 0
        self._last_message_time: float = 0
        self._running = False
        self._tasks: list[asyncio.Task] = []

    @abstractmethod
    async def _connect(self) -> None:
        """Establish WebSocket connection."""

    @abstractmethod
    async def _subscribe(self) -> None:
        """Send subscription messages after connecting."""

    @abstractmethod
    async def _handle_message(self, raw: str | bytes) -> list[BaseEvent]:
        """Parse raw WS message into CTE events."""

    @abstractmethod
    def _get_stream_key(self, event: BaseEvent) -> str:
        """Determine Redis stream key for an event."""

    async def start(self) -> None:
        """Start the connector with automatic reconnection."""
        self._running = True
        while self._running:
            try:
                self.state = ConnectionState.CONNECTING
                await self._connect()
                await self._subscribe()
                self.state = ConnectionState.CONNECTED
                ws_connection_state.labels(venue=self.venue_name).set(1)
                self._reconnect_count = 0

                await logger.ainfo(
                    "connector_connected",
                    venue=self.venue_name,
                )

                await self._read_loop()

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception:
                ws_connection_state.labels(venue=self.venue_name).set(0)
                await logger.aexception(
                    "connector_error",
                    venue=self.venue_name,
                    reconnect_count=self._reconnect_count,
                )

                if not self._running:
                    break

                self.state = ConnectionState.RECONNECTING
                ws_reconnect_total.labels(venue=self.venue_name).inc()
                delay = self._backoff_delay()
                await logger.ainfo(
                    "connector_reconnecting",
                    venue=self.venue_name,
                    delay_sec=delay,
                    attempt=self._reconnect_count,
                )
                await asyncio.sleep(delay)
                self._reconnect_count += 1

    async def _read_loop(self) -> None:
        """Read messages from WebSocket and publish to Redis."""
        while self._running and self._ws:
            try:
                raw = await self._ws.recv()
                self._last_message_time = time.monotonic()
                events = await self._handle_message(raw)
                for event in events:
                    stream_key = self._get_stream_key(event)
                    await self.publisher.publish(stream_key, event)
                    ws_messages_total.labels(
                        venue=self.venue_name, stream=stream_key
                    ).inc()
            except asyncio.CancelledError:
                break
            except Exception:
                await logger.aexception(
                    "message_handling_error",
                    venue=self.venue_name,
                )
                raise

    def _backoff_delay(self) -> float:
        """Exponential backoff with jitter."""
        exp_delay = min(
            self.reconnect_base * (2 ** self._reconnect_count),
            self.reconnect_max,
        )
        jitter = random.uniform(0, exp_delay * 0.1)
        return exp_delay + jitter

    async def stop(self) -> None:
        """Gracefully stop the connector."""
        self._running = False
        if self._ws:
            await self._ws.close()
        self.state = ConnectionState.DISCONNECTED
        ws_connection_state.labels(venue=self.venue_name).set(0)
        await logger.ainfo("connector_stopped", venue=self.venue_name)

    @property
    def is_healthy(self) -> bool:
        return (
            self.state == ConnectionState.CONNECTED
            and (time.monotonic() - self._last_message_time) < self.ping_interval * 2
        )

    def health_status(self) -> dict:
        return {
            "venue": self.venue_name,
            "state": self.state.value,
            "reconnect_count": self._reconnect_count,
            "last_message_age_sec": (
                round(time.monotonic() - self._last_message_time, 2)
                if self._last_message_time > 0
                else None
            ),
            "healthy": self.is_healthy,
        }
