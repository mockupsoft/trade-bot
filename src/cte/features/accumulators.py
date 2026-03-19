"""Incremental accumulator data structures for O(1) window operations.

The key insight: instead of storing individual trades and recomputing
everything, we aggregate into 1-second buckets and maintain running
totals. When a bucket exits a window, we subtract its values.
Cost: O(1) per event, O(1) per window tick.

Memory per symbol: 300 SecondBuckets (5m) × ~200 bytes = 60KB.
Compare: raw trade storage at 100 trades/sec × 5m = 30,000 records.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from cte.features.types import SecondBucket


@dataclass(slots=True)
class RunningTotals:
    """Incrementally maintained aggregates for a single window.

    Add() when a bucket enters, subtract() when it exits.
    All operations are O(1).
    """

    pq_sum: float = 0.0          # Σ(price × qty) for VWAP
    volume: float = 0.0           # Σ qty
    buy_volume: float = 0.0       # Σ buy qty (taker flow)
    sell_volume: float = 0.0      # Σ sell qty (taker flow)
    trade_count: int = 0

    spread_bps_sum: float = 0.0
    spread_count: int = 0

    ob_bid_qty_sum: float = 0.0
    ob_ask_qty_sum: float = 0.0
    ob_count: int = 0

    liq_long_vol: float = 0.0
    liq_short_vol: float = 0.0
    liq_count: int = 0

    # Number of non-empty seconds in the window (for fill-rate calculation)
    active_seconds: int = 0

    def add(self, b: SecondBucket) -> None:
        self.pq_sum += b.pq_sum
        self.volume += b.volume
        self.buy_volume += b.buy_volume
        self.sell_volume += b.sell_volume
        self.trade_count += b.trade_count
        self.spread_bps_sum += b.spread_bps_sum
        self.spread_count += b.spread_count
        self.ob_bid_qty_sum += b.ob_bid_qty_sum
        self.ob_ask_qty_sum += b.ob_ask_qty_sum
        self.ob_count += b.ob_count
        self.liq_long_vol += b.liq_long_vol
        self.liq_short_vol += b.liq_short_vol
        self.liq_count += b.liq_count
        if not b.is_empty:
            self.active_seconds += 1

    def subtract(self, b: SecondBucket) -> None:
        self.pq_sum -= b.pq_sum
        self.volume -= b.volume
        self.buy_volume -= b.buy_volume
        self.sell_volume -= b.sell_volume
        self.trade_count -= b.trade_count
        self.spread_bps_sum -= b.spread_bps_sum
        self.spread_count -= b.spread_count
        self.ob_bid_qty_sum -= b.ob_bid_qty_sum
        self.ob_ask_qty_sum -= b.ob_ask_qty_sum
        self.ob_count -= b.ob_count
        self.liq_long_vol -= b.liq_long_vol
        self.liq_short_vol -= b.liq_short_vol
        self.liq_count -= b.liq_count
        if not b.is_empty:
            self.active_seconds -= 1


class WindowState:
    """A single timeframe window backed by a bounded deque of SecondBuckets.

    Maintains running totals that are always in sync with the deque contents.
    When the deque is full and a new bucket is appended, the oldest bucket
    is auto-evicted and subtracted from the totals.
    """

    __slots__ = ("max_seconds", "buckets", "totals")

    def __init__(self, max_seconds: int) -> None:
        self.max_seconds = max_seconds
        self.buckets: deque[SecondBucket] = deque(maxlen=max_seconds)
        self.totals = RunningTotals()

    def push(self, bucket: SecondBucket) -> None:
        """Push a finalized second bucket into the window.

        If the deque is full, the oldest bucket is evicted and its
        values subtracted from the running totals before the new one
        is added. Total cost: O(1).
        """
        if len(self.buckets) == self.max_seconds:
            evicted = self.buckets[0]
            self.totals.subtract(evicted)
        self.buckets.append(bucket)
        self.totals.add(bucket)

    def first_price(self) -> float | None:
        """Open price of the earliest non-empty bucket in the window."""
        for b in self.buckets:
            if b.trade_count > 0:
                return b.open_price
        return None

    def last_price(self) -> float | None:
        """Close price of the most recent non-empty bucket."""
        for b in reversed(self.buckets):
            if b.trade_count > 0:
                return b.close_price
        return None

    def latest_spread_bps(self) -> float | None:
        """Most recent spread reading from any bucket."""
        for b in reversed(self.buckets):
            if b.spread_count > 0:
                return b.last_spread_bps
        return None

    def latest_ob_snapshot(self) -> tuple[float, float] | None:
        """Most recent (bid_qty, ask_qty) from orderbook."""
        for b in reversed(self.buckets):
            if b.ob_count > 0:
                return (b.last_bid_qty, b.last_ask_qty)
        return None

    def latest_mark_price(self) -> float | None:
        """Most recent mark price."""
        for b in reversed(self.buckets):
            if b.mark_count > 0:
                return b.last_mark_price
        return None

    @property
    def fill_pct(self) -> float:
        """Fraction of window seconds that have data."""
        if not self.buckets:
            return 0.0
        return self.totals.active_seconds / len(self.buckets)

    @property
    def size(self) -> int:
        return len(self.buckets)

    @property
    def is_full(self) -> bool:
        return len(self.buckets) == self.max_seconds


class ReturnHistory:
    """Ring buffer of past window-returns for z-score computation.

    Uses running sum/sum-of-squares with periodic full recomputation
    to prevent floating-point drift.

    For a 10s window with depth=180, stores 30 minutes of 10-second returns.
    Memory: 180 × 8 bytes = 1.4KB per timeframe per symbol.
    """

    __slots__ = ("_entries", "_sum", "_sum_sq", "_recompute_counter")

    def __init__(self, max_entries: int) -> None:
        self._entries: deque[float] = deque(maxlen=max_entries)
        self._sum: float = 0.0
        self._sum_sq: float = 0.0
        self._recompute_counter: int = 0

    def push(self, value: float) -> None:
        if len(self._entries) == self._entries.maxlen:
            old = self._entries[0]
            self._sum -= old
            self._sum_sq -= old * old
        self._entries.append(value)
        self._sum += value
        self._sum_sq += value * value

        self._recompute_counter += 1
        if self._recompute_counter >= 500:
            self._recompute()

    def _recompute(self) -> None:
        """Full recomputation to correct floating-point drift."""
        self._sum = sum(self._entries)
        self._sum_sq = sum(x * x for x in self._entries)
        self._recompute_counter = 0

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def mean(self) -> float:
        n = len(self._entries)
        if n == 0:
            return 0.0
        return self._sum / n

    @property
    def std(self) -> float:
        n = len(self._entries)
        if n < 2:
            return 0.0
        mean = self._sum / n
        variance = (self._sum_sq / n) - (mean * mean)
        # Clamp to avoid sqrt of tiny negative from float arithmetic
        return max(0.0, variance) ** 0.5

    def z_score(self, value: float) -> float | None:
        """Compute z-score of value against stored history.

        Returns None if insufficient history or zero variance.
        """
        if self.count < 10 or self.std < 1e-12:
            return None
        return (value - self.mean) / self.std


class MomentumHistory:
    """Separate history for taker-flow-based momentum z-scores.

    Tracks signed flow: (buy_volume - sell_volume) per window period.
    """

    __slots__ = ("_history",)

    def __init__(self, max_entries: int) -> None:
        self._history = ReturnHistory(max_entries)

    def push(self, net_flow: float) -> None:
        self._history.push(net_flow)

    def z_score(self, current_flow: float) -> float | None:
        return self._history.z_score(current_flow)

    @property
    def count(self) -> int:
        return self._history.count
