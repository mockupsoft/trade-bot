# Streaming Feature Engine — Design Document

## Table of Contents

1. [Feature Formulas](#1-feature-formulas)
2. [Rolling Window Design](#2-rolling-window-design)
3. [State Management Plan](#3-state-management-plan)
4. [Feature Persistence Tables](#4-feature-persistence-tables)
5. [Edge Cases and Fallback Logic](#5-edge-cases-and-fallback-logic)
6. [Unit Test Plan](#6-unit-test-plan)
7. [Example Feature Payload](#7-example-feature-payload)

---

## 1. Feature Formulas

### 1.1 Returns & Momentum Z-scores

**Returns** — Simple return over window period:

```
returns = (last_price - first_price) / first_price
```

Where `first_price` is the open price of the earliest non-empty bucket in the window,
and `last_price` is the close price of the most recent non-empty bucket.

**Returns Z-score** — How unusual is the current return vs recent history:

```
returns_z = (current_return - μ_returns) / σ_returns
```

Where μ and σ are computed over a ring buffer of past window-returns:
- 10s window: last 180 returns (30 min of history)
- 30s window: last 120 returns (60 min)
- 60s window: last 120 returns (2 hours)
- 5m window: last 48 returns (4 hours)

Running sum/sum-of-squares are maintained incrementally. Periodic full
recomputation every 500 pushes corrects floating-point drift.

**Momentum Z-score** — How unusual is the current net taker flow:

```
net_flow = buy_volume - sell_volume
momentum_z = (net_flow - μ_flow) / σ_flow
```

Separates flow pressure from price movement. A stock can have high returns
with low momentum (gap move) or high momentum with low returns (absorbed flow).

| Feature | Range | Interpretation |
|---|---|---|
| returns | (-∞, +∞), typically ±0.05 | Positive = price up over window |
| returns_z | (-∞, +∞), typically ±3 | > 2 = unusually strong move |
| momentum_z | (-∞, +∞), typically ±3 | > 2 = unusually strong buy pressure |

### 1.2 Taker Flow Imbalance

```
TFI = (buy_taker_volume - sell_taker_volume) / (buy_taker_volume + sell_taker_volume)
```

| Value | Meaning |
|---|---|
| +1.0 | All taker volume is buy-side (maximum aggression) |
| 0.0 | Balanced |
| -1.0 | All taker volume is sell-side |
| None | No volume in window |

"Taker" means the side that crossed the spread. On Binance, `is_buyer_maker=false`
means the taker was a buyer. On Bybit, `side="Buy"` in publicTrade means taker bought.

### 1.3 Spread BPS & Spread Widening

**Spread BPS** — Current bid-ask spread:

```
spread_bps = (best_ask - best_bid) / mid_price × 10,000
```

**Spread Widening** — Current spread relative to window average:

```
spread_widening = current_spread_bps / avg_spread_bps_in_window
```

| Value | Meaning |
|---|---|
| < 1.0 | Spread is tighter than recent average (good liquidity) |
| = 1.0 | Normal |
| > 1.5 | Spread is widening significantly (deteriorating liquidity) |
| > 3.0 | Extreme widening (potential market stress) |

Floor value of 0.01 bps prevents division by near-zero average spread.

### 1.4 Orderbook Imbalance

```
OBI = (Σ bid_qty - Σ ask_qty) / (Σ bid_qty + Σ ask_qty)
```

Uses the **latest** orderbook snapshot in the window, not the time-average.
Orderbook state is point-in-time; averaging snapshots would blur the signal.

| Value | Meaning |
|---|---|
| > 0 | Bid-heavy (more buy support) |
| < 0 | Ask-heavy (more sell pressure) |
| ~ 0 | Balanced book |

### 1.5 Liquidation Imbalance

```
LI = (long_liq_vol - short_liq_vol) / (long_liq_vol + short_liq_vol)
```

| Value | Meaning |
|---|---|
| > 0 | More longs being liquidated → bearish cascade risk |
| < 0 | More shorts being liquidated → short squeeze potential |
| None | No liquidations in window (common; liquidations are sparse) |

Data source: Binance `@forceOrder` stream, Bybit `liquidation` topic.
Liquidation events do NOT drive the tick clock.

### 1.6 Binance-vs-Bybit Divergence

```
divergence_bps = (binance_mid - bybit_mid) / avg_mid × 10,000
```

Uses per-venue `VenueState` which tracks the latest mid price from each exchange.

| Value | Meaning |
|---|---|
| > 5 bps | Notable divergence |
| > 20 bps | Extreme (one venue may be lagging) |
| None | One or both venues have no data |

Not used as an arbitrage trigger in v1 — context only.

### 1.7 Freshness Score

Per-source age in milliseconds + composite [0, 1] score:

```
trade_score = max(0, 1 - trade_age_ms / 5000)
ob_score    = max(0, 1 - ob_age_ms / 10000)
venue_score = max(binance_score, bybit_score)  # at least one venue alive
composite   = min(trade_score, ob_score) × venue_score
```

Using `min()` for critical sources: if either trades or orderbook are stale,
the composite drops even if the other is fresh.

| Composite | Meaning |
|---|---|
| > 0.9 | Excellent — all data fresh |
| 0.5-0.9 | Acceptable — some staleness |
| < 0.5 | Degraded — should not trade |
| 0 | Stale — halt all decisions |

### 1.8 Execution Feasibility Score

Composite [0, 1] score answering: "Can we execute a trade right now?"

```
spread_score = max(0, 1 - spread_bps / MAX_SPREAD_BPS)
depth_score  = min(1, min(bid_depth, ask_depth) / target_depth)
feasibility  = min(spread_score, depth_score) × freshness_composite
```

| Target Depth | Symbol |
|---|---|
| 1.0 BTC | BTCUSDT |
| 10.0 ETH | ETHUSDT |

Using `min()` again: both spread AND depth must be acceptable. You can't
compensate for a 20 bps spread with deep liquidity (you still get bad fill).

### 1.9 Whale Risk Flag

Boolean. True if any qualifying whale transfer (from Whale Alert) was seen
within the lookback window (default 60 minutes).

```
whale_flag = (now - last_whale_event_time) < 60 minutes
```

This is a **gating** flag. When True, the signal engine should require higher
confidence thresholds. It does NOT trigger or cancel trades by itself.

### 1.10 Urgent News Flag

Boolean. True if a high-impact news/context event was detected within
the lookback window (default 30 minutes).

```
news_flag = (now - last_news_event_time) < 30 minutes
```

Same gating behavior as whale flag. Both flags are set globally for all
symbols (a major whale USDT transfer affects the entire market).

---

## 2. Rolling Window Design

### Architecture: Bucketed Windows with O(1) Operations

Instead of storing individual trades (BTC generates 100+ trades/second = 30K/5min),
we aggregate into **SecondBucket** objects — one per calendar second.

```
Individual trades:  ████████████████████████████  (~30,000 for 5min)
                              ↓
SecondBuckets:      [B][B][B][B][B]...[B][B][B]   (300 for 5min)
```

Each SecondBucket (~200 bytes) stores:
- OHLC prices, pq_sum (for VWAP), volume, buy_vol, sell_vol, trade_count
- Spread: sum, count, last
- Orderbook: last bid/ask depth, cumulative depth
- Liquidations: long_vol, short_vol, count
- Mark price: last value

### Window Structure

Four timeframes share a common bucket format but use **separate deques**:

```
Window     Deque Size    Memory per Symbol    Z-score History
─────────────────────────────────────────────────────────────
10s        10 buckets    ~2 KB                180 past returns
30s        30 buckets    ~6 KB                120 past returns
60s        60 buckets    ~12 KB               120 past returns
5m         300 buckets   ~60 KB               48 past returns
─────────────────────────────────────────────────────────────
Total per symbol: ~80 KB + ~4 KB histories = ~84 KB
Total for 2 symbols: ~168 KB
```

### WindowState: O(1) Add + O(1) Evict

```python
class WindowState:
    buckets: deque[SecondBucket]  # maxlen = window_seconds
    totals: RunningTotals         # running sums, always in sync

    def push(bucket):
        if deque is full:
            evicted = buckets[0]
            totals.subtract(evicted)    # O(1)
        buckets.append(bucket)
        totals.add(bucket)              # O(1)
```

RunningTotals tracks: volume, buy_volume, sell_volume, trade_count,
pq_sum, spread_bps_sum, ob quantities, liquidation volumes, active_seconds.

**Add** and **subtract** are mirror operations on 13 fields. No loops, no recomputation.

### Tick Model: Event-Driven Second Boundaries

```
Events at second 1000:  T T T T OB T T
Events at second 1001:  T OB T
Events at second 1002:  (none)
Events at second 1003:  T T

Timeline:
  1000          1001          1002          1003
  ──────────────┼─────────────┼─────────────┼──────
  bucket 1000   │→ TICK       │             │→ TICK
  filling...    │finalize     │empty bucket │finalize
                │push to      │auto-inserted│push to
                │all windows  │             │all windows
                │compute      │             │compute
                │features     │             │features
```

On each second boundary crossing:
1. Finalize current bucket
2. Push it into all 4 window states (O(1) each)
3. Insert empty buckets for any skipped seconds (handles gaps)
4. Compute features from window accumulators
5. Update z-score histories
6. Emit StreamingFeatureVector

Only **trades** and **orderbook** events drive the tick clock.
Mark price and liquidation events update state on the current bucket
without advancing time (they are sparse and may lag).

---

## 3. State Management Plan

### Per-Symbol State (`SymbolState`)

```
SymbolState
├── windows: {10: WindowState, 30: ..., 60: ..., 300: ...}
├── return_history: {10: ReturnHistory, 30: ..., 60: ..., 300: ...}
├── momentum_history: {10: MomentumHistory, ...}
├── venues: {"binance": VenueState, "bybit": VenueState}
├── current_bucket: SecondBucket | None
├── current_second: int
├── last_trade_ms, last_ob_ms: int
├── last_whale_event_ms, last_news_event_ms: int
├── last_price, best_bid, best_ask, last_mark_price: float
└── total_ticks: int
```

### Startup: Cold Start vs Warm Recovery

**Cold start**: All windows empty. Features return None until sufficient
data accumulates. The `warmup_complete` flag in DataQuality is False
until the largest window (5m = 300 seconds) is full.

**Warm recovery** (future enhancement): On restart, read the last N
feature snapshots from TimescaleDB to seed the z-score histories.
Window state cannot be fully recovered from DB (we'd need raw bucket
data). Accept a brief warmup period after restart.

### Memory Budget

| Component | Per Symbol | Total (2 symbols) |
|---|---|---|
| WindowState × 4 | ~80 KB | ~160 KB |
| Z-score histories × 4 × 2 | ~4 KB | ~8 KB |
| VenueState × 2 | ~0.1 KB | ~0.4 KB |
| Current bucket + metadata | ~0.5 KB | ~1 KB |
| **Total** | **~85 KB** | **~170 KB** |

Negligible. The entire streaming feature engine for 2 symbols uses less memory
than a single high-resolution PNG image.

### Concurrency Model

The engine is **single-threaded async**. One `StreamingFeatureEngine` instance
per process. Events are consumed from Redis Streams sequentially (per consumer).
No locks needed — all state mutation happens in one coroutine at a time.

If throughput becomes a bottleneck (unlikely at 2 symbols), partition by symbol
across multiple consumers.

---

## 4. Feature Persistence Tables

### `cte.streaming_features` (TimescaleDB hypertable)

```sql
CREATE TABLE cte.streaming_features (
    time                TIMESTAMPTZ NOT NULL,    -- event time
    event_id            UUID NOT NULL,
    symbol              TEXT NOT NULL,
    window_seconds      INTEGER NOT NULL,        -- 10, 30, 60, 300

    -- Core features (10 families)
    returns             DOUBLE PRECISION,
    returns_z           DOUBLE PRECISION,
    momentum_z          DOUBLE PRECISION,
    taker_flow_imbalance DOUBLE PRECISION,
    spread_bps          DOUBLE PRECISION,
    spread_widening     DOUBLE PRECISION,
    ob_imbalance        DOUBLE PRECISION,
    liquidation_imbalance DOUBLE PRECISION,
    venue_divergence_bps DOUBLE PRECISION,
    vwap                DOUBLE PRECISION,

    -- Volume & activity
    trade_count         INTEGER NOT NULL DEFAULT 0,
    volume              DOUBLE PRECISION NOT NULL DEFAULT 0,
    buy_volume          DOUBLE PRECISION NOT NULL DEFAULT 0,
    sell_volume         DOUBLE PRECISION NOT NULL DEFAULT 0,
    window_fill_pct     DOUBLE PRECISION NOT NULL DEFAULT 0,

    -- Cross-timeframe scalars (stored with window_seconds=0)
    execution_feasibility DOUBLE PRECISION,
    whale_risk_flag     BOOLEAN DEFAULT false,
    urgent_news_flag    BOOLEAN DEFAULT false,

    -- Freshness
    freshness_composite DOUBLE PRECISION,
    trade_age_ms        INTEGER,
    orderbook_age_ms    INTEGER,

    -- Raw reference values
    last_price          NUMERIC,
    best_bid            NUMERIC,
    best_ask            NUMERIC,
    mid_price           NUMERIC,
    mark_price          NUMERIC
);

-- Hypertable with automatic chunk management
SELECT create_hypertable('cte.streaming_features', 'time');

-- Primary query pattern: latest features for a symbol + timeframe
CREATE INDEX idx_sf_symbol_window_time
    ON cte.streaming_features (symbol, window_seconds, time DESC);
```

### Write Pattern

The `FeaturePersister` batches feature vectors and flushes periodically
(every 10 seconds by default). Each `StreamingFeatureVector` produces
4 rows (one per timeframe).

Write rate: 2 symbols × 4 timeframes × 6/minute = 48 rows/minute = ~70K rows/day.

With TimescaleDB compression (enabled after 7 days), storage is ~10 MB/day.

### Supporting Tables

```sql
-- Liquidation events (raw, for replay and analysis)
CREATE TABLE cte.liquidations (
    time, venue, symbol, side, price, quantity, is_long_liq
);

-- Mark price history (for PnL and divergence analysis)
CREATE TABLE cte.mark_prices (
    time, venue, symbol, mark_price, index_price, funding_rate
);
```

---

## 5. Edge Cases and Fallback Logic

### 5.1 Empty Window (No Data)

| Feature | Behavior | Fallback Value |
|---|---|---|
| returns | No first/last price | None |
| returns_z | Cannot compute | None |
| momentum_z | Zero flow | z_score(0) or None |
| taker_flow_imbalance | Zero volume | None |
| spread_bps | No spread data | None |
| spread_widening | No average | None |
| ob_imbalance | No orderbook | None |
| liquidation_imbalance | No liquidations | None (expected; liqs are rare) |
| venue_divergence | Missing venue | None |
| freshness | No timestamps | composite = 0.0 |
| execution_feasibility | Missing spread | None |

**Rule**: None means "insufficient data to compute". Downstream consumers
(signal engine) must treat None as "no opinion" — never as zero.

### 5.2 Single Venue Down

If Binance is up but Bybit is down (or vice versa):
- venue_divergence_bps = None (cannot compare)
- freshness.bybit_age_ms = very large
- freshness.composite still > 0 if the other venue is fresh (we use `max()`)
- All other features work normally (they use combined data)

The `DataQuality.binance_connected` / `bybit_connected` flags reflect this.

### 5.3 Gap Seconds (No Events for Multiple Seconds)

When an event arrives at second T and the last event was at second T-N:
- Empty buckets are inserted for seconds T-N+1 through T-1
- Capped at `max_window_size` to prevent unbounded loop
- Window fill_pct decreases (reflecting the data gap)
- Features computed on the sparse data may have None values

If no events arrive for > 5 seconds, the freshness composite drops to 0
and execution_feasibility becomes None.

### 5.4 Clock Skew Between Venues

Events from different venues may have slightly different timestamps.
The tick clock uses event timestamps, not wall clock. If Binance and Bybit
events arrive for the same market second, they're bucketed together.

If one venue is consistently 100-200ms ahead, the events may fall in
different second buckets. This is acceptable — the z-score histories
smooth out sub-second jitter.

### 5.5 Burst of Events in One Second

BTC can have 100+ trades in a single second. All are aggregated into one
SecondBucket via `add_trade()`. The bucket handles N adds with no memory growth.

### 5.6 Numeric Stability

ReturnHistory uses running sum/sum-of-squares which can accumulate
floating-point error. Mitigation:
- Periodic full recomputation every 500 pushes
- Returns are small numbers (±0.05) where float64 has ~15 digits of precision
- Error only affects z-score computation, not the raw feature values

### 5.7 Warmup Period

The first 300 seconds (5 minutes) after start, `warmup_complete=False`.
The signal engine should check this flag and refuse to generate signals
until warmup is complete.

Shorter windows become valid sooner (10s window is full after 10 seconds)
but z-scores need at least 10 samples in the history buffer, so meaningful
z-scores for 10s windows appear after 100 seconds.

### 5.8 Market Maintenance / Weekend Gaps

If the exchange goes offline (maintenance) and reconnects with a large time gap:
- The gap-filling logic caps at `max_window_size` empty buckets
- All windows effectively reset (filled with empty buckets)
- Warmup begins again from scratch
- This is correct behavior — features from before maintenance are stale

---

## 6. Unit Test Plan

### Test Matrix

| Module | Test Class | Tests | Coverage |
|---|---|---|---|
| **types.py** | TestSecondBucket | 7 tests | OHLC, VWAP, spread, orderbook, liquidation, copy, empty |
| **accumulators.py** | TestRunningTotals | 3 tests | add/subtract inverse, empty inactive, liquidation accumulation |
| | TestWindowState | 8 tests | push/evict, first/last price, empty, fill_pct, is_full, spread, OB |
| | TestReturnHistory | 6 tests | insufficient data, zero variance, normal z, outlier z, eviction, drift correction |
| | TestMomentumHistory | 1 test | push and z_score |
| **formulas.py** | TestReturns | 4 tests | positive, negative, empty, single bucket |
| | TestReturnsZ | 3 tests | no history, with history, None return |
| | TestMomentumZ | 1 test | buy-heavy window |
| | TestTakerFlowImbalance | 4 tests | all buys (+1), all sells (-1), balanced (0), empty (None) |
| | TestSpread | 4 tests | bps, widening >1, widening <1, no data |
| | TestOrderbookImbalance | 4 tests | bid-heavy, ask-heavy, balanced, no OB |
| | TestLiquidationImbalance | 2 tests | long-heavy, no liquidations |
| | TestVenueDivergence | 3 tests | binance higher, zero, stale venue |
| | TestFreshness | 3 tests | fully fresh, stale, no data |
| | TestExecutionFeasibility | 4 tests | good, wide spread, no depth, None spread |
| | TestWhaleRiskFlag | 3 tests | recent, old, none |
| | TestUrgentNewsFlag | 3 tests | recent, old, none |
| | TestVWAP | 3 tests | simple, volume-weighted, empty |
| **engine.py** | TestBasic | 5 tests | first event, second boundary, same-second, gaps, multi-symbol |
| | TestFeatureVectorContents | 5 tests | all timeframes, returns, TFI, last_price, freshness |
| | TestOrderbookHandling | 2 tests | spread update, OB imbalance |
| | TestVenueDivergence | 1 test | cross-venue divergence |
| | TestMarkPrice | 2 tests | mark price stored, liquidation processed |
| | TestContextFlags | 2 tests | whale flag set, default false |
| | TestDataQuality | 2 tests | warmup, window fill |
| | TestDeterminism | 1 test | replay produces identical output |
| **Total** | | **86 tests** | |

### Key Test Properties

1. **Determinism**: Same event sequence → identical feature vectors (tested explicitly)
2. **Boundary conditions**: Empty windows, zero volume, single venue, no liquidations
3. **Incremental correctness**: Add + subtract = identity for RunningTotals
4. **Numeric**: z-score with known distributions, VWAP arithmetic, spread BPS calculation
5. **Integration**: Full engine pipeline from trade event to feature vector

---

## 7. Example Feature Payload

### BTCUSDT at 2024-03-15T12:00:01Z (active market)

```json
{
  "event_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "timestamp": "2024-03-15T12:00:01.000000+00:00",
  "source": "streaming_feature_engine",
  "symbol": "BTCUSDT",

  "tf_10s": {
    "window_seconds": 10,
    "returns": 0.00012,
    "returns_z": 0.34,
    "momentum_z": 0.87,
    "taker_flow_imbalance": 0.15,
    "spread_bps": 1.2,
    "spread_widening": 0.95,
    "ob_imbalance": 0.22,
    "liquidation_imbalance": null,
    "venue_divergence_bps": 0.8,
    "vwap": 65234.50,
    "trade_count": 247,
    "volume": 12.45,
    "window_fill_pct": 1.0
  },

  "tf_30s": {
    "window_seconds": 30,
    "returns": 0.00045,
    "returns_z": 0.61,
    "momentum_z": 1.23,
    "taker_flow_imbalance": 0.08,
    "spread_bps": 1.2,
    "spread_widening": 1.02,
    "ob_imbalance": 0.22,
    "liquidation_imbalance": null,
    "venue_divergence_bps": 0.8,
    "vwap": 65228.30,
    "trade_count": 812,
    "volume": 38.7,
    "window_fill_pct": 1.0
  },

  "tf_60s": {
    "window_seconds": 60,
    "returns": -0.00021,
    "returns_z": -0.45,
    "momentum_z": -0.12,
    "taker_flow_imbalance": -0.03,
    "spread_bps": 1.2,
    "spread_widening": 0.88,
    "ob_imbalance": 0.22,
    "liquidation_imbalance": -0.6,
    "venue_divergence_bps": 0.8,
    "vwap": 65241.10,
    "trade_count": 1584,
    "volume": 74.2,
    "window_fill_pct": 1.0
  },

  "tf_5m": {
    "window_seconds": 300,
    "returns": 0.0018,
    "returns_z": 1.42,
    "momentum_z": 0.95,
    "taker_flow_imbalance": 0.11,
    "spread_bps": 1.2,
    "spread_widening": 0.91,
    "ob_imbalance": 0.22,
    "liquidation_imbalance": 0.33,
    "venue_divergence_bps": 0.8,
    "vwap": 65198.75,
    "trade_count": 8420,
    "volume": 412.8,
    "window_fill_pct": 0.97
  },

  "freshness": {
    "trade_age_ms": 45,
    "orderbook_age_ms": 120,
    "binance_age_ms": 45,
    "bybit_age_ms": 200,
    "composite": 0.98
  },

  "execution_feasibility": 0.92,
  "whale_risk_flag": false,
  "urgent_news_flag": false,

  "last_price": "65235.10",
  "best_bid": "65234.90",
  "best_ask": "65235.30",
  "mid_price": "65235.10",
  "mark_price": "65235.05",

  "data_quality": {
    "warmup_complete": true,
    "binance_connected": true,
    "bybit_connected": true,
    "window_fill_pct": {
      "10s": 1.0,
      "30s": 1.0,
      "60s": 1.0,
      "5m": 0.97
    }
  }
}
```

### Reading This Payload

**Short-term (10s)**: Slight positive return (+0.012%), moderate buy pressure (TFI +0.15),
tight spread (1.2 bps), slightly bid-heavy book (+0.22). No notable divergence.

**Medium-term (60s)**: Slightly negative (-0.021%), balanced flow (TFI -0.03).
Some short liquidations (LI -0.6) suggesting shorts are being squeezed. Spread tighter
than average (widening 0.88).

**Longer-term (5m)**: Positive trend (+0.18%), returns_z of 1.42 means this move is
roughly 1.4 standard deviations above the recent 4-hour average. Buy-dominant flow
(TFI +0.11). Some long liquidations too (LI +0.33).

**Signal engine interpretation**: The 5m uptrend with above-average strength (z=1.42)
and positive flow, combined with tight spreads and high execution feasibility (0.92),
would make this a reasonable OPEN_LONG candidate — pending risk manager approval.

### ETHUSDT During Low Activity (4:00 AM UTC)

```json
{
  "symbol": "ETHUSDT",
  "tf_10s": {
    "window_seconds": 10,
    "returns": null,
    "returns_z": null,
    "taker_flow_imbalance": null,
    "spread_bps": 3.8,
    "spread_widening": 1.45,
    "ob_imbalance": -0.05,
    "liquidation_imbalance": null,
    "trade_count": 3,
    "volume": 0.8,
    "window_fill_pct": 0.3
  },
  "freshness": {
    "trade_age_ms": 3200,
    "composite": 0.36
  },
  "execution_feasibility": 0.28,
  "data_quality": {
    "warmup_complete": true,
    "window_fill_pct": {"10s": 0.3, "30s": 0.4, "60s": 0.45, "5m": 0.52}
  }
}
```

**Reading**: Low activity period. Only 3 trades in 10 seconds, spread has widened
to 3.8 bps (45% above average). Execution feasibility is poor (0.28). The signal
engine should NOT generate signals in these conditions — the freshness and feasibility
scores act as natural gates.
