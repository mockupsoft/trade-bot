"""Bybit v5 public WebSocket connector.

Connects to the linear public WebSocket for publicTrade and orderbook data.
Bybit v5 requires explicit subscribe messages and 20s ping keepalive.
"""

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

import orjson
import websockets

from cte.connectors.base import BaseConnector
from cte.core.logging import setup_logging
from cte.core.settings import get_settings
from cte.core.streams import StreamPublisher, create_redis_pool
from cte.core.events import (
    STREAM_KEYS,
    BaseEvent,
    RawOrderbookEvent,
    RawTradeEvent,
    Venue,
)

if TYPE_CHECKING:
    from cte.core.settings import BybitSettings
    from cte.core.streams import StreamPublisher


class BybitConnector(BaseConnector):
    """Bybit v5 public linear WebSocket connector.

    Subscribes to publicTrade and orderbook topics.
    Handles snapshot/delta orderbook model.
    Connection limit: 10 subscriptions per connection, 500 connections per 5min.
    """

    def __init__(
        self,
        settings: BybitSettings,
        publisher: StreamPublisher,
    ) -> None:
        super().__init__(
            venue_name="bybit",
            publisher=publisher,
            reconnect_base=settings.reconnect_base_sec,
            reconnect_max=settings.reconnect_max_sec,
            ping_interval=settings.ping_interval_sec,
        )
        self._settings = settings
        self._topics = settings.topics

    async def _connect(self) -> None:
        self._ws = await websockets.connect(
            self._settings.ws_base_url,
            ping_interval=self._settings.ping_interval_sec,
            ping_timeout=10,
            close_timeout=5,
            max_size=2**20,
        )

    async def _subscribe(self) -> None:
        subscribe_msg = orjson.dumps(
            {
                "op": "subscribe",
                "args": self._topics,
            }
        )
        await self._ws.send(subscribe_msg)

    async def _handle_message(self, raw: str | bytes) -> list[BaseEvent]:
        data = orjson.loads(raw)

        # Bybit sends op responses for subscribe/pong
        if "op" in data:
            return []

        topic = data.get("topic", "")
        msg_data = data.get("data", {})
        msg_type = data.get("type", "")

        if topic.startswith("publicTrade"):
            return self._parse_trades(msg_data)
        elif topic.startswith("orderbook"):
            return [self._parse_orderbook(msg_data, msg_type, data)]
        return []

    def _parse_trades(self, trades: list[dict]) -> list[RawTradeEvent]:
        events = []
        for t in trades:
            events.append(
                RawTradeEvent(
                    venue=Venue.BYBIT,
                    symbol_raw=t["s"],
                    price=t["p"],
                    quantity=t["v"],
                    trade_id=str(t.get("i", "")),
                    trade_time=t["T"],
                    is_buyer_maker=t["S"] == "Sell",
                )
            )
        return events

    def _parse_orderbook(self, data: dict, msg_type: str, full_msg: dict) -> RawOrderbookEvent:
        return RawOrderbookEvent(
            venue=Venue.BYBIT,
            symbol_raw=data.get("s", ""),
            event_type="snapshot" if msg_type == "snapshot" else "delta",
            bids=data.get("b", []),
            asks=data.get("a", []),
            update_id=data.get("u", 0),
            venue_timestamp=full_msg.get("ts", 0),
        )

    def _get_stream_key(self, event: BaseEvent) -> str:
        if isinstance(event, RawTradeEvent):
            return STREAM_KEYS["raw_trade"]
        if isinstance(event, RawOrderbookEvent):
            return STREAM_KEYS["raw_orderbook"]
        return "cte:raw:unknown"


async def _run_connector() -> None:
    setup_logging(service_name="bybit-connector")
    settings = get_settings()
    redis = await create_redis_pool(settings.redis)
    publisher = StreamPublisher(redis, max_len=settings.redis.stream_max_len)
    connector = BybitConnector(settings.bybit, publisher)
    try:
        await connector.start()
    finally:
        await connector.stop()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(_run_connector())
