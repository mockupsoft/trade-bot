"""Rolling window management for feature computation.

Maintains in-memory sliding windows of market data for efficient
indicator calculation. Supports snapshotting to DB for crash recovery.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class TradeRecord:
    """Single trade record for rolling window."""

    time: datetime
    price: float
    quantity: float
    side: str


@dataclass
class OrderbookRecord:
    """Single orderbook snapshot for rolling window."""

    time: datetime
    best_bid: float
    best_ask: float
    bid_quantities: list[float]
    ask_quantities: list[float]


class RollingWindow:
    """Time-based rolling window for market data.

    Maintains a deque of records within a configurable time window.
    Expired records are evicted on each insert.
    """

    def __init__(self, window_minutes: int = 240) -> None:
        self.window_minutes = window_minutes
        self.trades: deque[TradeRecord] = deque()
        self.orderbooks: deque[OrderbookRecord] = deque()
        self._last_update: datetime | None = None

    def add_trade(self, time: datetime, price: float, quantity: float, side: str) -> None:
        self.trades.append(TradeRecord(time=time, price=price, quantity=quantity, side=side))
        self._last_update = time
        self._evict_expired(time)

    def add_orderbook(
        self,
        time: datetime,
        best_bid: float,
        best_ask: float,
        bid_quantities: list[float],
        ask_quantities: list[float],
    ) -> None:
        self.orderbooks.append(OrderbookRecord(
            time=time,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_quantities=bid_quantities,
            ask_quantities=ask_quantities,
        ))
        self._last_update = time
        self._evict_expired(time)

    def _evict_expired(self, current_time: datetime) -> None:
        """Remove records older than window_minutes from current_time."""
        from datetime import timedelta
        cutoff = current_time - timedelta(minutes=self.window_minutes)

        while self.trades and self.trades[0].time < cutoff:
            self.trades.popleft()

        while self.orderbooks and self.orderbooks[0].time < cutoff:
            self.orderbooks.popleft()

    def get_prices(self) -> list[float]:
        return [t.price for t in self.trades]

    def get_volumes(self) -> list[float]:
        return [t.quantity for t in self.trades]

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def last_update(self) -> datetime | None:
        return self._last_update

    @property
    def latest_orderbook(self) -> OrderbookRecord | None:
        return self.orderbooks[-1] if self.orderbooks else None

    def snapshot(self) -> dict:
        """Create a serializable snapshot for DB persistence."""
        return {
            "window_minutes": self.window_minutes,
            "trade_count": self.trade_count,
            "orderbook_count": len(self.orderbooks),
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "oldest_trade": self.trades[0].time.isoformat() if self.trades else None,
            "newest_trade": self.trades[-1].time.isoformat() if self.trades else None,
        }


@dataclass
class WindowManager:
    """Manages rolling windows per symbol."""

    window_minutes: int = 240
    _windows: dict[str, RollingWindow] = field(default_factory=dict)

    def get_window(self, symbol: str) -> RollingWindow:
        if symbol not in self._windows:
            self._windows[symbol] = RollingWindow(window_minutes=self.window_minutes)
        return self._windows[symbol]

    @property
    def symbols(self) -> list[str]:
        return list(self._windows.keys())
