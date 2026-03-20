"""Data types for the streaming feature engine.

SecondBucket is the core data structure: one calendar second of aggregated
market data. This avoids storing individual trades (BTC sees 100+ trades/sec)
and gives O(1) per-event updates with O(1) window eviction.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import inf

WINDOW_SECONDS: tuple[int, ...] = (10, 30, 60, 300)

WINDOW_LABELS: dict[int, str] = {
    10: "10s",
    30: "30s",
    60: "60s",
    300: "5m",
}

# Z-score history depths: how many past window-returns to keep per timeframe.
# 10s window → 180 past values = 30 minutes of history
# 30s window → 120 past values = 60 minutes
# 60s window → 120 past values = 2 hours
# 5m window  → 48 past values  = 4 hours
ZSCORE_HISTORY_DEPTH: dict[int, int] = {
    10: 180,
    30: 120,
    60: 120,
    300: 48,
}

# Minimum samples before z-score is considered valid
ZSCORE_MIN_SAMPLES = 10

# Freshness thresholds (ms) — data older than this gets 0 freshness
FRESHNESS_MAX_AGE_MS: dict[str, int] = {
    "trade": 5_000,
    "orderbook": 10_000,
    "venue": 30_000,
}

# Execution feasibility constants
MAX_ACCEPTABLE_SPREAD_BPS = 20.0
TARGET_DEPTH_QTY_BTC = 1.0
TARGET_DEPTH_QTY_ETH = 10.0

# Spread widening: compare current spread to this window's average
# A value of 2.0 means "spread is 2x the recent average"
SPREAD_WIDENING_FLOOR = 0.01  # avoid div by zero if avg spread is ~0


@dataclass(slots=True)
class SecondBucket:
    """Aggregated market data for one calendar second.

    All fields are mutable during the current second, then frozen
    once the second boundary is crossed and the bucket is pushed
    into the rolling window.
    """

    ts: int = 0  # unix second

    # ── Trade aggregates ──────────────────────────────────────
    open_price: float = 0.0
    close_price: float = 0.0
    high_price: float = -inf
    low_price: float = inf
    pq_sum: float = 0.0        # Σ(price x qty) for VWAP
    volume: float = 0.0         # Σ qty
    buy_volume: float = 0.0     # Σ qty where taker is buyer
    sell_volume: float = 0.0    # Σ qty where taker is seller
    trade_count: int = 0

    # ── Spread tracking ───────────────────────────────────────
    spread_bps_sum: float = 0.0
    spread_count: int = 0
    last_spread_bps: float = 0.0

    # ── Orderbook L1 ─────────────────────────────────────────
    last_bid_qty: float = 0.0   # sum of bid-side depth
    last_ask_qty: float = 0.0   # sum of ask-side depth
    ob_bid_qty_sum: float = 0.0 # Σ bid_qty across snapshots
    ob_ask_qty_sum: float = 0.0 # Σ ask_qty across snapshots
    ob_count: int = 0

    # ── Liquidations ──────────────────────────────────────────
    liq_long_vol: float = 0.0
    liq_short_vol: float = 0.0
    liq_count: int = 0

    # ── Mark price ────────────────────────────────────────────
    last_mark_price: float = 0.0
    mark_count: int = 0

    def add_trade(self, price: float, qty: float, is_buy: bool) -> None:
        if self.trade_count == 0:
            self.open_price = price
            self.high_price = price
            self.low_price = price
        self.close_price = price
        if price > self.high_price:
            self.high_price = price
        if price < self.low_price:
            self.low_price = price
        self.pq_sum += price * qty
        self.volume += qty
        if is_buy:
            self.buy_volume += qty
        else:
            self.sell_volume += qty
        self.trade_count += 1

    def add_spread(self, spread_bps: float) -> None:
        self.spread_bps_sum += spread_bps
        self.spread_count += 1
        self.last_spread_bps = spread_bps

    def add_orderbook(self, bid_qty_total: float, ask_qty_total: float) -> None:
        self.last_bid_qty = bid_qty_total
        self.last_ask_qty = ask_qty_total
        self.ob_bid_qty_sum += bid_qty_total
        self.ob_ask_qty_sum += ask_qty_total
        self.ob_count += 1

    def add_liquidation(self, qty: float, is_long_liq: bool) -> None:
        """is_long_liq=True means a long position was liquidated (bearish signal)."""
        if is_long_liq:
            self.liq_long_vol += qty
        else:
            self.liq_short_vol += qty
        self.liq_count += 1

    def add_mark_price(self, price: float) -> None:
        self.last_mark_price = price
        self.mark_count += 1

    @property
    def is_empty(self) -> bool:
        return self.trade_count == 0 and self.ob_count == 0 and self.liq_count == 0

    @property
    def vwap(self) -> float:
        if self.volume <= 0:
            return self.close_price
        return self.pq_sum / self.volume

    @property
    def avg_spread_bps(self) -> float:
        if self.spread_count == 0:
            return 0.0
        return self.spread_bps_sum / self.spread_count

    def copy(self) -> SecondBucket:
        """Shallow copy for snapshot purposes."""
        b = SecondBucket.__new__(SecondBucket)
        for attr in SecondBucket.__slots__:
            setattr(b, attr, getattr(self, attr))
        return b


@dataclass(slots=True)
class VenueState:
    """Per-venue tracking for cross-venue divergence."""

    last_mid: float = 0.0
    last_bid: float = 0.0
    last_ask: float = 0.0
    last_price: float = 0.0
    last_update_ms: int = 0  # epoch ms of last data from this venue

    def update_book(self, bid: float, ask: float, ts_ms: int) -> None:
        self.last_bid = bid
        self.last_ask = ask
        self.last_mid = (bid + ask) / 2.0
        self.last_update_ms = ts_ms

    def update_trade(self, price: float, ts_ms: int) -> None:
        self.last_price = price
        self.last_update_ms = ts_ms

    @property
    def is_stale(self) -> bool:
        return self.last_update_ms == 0


def empty_bucket(ts: int) -> SecondBucket:
    """Create a properly initialized empty bucket for a given second."""
    return SecondBucket(ts=ts)
