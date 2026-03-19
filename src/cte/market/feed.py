"""Live market data feed service.

Connects to Binance USDS-M Futures WebSocket for real-time
trade and orderbook data. Tracks connection health, latency,
message rates, and staleness.

This feeds the dashboard and feature engine with REAL market data
instead of seed/fake data.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal

import orjson
import structlog
import websockets

logger = structlog.get_logger(__name__)

BINANCE_COMBINED_STREAM = "wss://stream.binancefuture.com/stream"
DEFAULT_STREAMS = [
    "btcusdt@trade",
    "btcusdt@depth5@100ms",
    "btcusdt@markPrice@1s",
    "ethusdt@trade",
    "ethusdt@depth5@100ms",
    "ethusdt@markPrice@1s",
]


@dataclass
class TickerState:
    """Latest ticker data for a symbol."""

    symbol: str = ""
    last_price: Decimal = Decimal("0")
    best_bid: Decimal = Decimal("0")
    best_ask: Decimal = Decimal("0")
    mark_price: Decimal = Decimal("0")
    bid_qty: Decimal = Decimal("0")
    ask_qty: Decimal = Decimal("0")
    last_trade_time_ms: int = 0
    last_update_ms: int = 0
    trade_count_1m: int = 0
    volume_1m: Decimal = Decimal("0")

    @property
    def spread_bps(self) -> float:
        if self.best_bid <= 0 or self.best_ask <= 0:
            return 0.0
        mid = (self.best_bid + self.best_ask) / 2
        return float((self.best_ask - self.best_bid) / mid * 10000)

    @property
    def age_ms(self) -> int:
        if self.last_update_ms == 0:
            return 999999
        return int(time.time() * 1000) - self.last_update_ms

    @property
    def is_stale(self) -> bool:
        return self.age_ms > 5000


@dataclass
class FeedHealth:
    """Health status of the market data feed."""

    connected: bool = False
    last_message_ms: int = 0
    messages_total: int = 0
    reconnect_count: int = 0
    errors_total: int = 0
    latency_ms: float = 0.0
    uptime_seconds: float = 0.0
    symbols: dict[str, dict] = field(default_factory=dict)


class MarketDataFeed:
    """Live WebSocket market data feed from Binance Futures.

    Maintains per-symbol TickerState with best bid/ask, last price,
    mark price, and health metrics.
    """

    def __init__(
        self,
        ws_url: str | None = None,
        streams: list[str] | None = None,
    ) -> None:
        env_ws = (os.environ.get("CTE_MARKET_WS_URL") or "").strip()
        self._ws_url = ws_url or (env_ws if env_ws else BINANCE_COMBINED_STREAM)
        self._streams = streams or DEFAULT_STREAMS
        self._ws = None
        self._running = False
        self._start_time: float = 0

        self._tickers: dict[str, TickerState] = {
            "BTCUSDT": TickerState(symbol="BTCUSDT"),
            "ETHUSDT": TickerState(symbol="ETHUSDT"),
        }
        self._health = FeedHealth()

    @property
    def tickers(self) -> dict[str, TickerState]:
        return self._tickers

    def get_ticker(self, symbol: str) -> TickerState | None:
        return self._tickers.get(symbol.upper())

    @property
    def health(self) -> FeedHealth:
        h = self._health
        h.uptime_seconds = time.monotonic() - self._start_time if self._start_time else 0
        h.symbols = {
            sym: {
                "last_price": str(t.last_price),
                "best_bid": str(t.best_bid),
                "best_ask": str(t.best_ask),
                "mark_price": str(t.mark_price),
                "spread_bps": round(t.spread_bps, 2),
                "age_ms": t.age_ms,
                "is_stale": t.is_stale,
                "trade_count_1m": t.trade_count_1m,
            }
            for sym, t in self._tickers.items()
        }
        return h

    async def start(self) -> None:
        """Start the WebSocket feed with auto-reconnection."""
        self._running = True
        self._start_time = time.monotonic()

        while self._running:
            try:
                url = self._build_url()
                await logger.ainfo("market_feed_connecting", url=url[:60])

                async with websockets.connect(
                    url, ping_interval=180, ping_timeout=10, close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._health.connected = True
                    await logger.ainfo("market_feed_connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        self._process_message(raw)

            except asyncio.CancelledError:
                break
            except Exception:
                self._health.connected = False
                self._health.reconnect_count += 1
                self._health.errors_total += 1
                await logger.aexception("market_feed_error", reconnect=self._health.reconnect_count)
                if self._running:
                    await asyncio.sleep(min(2 ** self._health.reconnect_count, 30))

        self._health.connected = False

    def stop(self) -> None:
        self._running = False

    def _build_url(self) -> str:
        stream_path = "/".join(self._streams)
        return f"{self._ws_url}?streams={stream_path}"

    def _process_message(self, raw: str | bytes) -> None:
        """Parse a Binance combined stream message and update ticker state."""
        now_ms = int(time.time() * 1000)
        self._health.last_message_ms = now_ms
        self._health.messages_total += 1

        try:
            data = orjson.loads(raw)
            stream = data.get("stream", "")
            payload = data.get("data", {})

            symbol_raw = ""
            if "@" in stream:
                symbol_raw = stream.split("@")[0].upper()

            ticker = self._tickers.get(symbol_raw)
            if not ticker:
                return

            if "@trade" in stream and "depth" not in stream:
                self._handle_trade(ticker, payload, now_ms)
            elif "@depth" in stream:
                self._handle_depth(ticker, payload, now_ms)
            elif "@markPrice" in stream:
                self._handle_mark_price(ticker, payload, now_ms)

            # Latency estimate from venue timestamp
            venue_ts = payload.get("T") or payload.get("E") or 0
            if venue_ts:
                self._health.latency_ms = now_ms - venue_ts

        except Exception:
            self._health.errors_total += 1

    def _handle_trade(self, ticker: TickerState, d: dict, now_ms: int) -> None:
        ticker.last_price = Decimal(str(d.get("p", "0")))
        ticker.last_trade_time_ms = d.get("T", now_ms)
        ticker.last_update_ms = now_ms
        ticker.trade_count_1m += 1
        ticker.volume_1m += Decimal(str(d.get("q", "0")))

    def _handle_depth(self, ticker: TickerState, d: dict, now_ms: int) -> None:
        bids = d.get("b", d.get("bids", []))
        asks = d.get("a", d.get("asks", []))
        if bids:
            ticker.best_bid = Decimal(str(bids[0][0]))
            ticker.bid_qty = Decimal(str(bids[0][1]))
        if asks:
            ticker.best_ask = Decimal(str(asks[0][0]))
            ticker.ask_qty = Decimal(str(asks[0][1]))
        ticker.last_update_ms = now_ms

    def _handle_mark_price(self, ticker: TickerState, d: dict, now_ms: int) -> None:
        ticker.mark_price = Decimal(str(d.get("p", "0")))
        ticker.last_update_ms = now_ms
