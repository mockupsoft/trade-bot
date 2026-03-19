"""Binance USDⓈ-M Futures WebSocket connector.

Connects to the combined stream endpoint for trade and depth data.
Uses the new stream URL separation (fstream.binance.com).
"""
from __future__ import annotations

import orjson
import websockets

from cte.core.events import (
    STREAM_KEYS,
    BaseEvent,
    RawOrderbookEvent,
    RawTradeEvent,
    Venue,
)
from cte.core.settings import BinanceSettings
from cte.core.streams import StreamPublisher
from cte.connectors.base import BaseConnector


class BinanceConnector(BaseConnector):
    """Binance USDⓈ-M Futures WebSocket connector.

    Subscribes to combined streams: {symbol}@trade and {symbol}@depth20@100ms.
    Binance WS docs: https://binance-docs.github.io/apidocs/futures/en/
    """

    def __init__(
        self,
        settings: BinanceSettings,
        publisher: StreamPublisher,
    ) -> None:
        super().__init__(
            venue_name="binance",
            publisher=publisher,
            reconnect_base=settings.reconnect_base_sec,
            reconnect_max=settings.reconnect_max_sec,
            ping_interval=settings.ping_interval_sec,
        )
        self._settings = settings
        self._streams = settings.streams

    def _build_url(self) -> str:
        stream_path = "/".join(self._streams)
        return f"{self._settings.ws_combined_url}?streams={stream_path}"

    async def _connect(self) -> None:
        url = self._build_url()
        self._ws = await websockets.connect(
            url,
            ping_interval=self._settings.ping_interval_sec,
            ping_timeout=10,
            close_timeout=5,
            max_size=2**20,  # 1MB
        )

    async def _subscribe(self) -> None:
        # Combined stream URL auto-subscribes; no explicit subscribe needed.
        pass

    async def _handle_message(self, raw: str | bytes) -> list[BaseEvent]:
        data = orjson.loads(raw)
        stream = data.get("stream", "")
        payload = data.get("data", {})

        if "@trade" in stream and "depth" not in stream:
            return [self._parse_trade(payload)]
        elif "@depth" in stream:
            return [self._parse_orderbook(payload)]
        return []

    def _parse_trade(self, d: dict) -> RawTradeEvent:
        return RawTradeEvent(
            venue=Venue.BINANCE,
            symbol_raw=d["s"],
            price=str(d["p"]),
            quantity=str(d["q"]),
            trade_id=str(d["t"]),
            trade_time=d["T"],
            is_buyer_maker=d["m"],
        )

    def _parse_orderbook(self, d: dict) -> RawOrderbookEvent:
        return RawOrderbookEvent(
            venue=Venue.BINANCE,
            symbol_raw=d.get("s", ""),
            event_type="snapshot",
            bids=d.get("b", d.get("bids", [])),
            asks=d.get("a", d.get("asks", [])),
            update_id=d.get("u", d.get("lastUpdateId", 0)),
            venue_timestamp=d.get("T", d.get("E", 0)),
        )

    def _get_stream_key(self, event: BaseEvent) -> str:
        if isinstance(event, RawTradeEvent):
            return STREAM_KEYS["raw_trade"]
        if isinstance(event, RawOrderbookEvent):
            return STREAM_KEYS["raw_orderbook"]
        return "cte:raw:unknown"
