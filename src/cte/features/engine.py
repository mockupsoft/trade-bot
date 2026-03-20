"""Streaming Feature Engine — event-driven, incremental, multi-timeframe.

Replaces the old batch-recompute FeatureEngine with a design that:
- Aggregates events into 1-second buckets (not individual trades)
- Maintains O(1) running totals per window via add/subtract
- Computes z-scores from windowed return/momentum history
- Tracks per-venue state for cross-venue divergence
- Emits StreamingFeatureVector once per second per symbol

Tick Model (deterministic, replay-safe):
  Events arrive → bucketed into current second → on second boundary:
    1. Finalize current bucket
    2. Insert empty buckets for any skipped seconds
    3. Push into all window states
    4. Compute features from window accumulators
    5. Update z-score histories
    6. Emit StreamingFeatureVector to Redis
    7. Periodically persist to TimescaleDB

The old FeatureEngine and indicators.py are preserved for backward
compatibility with the signal engine (which consumes FeatureVector).
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from prometheus_client import Counter, Gauge, Histogram

from cte.core.events import (
    STREAM_KEYS,
    DataQuality,
    FreshnessScore,
    LiquidationEvent,
    MarkPriceEvent,
    OrderbookSnapshotEvent,
    StreamingFeatureVector,
    Symbol,
    TimeframeFeatures,
    TradeEvent,
    WhaleAlertEvent,
)
from cte.features.accumulators import MomentumHistory, ReturnHistory, WindowState
from cte.features.formulas import (
    compute_execution_feasibility,
    compute_freshness,
    compute_liquidation_imbalance,
    compute_momentum_z,
    compute_ob_imbalance,
    compute_returns,
    compute_returns_z,
    compute_spread_bps,
    compute_spread_widening,
    compute_taker_flow_imbalance,
    compute_urgent_news_flag,
    compute_venue_divergence_bps,
    compute_vwap,
    compute_whale_risk_flag,
)
from cte.features.types import (
    WINDOW_LABELS,
    WINDOW_SECONDS,
    ZSCORE_HISTORY_DEPTH,
    SecondBucket,
    VenueState,
    empty_bucket,
)

if TYPE_CHECKING:
    from cte.core.settings import FeatureSettings
    from cte.core.streams import StreamPublisher

logger = structlog.get_logger(__name__)

# Prometheus metrics
sf_ticks_total = Counter("cte_sf_ticks_total", "Total second-boundary ticks", ["symbol"])
sf_events_total = Counter(
    "cte_sf_events_total", "Total events processed", ["symbol", "event_type"]
)
sf_emit_total = Counter("cte_sf_emit_total", "Total feature vectors emitted", ["symbol"])
sf_compute_latency = Histogram(
    "cte_sf_compute_latency_seconds", "Feature computation time per tick", ["symbol"]
)
sf_window_fill = Gauge(
    "cte_sf_window_fill_pct", "Window fill percentage", ["symbol", "window"]
)


class SymbolState:
    """All mutable state for one symbol's feature computation.

    Holds window states, z-score histories, venue states, and context flags.
    Memory per symbol: ~100KB (dominated by 300 SecondBuckets x 4 windows,
    though shorter windows share the same bucket objects).
    """

    def __init__(self, symbol: str, window_sizes: tuple[int, ...] = WINDOW_SECONDS) -> None:
        self.symbol = symbol

        # One WindowState per timeframe
        self.windows: dict[int, WindowState] = {
            w: WindowState(max_seconds=w) for w in window_sizes
        }

        # Z-score histories per timeframe
        self.return_history: dict[int, ReturnHistory] = {
            w: ReturnHistory(max_entries=ZSCORE_HISTORY_DEPTH.get(w, 120))
            for w in window_sizes
        }
        self.momentum_history: dict[int, MomentumHistory] = {
            w: MomentumHistory(max_entries=ZSCORE_HISTORY_DEPTH.get(w, 120))
            for w in window_sizes
        }

        # Per-venue state
        self.venues: dict[str, VenueState] = {
            "binance": VenueState(),
            "bybit": VenueState(),
        }

        # Current second bucket (being filled)
        self.current_bucket: SecondBucket | None = None
        self.current_second: int = 0

        # Freshness timestamps (epoch ms)
        self.last_trade_ms: int = 0
        self.last_ob_ms: int = 0

        # Context flags
        self.last_whale_event_ms: int = 0
        self.last_news_event_ms: int = 0

        # Latest raw values
        self.last_price: float = 0.0
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_mark_price: float = 0.0

        # Warmup tracking
        self.total_ticks: int = 0

    @property
    def warmup_complete(self) -> bool:
        largest_window = max(self.windows.keys())
        return self.windows[largest_window].is_full


class StreamingFeatureEngine:
    """Event-driven streaming feature engine with incremental computation."""

    def __init__(
        self,
        settings: FeatureSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._symbols: dict[str, SymbolState] = {}

    def _get_state(self, symbol: str) -> SymbolState:
        if symbol not in self._symbols:
            window_sizes = tuple(self._settings.streaming_windows)
            self._symbols[symbol] = SymbolState(symbol, window_sizes)
        return self._symbols[symbol]

    # ── Event Handlers ────────────────────────────────────────────

    async def handle_trade(self, event: TradeEvent) -> StreamingFeatureVector | None:
        """Ingest a normalized trade event."""
        state = self._get_state(event.symbol.value)
        ts_ms = int(event.trade_time.timestamp() * 1000)
        ts_sec = ts_ms // 1000

        sf_events_total.labels(symbol=event.symbol.value, event_type="trade").inc()

        # Update venue state
        venue_name = event.venue.value
        if venue_name in state.venues:
            state.venues[venue_name].update_trade(float(event.price), ts_ms)

        state.last_trade_ms = ts_ms
        state.last_price = float(event.price)

        result = self._advance_to_second(state, ts_sec)

        bucket = state.current_bucket
        assert bucket is not None
        is_buy = event.side.value == "buy"
        bucket.add_trade(float(event.price), float(event.quantity), is_buy)

        return result

    async def handle_orderbook(
        self, event: OrderbookSnapshotEvent
    ) -> StreamingFeatureVector | None:
        """Ingest a normalized orderbook snapshot."""
        state = self._get_state(event.symbol.value)
        ts_ms = int(event.snapshot_time.timestamp() * 1000)
        ts_sec = ts_ms // 1000

        sf_events_total.labels(symbol=event.symbol.value, event_type="orderbook").inc()

        if not event.bids or not event.asks:
            return None

        best_bid = float(event.bids[0].price)
        best_ask = float(event.asks[0].price)
        mid = (best_bid + best_ask) / 2.0

        # Update venue state
        venue_name = event.venue.value
        if venue_name in state.venues:
            state.venues[venue_name].update_book(best_bid, best_ask, ts_ms)

        state.last_ob_ms = ts_ms
        state.best_bid = best_bid
        state.best_ask = best_ask

        result = self._advance_to_second(state, ts_sec)

        bucket = state.current_bucket
        assert bucket is not None

        # Spread BPS
        if mid > 0:
            spread_bps = (best_ask - best_bid) / mid * 10_000
            bucket.add_spread(spread_bps)

        # Orderbook depth (sum of all levels)
        bid_depth = sum(float(b.quantity) for b in event.bids)
        ask_depth = sum(float(a.quantity) for a in event.asks)
        bucket.add_orderbook(bid_depth, ask_depth)

        return result

    async def handle_mark_price(self, event: MarkPriceEvent) -> None:
        """Ingest a mark price update.

        Mark price events update state but do NOT drive the tick clock.
        The tick clock is driven exclusively by trade/orderbook events
        which have reliable, high-frequency venue timestamps.
        """
        state = self._get_state(event.symbol.value)
        state.last_mark_price = float(event.mark_price)

        sf_events_total.labels(symbol=event.symbol.value, event_type="mark_price").inc()

        if state.current_bucket is not None:
            state.current_bucket.add_mark_price(float(event.mark_price))

    async def handle_liquidation(self, event: LiquidationEvent) -> None:
        """Ingest a liquidation event.

        Like mark price, liquidation events update state on the current
        bucket but do not advance the tick clock. Liquidations are sparse
        and their timestamps may lag behind trade timestamps.
        """
        state = self._get_state(event.symbol.value)

        sf_events_total.labels(symbol=event.symbol.value, event_type="liquidation").inc()

        if state.current_bucket is not None:
            state.current_bucket.add_liquidation(
                float(event.quantity), event.is_long_liquidation
            )

    async def handle_whale_alert(self, event: WhaleAlertEvent) -> None:
        """Record a whale transfer event for context gating."""
        ts_ms = int(event.timestamp.timestamp() * 1000)
        for state in self._symbols.values():
            state.last_whale_event_ms = ts_ms

    async def handle_news_context(self, ts_ms: int) -> None:
        """Record a high-impact news event for context gating."""
        for state in self._symbols.values():
            state.last_news_event_ms = ts_ms

    # ── Core Tick Logic ───────────────────────────────────────────

    def _advance_to_second(
        self, state: SymbolState, new_second: int
    ) -> StreamingFeatureVector | None:
        """Advance time to new_second, finalizing buckets and computing features.

        If the new event is in the same second as current, no tick happens.
        If it's in a future second, we finalize the current bucket,
        insert empty buckets for any gaps, and compute features.

        Returns a feature vector if a tick boundary was crossed, else None.
        """
        if state.current_bucket is None:
            state.current_bucket = empty_bucket(new_second)
            state.current_second = new_second
            return None

        if new_second <= state.current_second:
            return None

        # Finalize current bucket
        finalized = state.current_bucket

        # Push finalized bucket into all windows
        for w in state.windows.values():
            w.push(finalized)

        # Fill gap seconds with empty buckets if events skipped seconds
        gap = new_second - state.current_second - 1
        if gap > 0:
            max_gap = max(state.windows.keys())
            for offset in range(1, min(gap, max_gap) + 1):
                gap_bucket = empty_bucket(state.current_second + offset)
                for w in state.windows.values():
                    w.push(gap_bucket)

        # Start new current bucket
        state.current_bucket = empty_bucket(new_second)
        state.current_second = new_second
        state.total_ticks += 1

        sf_ticks_total.labels(symbol=state.symbol).inc()

        # Compute features
        return self._compute_features(state)

    def _compute_features(self, state: SymbolState) -> StreamingFeatureVector:
        """Compute all features across all timeframes from window state."""
        now_ms = state.current_second * 1000

        # Compute per-timeframe features
        tf_map: dict[int, TimeframeFeatures] = {}
        for ws in state.windows:
            window = state.windows[ws]
            ret_hist = state.return_history[ws]
            mom_hist = state.momentum_history[ws]

            # Core feature computations
            returns = compute_returns(window)
            returns_z = compute_returns_z(returns, ret_hist)
            momentum_z = compute_momentum_z(window, mom_hist)
            tfi = compute_taker_flow_imbalance(window)
            spread = compute_spread_bps(window)
            widening = compute_spread_widening(window)
            obi = compute_ob_imbalance(window)
            liq_imb = compute_liquidation_imbalance(window)
            div_bps = compute_venue_divergence_bps(
                state.venues.get("binance", VenueState()),
                state.venues.get("bybit", VenueState()),
            )
            window_vwap = compute_vwap(window)

            # Update z-score histories with current values
            if returns is not None:
                ret_hist.push(returns)
            net_flow = window.totals.buy_volume - window.totals.sell_volume
            mom_hist.push(net_flow)

            tf_map[ws] = TimeframeFeatures(
                window_seconds=ws,
                returns=returns,
                returns_z=returns_z,
                momentum_z=momentum_z,
                taker_flow_imbalance=tfi,
                spread_bps=spread,
                spread_widening=widening,
                ob_imbalance=obi,
                liquidation_imbalance=liq_imb,
                venue_divergence_bps=div_bps,
                vwap=window_vwap,
                trade_count=window.totals.trade_count,
                volume=window.totals.volume,
                window_fill_pct=window.fill_pct,
            )

            sf_window_fill.labels(
                symbol=state.symbol, window=WINDOW_LABELS.get(ws, str(ws))
            ).set(window.fill_pct)

        # Freshness
        freshness_dict = compute_freshness(
            now_ms=now_ms,
            last_trade_ms=state.last_trade_ms,
            last_ob_ms=state.last_ob_ms,
            binance_ms=state.venues["binance"].last_update_ms,
            bybit_ms=state.venues["bybit"].last_update_ms,
        )
        freshness = FreshnessScore(**freshness_dict)

        # Execution feasibility (use 60s window data for spread/depth)
        w60 = state.windows.get(60)
        ob_snap = w60.latest_ob_snapshot() if w60 else None
        ob_bid_qty = ob_snap[0] if ob_snap else 0.0
        ob_ask_qty = ob_snap[1] if ob_snap else 0.0
        exec_feas = compute_execution_feasibility(
            spread_bps=tf_map[60].spread_bps if 60 in tf_map else None,
            ob_bid_qty=ob_bid_qty,
            ob_ask_qty=ob_ask_qty,
            freshness_composite=freshness.composite,
            symbol=state.symbol,
        )

        # Context flags
        whale_flag = compute_whale_risk_flag(
            state.last_whale_event_ms, now_ms,
            lookback_ms=self._settings.whale_lookback_minutes * 60_000,
        )
        news_flag = compute_urgent_news_flag(
            state.last_news_event_ms, now_ms,
            lookback_ms=self._settings.news_lookback_minutes * 60_000,
        )

        # Data quality
        window_fills = {
            WINDOW_LABELS.get(ws, str(ws)): round(tf_map[ws].window_fill_pct, 3)
            for ws in tf_map
        }
        binance_ok = not state.venues["binance"].is_stale
        bybit_ok = not state.venues["bybit"].is_stale
        quality = DataQuality(
            warmup_complete=state.warmup_complete,
            binance_connected=binance_ok,
            bybit_connected=bybit_ok,
            window_fill_pct=window_fills,
        )

        # Assemble the vector
        sorted(tf_map.keys())
        vector = StreamingFeatureVector(
            symbol=Symbol(state.symbol),
            tf_10s=tf_map.get(10, _empty_tf(10)),
            tf_30s=tf_map.get(30, _empty_tf(30)),
            tf_60s=tf_map.get(60, _empty_tf(60)),
            tf_5m=tf_map.get(300, _empty_tf(300)),
            freshness=freshness,
            execution_feasibility=exec_feas,
            whale_risk_flag=whale_flag,
            urgent_news_flag=news_flag,
            last_price=Decimal(str(state.last_price)) if state.last_price else Decimal("0"),
            best_bid=Decimal(str(state.best_bid)) if state.best_bid else None,
            best_ask=Decimal(str(state.best_ask)) if state.best_ask else None,
            mid_price=(
                Decimal(str((state.best_bid + state.best_ask) / 2))
                if state.best_bid and state.best_ask
                else None
            ),
            mark_price=(
                Decimal(str(state.last_mark_price)) if state.last_mark_price else None
            ),
            data_quality=quality,
        )

        sf_emit_total.labels(symbol=state.symbol).inc()
        return vector

    async def emit(self, vector: StreamingFeatureVector) -> None:
        """Publish a computed feature vector to Redis Streams."""
        await self._publisher.publish(STREAM_KEYS["feature_streaming"], vector)

    # ── State Access ──────────────────────────────────────────────

    def get_state(self, symbol: str) -> SymbolState | None:
        return self._symbols.get(symbol)

    @property
    def active_symbols(self) -> list[str]:
        return list(self._symbols.keys())


def _empty_tf(ws: int) -> TimeframeFeatures:
    return TimeframeFeatures(window_seconds=ws)
