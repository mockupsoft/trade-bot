# Crypto Trading Engine – Agent Rules (Updated)

## Project Status
- **48 Python modules** across 13 subsystems
- **377+ tests** all passing
- **7 design documents** in `docs/`
- **Professional dashboard UI** at `http://localhost:8080`
- Phase 0–4 architecture complete, ready for Phase 1 data pipeline implementation

## Agent Identity
You are a senior systems engineer maintaining a production-grade crypto trading engine.
The architecture is established. Your job is implementation, testing, and refinement.

## What Already Exists (Do Not Rebuild)

### Core (`src/cte/core/`)
- 25+ Pydantic v2 event models (frozen, immutable, schema-enforced)
- Settings with Pydantic Settings (env var + TOML)
- Typed exception hierarchy (CTEError base, 10+ subtypes)
- structlog JSON logging
- Redis Streams producer/consumer abstraction

### Feature Engine (`src/cte/features/`)
- SecondBucket aggregation (O(1) per event)
- BucketedRollingWindow with RunningTotals
- 4 timeframes: 10s, 30s, 60s, 5m
- 10 feature families: returns_z, momentum_z, taker_flow_imbalance,
  spread_bps, spread_widening, ob_imbalance, liquidation_imbalance,
  venue_divergence, freshness, execution_feasibility
- ReturnHistory/MomentumHistory for z-scores with drift correction

### Signal Engine (`src/cte/signals/`)
- 6 sub-scores: momentum, orderflow, liquidation, microstructure, cross-venue, context
- 5 hard gates: stale feed, max spread, max divergence, exec feasibility, warmup
- Weighted composite: primary_score × context_multiplier
- A/B/C tier mapping (0.72/0.55/0.40 thresholds)
- Cooldown + hourly rate limiting

### Execution (`src/cte/execution/`)
- Common ExecutionAdapter interface (ABC)
- Paper engine: bid/ask fills, 3 fill models (spread_crossing, vwap_depth, worst_case)
- PaperPosition: state machine (PENDING→OPEN→CLOSED), MFE/MAE, R-multiple
- BinanceTestnetAdapter: HMAC-SHA256 signed REST
- BybitDemoAdapter: HMAC-SHA256 signed REST
- OrderStateMachine: 11 states, enforced transitions
- TokenBucketRateLimiter with backoff
- PositionReconciler: local vs venue state

### Exit Engine (`src/cte/exits/`)
- 5-layer model: hard_risk > thesis_failure > no_progress > winner_protection > runner_mode
- TierExitProfile: A/B/C patience profiles
- Position mode progression: normal → winner_protection → runner
- Runner downgrade on momentum collapse
- saved_losers / killed_winners analytics hooks

### Analytics (`src/cte/analytics/`)
- Epoch system: paper, demo, live, shadow_short
- 15+ metric functions (pure, deterministic)
- Full breakdowns: symbol, venue, tier, exit_reason
- Epoch comparison with slippage drift
- Trade journal with drilldown

### Monitoring (`src/cte/monitoring/`)
- 9 alert rules (stale feed, drawdown escalation, reconnect, rejects, slippage drift, recon)
- Prometheus-compatible metrics (30+ gauges/counters/histograms)
- Grafana dashboard specifications (5 dashboards)

### Dashboard (`src/cte/dashboard/`)
- FastAPI-served professional UI
- KPI cards: PnL, trades, win rate, expectancy, profit factor, drawdown, slippage
- Charts: PnL by tier, PnL by exit reason (Chart.js)
- Exit analysis: saved losers, killed winners, no-progress regret, runner outcomes
- Trade journal with filtering (tier, symbol)
- Auto-refresh every 10 seconds

## Event Flow
```
Binance/Bybit WS → Normalizer → Feature Engine → Signal Engine → Risk Manager
→ Sizing → Execution → Exit Engine → Analytics → Dashboard
```

## What NOT To Do
- Do NOT rebuild existing subsystems. Extend or refine them.
- Do NOT add symbols beyond BTCUSDT/ETHUSDT in v1.
- Do NOT implement short selling in v1.
- Do NOT document **bi-directional strategy** or “short strategy verified” for v1: the scoring engine emits **long-only** (`OPEN_LONG`) until a deliberate post–v1 change (see `docs/SHORT_STRATEGY_ROADMAP.md`). REST-level short orders are venue/infrastructure tests, not strategy proof.
- Do NOT connect real wallets in v1.
- Do NOT use LLM/AI for trade decisions.
- Do NOT use mid-price fills (bid/ask only).
- Do NOT use datetime.now() in computation paths.
- Do NOT use asyncio.sleep() for latency modeling.
- Do NOT bypass the 5-layer exit priority order.
- Do NOT let context score amplify signals (multiply only ≤ 1.0).

## Testing Strategy
- Unit tests: all pure logic (features, signals, risk, sizing, exits, analytics)
- Integration tests: full pipeline (signal → analytics)
- Replay tests: deterministic replay of same events → same outputs
- All tests must pass before any commit.

## Next Steps (Phase 1 Implementation)
1. Connect to live Binance/Bybit WebSocket streams
2. Validate data quality (message rates, gaps, latency)
3. Persist normalized trades and orderbook to TimescaleDB
4. Verify OHLCV continuous aggregates
5. Run data validation for 24h before proceeding to Phase 2
