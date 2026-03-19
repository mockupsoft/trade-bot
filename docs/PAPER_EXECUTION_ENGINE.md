# Paper Execution Engine — Design Document

## Table of Contents

1. [Execution Architecture](#1-execution-architecture)
2. [Fill Model Design](#2-fill-model-design)
3. [Position Schema](#3-position-schema)
4. [Paper Position State Machine](#4-paper-position-state-machine)
5. [Analytics Fields](#5-analytics-fields)
6. [Deterministic Replay Approach](#6-deterministic-replay-approach)
7. [Acceptance Tests](#7-acceptance-tests)

---

## 1. Execution Architecture

### Pipeline

```
ScoredSignalEvent (from signal engine)
  │
  ├── Book Lookup (latest bid/ask for symbol)
  │     ├── No book → REJECT (cannot fill without a quote)
  │     └── Book available → proceed
  │
  ├── Fill Model (bid/ask-aware, configurable)
  │     ├── SpreadCrossing: BUY @ ask + slippage
  │     ├── VWAPDepth: walk orderbook levels
  │     └── WorstCase: 2× slippage stress test
  │
  ├── Position Creation
  │     ├── PENDING → OPEN (with modeled latency offset)
  │     ├── Carries: signal tier, entry reason, composite score
  │     ├── Sets: stop distance, MFE/MAE counters, fee estimate
  │     └── Records: entry latency, slippage, spread cost
  │
  ├── Price Updates (on every market tick)
  │     ├── Update MFE/MAE
  │     ├── Update unrealized PnL
  │     └── Evaluate exit conditions
  │
  └── Exit Evaluation
        ├── Stop loss: loss_pct ≥ threshold
        ├── Take profit: gain_pct ≥ threshold
        ├── Trailing stop: drawdown from high ≥ threshold (in profit only)
        ├── Timeout: hold_minutes ≥ max
        └── Close at bid - slippage (spread-crossing exit)
```

### Component Map

```
src/cte/execution/
├── fill_model.py    # Pure fill price computations
├── position.py      # PaperPosition with state machine + analytics
├── paper.py         # PaperExecutionEngine (coordinator)
└── engine.py        # ExecutionEngine (mode dispatcher)
```

### Key Invariants

1. **No mid-price fills.** BUY always fills at or above ask. SELL always fills at or below bid.
2. **No asyncio.sleep.** Latency is modeled as a timestamp offset, not a real delay.
3. **No datetime.now.** All timestamps come from event payloads.
4. **No position without a book.** If no bid/ask is available, the fill is rejected.
5. **Signal provenance survives.** Tier, composite_score, and entry_reason propagate to the position.

---

## 2. Fill Model Design

### Why Not Mid-Price?

The naive approach: `fill_price = last_price + slippage`.

This is wrong because:
- `last_price` is usually the last trade price, which is between bid and ask
- A market BUY order crosses the spread and fills at the **ask**, not the mid
- In a 2-bps spread market, the mid-price model ignores 1 bps of real cost (per side)
- Over 100 trades, that's 200 bps of hidden cost — enough to flip a strategy from profitable to losing

### Model 1: Spread Crossing (Default)

```
BUY:  fill = best_ask × (1 + slippage_bps / 10000)
SELL: fill = best_bid × (1 - slippage_bps / 10000)
```

The `slippage_bps` parameter models:
- Execution latency (price moves between decision and fill)
- Market impact (your order pushes the price)
- Partial fills at worse levels (for small orders within top-of-book depth)

Default: 5 bps. This is conservative for BTC/ETH perps where typical fills
are 1-3 bps from touch, but accounts for tail scenarios.

### Model 2: VWAP Depth

```python
remaining_qty = order_qty
total_cost = 0
for level in orderbook_levels:
    fill_at_level = min(remaining_qty, level.quantity)
    total_cost += fill_at_level × level.price
    remaining_qty -= fill_at_level
    if remaining_qty <= 0: break

vwap_price = total_cost / order_qty
fill = vwap_price × (1 + slippage_bps / 10000)
```

Used for larger orders (>1 BTC) that would walk the book.
Requires orderbook level data from the feature engine.

### Model 3: Worst Case

```
fill = ask × (1 + 2 × slippage_bps / 10000)    # for BUY
```

Doubles the slippage for stress testing. If a strategy is profitable
under worst-case fills, it's robust.

### Fill Result

Every fill returns a `FillResult` with:
- `fill_price`: actual execution price
- `slippage_bps`: cost from touching best price to fill price
- `effective_spread_bps`: cost from mid to fill price (half-spread + slippage)
- `model_used`: which fill model was applied
- `detail`: human-readable explanation

---

## 3. Position Schema

### In-Memory (PaperPosition dataclass)

```python
@dataclass
class PaperPosition:
    # Identity
    position_id: UUID
    symbol: str
    direction: str          # "long" | "short"
    status: PositionStatus  # PENDING | OPEN | REDUCED | CLOSED

    # Signal provenance
    signal_id: UUID
    signal_tier: str        # "A" | "B" | "C"
    entry_reason: str       # human-readable reason from signal
    composite_score: float  # signal composite at entry time

    # Fill details
    entry_price: Decimal    # actual fill price
    fill_price: Decimal     # same as entry_price (alias for clarity)
    quantity: Decimal
    notional_usd: Decimal
    leverage: int

    # Slippage and cost
    signal_price: Decimal           # mid at signal time (for slip measurement)
    modeled_slippage_bps: Decimal   # slippage from best touch to fill
    effective_spread_bps: Decimal   # cost from mid to fill
    fill_model_used: str            # "spread_crossing" | "vwap_depth" | ...
    estimated_fees_usd: Decimal     # modeled taker fees

    # Timing
    signal_time: datetime           # when signal was generated
    fill_time: datetime             # signal_time + modeled_fill_latency
    close_time: datetime | None
    entry_latency_ms: int           # fill_time - signal_time
    modeled_fill_latency_ms: int    # configured exchange processing delay

    # Risk
    stop_loss_pct: float
    take_profit_pct: float
    stop_distance_usd: Decimal      # entry × stop_pct × qty

    # Price tracking
    current_price: Decimal
    highest_price: Decimal
    lowest_price: Decimal

    # Excursion analytics
    mfe_pct: float          # max favorable excursion (%)
    mae_pct: float          # max adverse excursion (%)
    mfe_usd: Decimal
    mae_usd: Decimal

    # PnL
    unrealized_pnl: Decimal
    realized_pnl: Decimal   # set on close, includes fee deduction

    # Exit
    exit_price: Decimal
    exit_reason: str         # stop_loss | take_profit | trailing_stop | timeout | ...
    exit_detail: str         # human-readable exit explanation

    # Audit
    state_transitions: list[tuple]  # [(old, new, timestamp), ...]
```

### Database (cte.positions)

Same fields mapped to PostgreSQL columns. See `src/cte/db/schema.py` for the full
CREATE TABLE with all 35+ columns, including indexes on status, symbol, and signal_id.

---

## 4. Paper Position State Machine

```
                ┌─────────┐
                │ PENDING  │
                └────┬─────┘
                     │ open(fill_price, fill_time)
                     │ Sets: entry_price, fill_time, stop_distance
                     │ Records: entry_latency_ms
                     ▼
                ┌─────────┐
           ┌───▶│  OPEN    │◀──┐
           │    └────┬─────┘   │
           │         │         │
           │    update_price() │
           │    (every tick)   │
           │    • MFE/MAE      │
           │    • unrealized   │
           │                   │
           │    close()        │ reduce() [future]
           │         │         │
           │         ▼         │
           │    ┌─────────┐   │
           │    │ CLOSED   │   │
           │    └──────────┘   │
           │    Sets:          │
           │    • exit_price   ├───────────┐
           │    • exit_reason  │           │
           │    • realized_pnl │     ┌─────┴────┐
           │    • r_multiple   │     │ REDUCED  │
           │                   │     │ (future) │
           └───────────────────┘     └──────────┘
```

### Transition Rules

| From | To | Trigger | Side Effects |
|---|---|---|---|
| PENDING | OPEN | `open(fill_price, fill_time)` | Sets entry, calculates stop_distance, records latency |
| OPEN | CLOSED | `close(exit_price, time, reason)` | Calculates realized_pnl (minus fees), sets exit fields, final MFE/MAE update |
| OPEN | REDUCED | `reduce(qty, price, time)` | Future: partial close, adjusts remaining quantity |
| REDUCED | CLOSED | `close(exit_price, time, reason)` | Same as OPEN → CLOSED |

### Guard Conditions

- Cannot `open()` a non-PENDING position
- Cannot `close()` a PENDING position
- Cannot `update_price()` a CLOSED position (silently ignored)
- All transitions are logged in `state_transitions`

---

## 5. Analytics Fields

### Per-Position

| Field | Formula | Purpose |
|---|---|---|
| `mfe_pct` | max((price - entry) / entry) during life | Best unrealized profit — "how good could this have been?" |
| `mae_pct` | max((entry - price) / entry) during life | Worst unrealized loss — "how much heat did we take?" |
| `mfe_usd` | mfe_pct × entry × qty | Dollar MFE |
| `mae_usd` | mae_pct × entry × qty | Dollar MAE |
| `r_multiple` | realized_pnl / stop_distance_usd | PnL in units of initial risk |
| `entry_latency_ms` | fill_time - signal_time | Time from decision to execution |
| `modeled_slippage_bps` | (fill - touch) / touch × 10000 | Cost from best available to actual fill |
| `effective_spread_bps` | (fill - mid) / mid × 10000 | Total cost including half-spread + slippage |
| `hold_duration_seconds` | close_time - fill_time | How long the position was held |
| `estimated_fees_usd` | notional × fee_bps / 10000 | Modeled taker fees (entry + exit) |

### Why MFE/MAE Matter

MFE tells you if your entries are good (high MFE = the trade went in your favor at some point).
MAE tells you if your stops are too tight (high MAE on winners = you almost got stopped out).

The combination reveals strategy quality:
- High MFE, low MAE: excellent entries, clean trade paths
- High MFE, high MAE: good entries but volatile — need wider stops or better timing
- Low MFE, high MAE: poor entries — re-evaluate signal logic
- Low MFE, low MAE: small moves either way — possibly trading noise

### Why R-Multiple Matters

R-multiple normalizes PnL by risk. A $500 profit on a $250 risk (R=2.0) is better than
a $500 profit on a $1000 risk (R=0.5), even though the dollar amounts are the same.

For portfolio-level analysis: mean R-multiple > 0 and positive expectancy (win_rate × avg_win_R
- loss_rate × avg_loss_R > 0) are necessary conditions for a viable strategy.

---

## 6. Deterministic Replay Approach

### Three Rules for Replay Safety

1. **No wall-clock time.** Every timestamp comes from event payloads (`event.timestamp`,
   `event.trade_time`, `event.snapshot_time`). The paper engine never calls `datetime.now()`.

2. **No real delays.** Latency is modeled as `fill_time = signal_time + timedelta(ms=fill_delay_ms)`.
   No `asyncio.sleep()`. The engine processes events as fast as they arrive.

3. **No randomness.** Slippage is a deterministic function of `slippage_bps` config and book state.
   No random jitter, no Gaussian slippage models.

### Replay Procedure

```python
# Load historical events from DB or file
events = load_events("2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")

# Create engine with same config
engine = PaperExecutionEngine(exec_settings, exit_settings, publisher)

for event in events:
    if isinstance(event, OrderbookSnapshotEvent):
        engine.update_book(event.symbol, event.bids[0].price, event.asks[0].price)
    elif isinstance(event, TradeEvent):
        engine.update_price(event.symbol, event.price)
        engine.evaluate_exits(event.symbol, event.price, event.trade_time)
    elif isinstance(event, ScoredSignalEvent):
        engine.open_position(event, qty, notional, event.timestamp)

# Compare results with original run
assert replay_positions == original_positions
```

This is verified by the `TestDeterministicReplay.test_same_sequence_same_results` test
which runs the same event sequence twice and asserts identical fill prices, MFE/MAE, and
position states.

### What Makes Replay Possible

| Component | Replay-Safe? | How |
|---|---|---|
| Fill price | Yes | Deterministic function of book state + config |
| Fill time | Yes | `signal_time + fill_delay_ms` offset |
| MFE/MAE | Yes | Deterministic from price sequence |
| Exit triggers | Yes | Deterministic from price + time |
| PnL | Yes | Deterministic from entry + exit fills |
| Fees | Yes | `notional × fee_bps / 10000` (no venue API) |

---

## 7. Acceptance Tests

### Test Coverage: 50 tests across 3 files

#### Fill Model Tests (test_fill_model.py)

| Test | Validates |
|---|---|
| buy_fills_above_ask | BUY fills at ask + slippage, never at mid |
| sell_fills_below_bid | SELL fills at bid - slippage |
| zero_slippage_fills_at_touch | Slippage=0 fills exactly at best price |
| slippage_bps_tracked | FillResult.slippage_bps is populated |
| buy_always_worse_than_sell | BUY price > SELL price (spread cost) |
| invalid_book_raises | ValueError on zero/negative prices |
| small_order_fills_at_best | VWAP: order < first level fills at L1 |
| large_order_walks_book | VWAP: order > L1 walks to deeper levels |
| order_exceeds_depth | VWAP: fills remaining at worst known price |
| vwap_falls_back | No levels → falls back to spread crossing |
| worst_case_double_slip | WorstCase: 2× slippage applied |

#### Position Lifecycle Tests (test_position.py)

| Test | Validates |
|---|---|
| pending_to_open | State transition + field initialization |
| open_to_closed | State transition + PnL calculation |
| cannot_open_twice | Guard: double-open raises |
| cannot_close_pending | Guard: close before open raises |
| state_transitions_recorded | Audit log populated |
| long_winning/losing | PnL correct for both outcomes |
| unrealized_pnl_updates | Live PnL tracking on price tick |
| fees_deducted | Realized PnL includes fee subtraction |
| mfe_tracks_best | MFE captures peak unrealized profit |
| mae_tracks_worst | MAE captures deepest unrealized loss |
| no_updates_when_closed | Closed positions are immutable |
| r_multiple calculations | R-multiple correct for winners and losers |
| entry_latency | Latency = fill_time - signal_time |
| hold_duration | Duration = close_time - fill_time |
| to_dict_complete | All fields serialize to dict |

#### Engine Integration Tests (test_paper_engine.py)

| Test | Validates |
|---|---|
| creates_position | Signal → fill → OPEN position |
| fills_above_ask | Entry fill crosses the spread |
| carries_signal_provenance | Tier + score on position |
| no_book_returns_none | No fill without bid/ask |
| entry_latency_modeled | fill_delay_ms → latency |
| stop_distance_calculated | Risk amount computed |
| close_at_bid | Exit fill at bid side |
| realized_pnl_calculated | PnL from entry to exit |
| stop_loss_triggered | SL exit at configured threshold |
| take_profit_triggered | TP exit at configured threshold |
| timeout_triggered | Max hold time exit |
| no_exit_in_range | No premature exits |
| mfe_mae_through_lifecycle | Excursions tracked across ticks |
| deterministic_replay | Same sequence → same results |
| vwap_fill | VWAP mode walks book correctly |

### Acceptance Criteria for Phase 3 (Paper Trading)

Before moving to Phase 4 (testnet), the paper engine must demonstrate:

- [ ] 7 consecutive days of paper trading without crashes
- [ ] All fills use bid/ask (no mid-price fills in logs)
- [ ] MFE/MAE populated for every closed position
- [ ] R-multiple populated for all positions with stop distance
- [ ] Entry latency = configured fill_delay_ms for all fills
- [ ] Stop loss / take profit triggers at correct thresholds (verified by replay)
- [ ] Position state transitions recorded in correct order
- [ ] Total PnL matches sum of individual position realized_pnl
- [ ] Replay of same event sequence produces identical results
