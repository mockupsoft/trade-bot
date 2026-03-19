# Changelog

All notable changes to the Crypto Trading Engine.

## [0.1.0] — 2026-03-19

### Architecture & Core
- Event-driven architecture with Redis Streams inter-service communication
- 25+ Pydantic v2 frozen event models with schema enforcement
- Pydantic Settings with env var + TOML configuration
- structlog JSON logging with correlation IDs
- Typed exception hierarchy (CTEError base, 10+ subtypes)
- FastAPI app factory with health/metrics endpoints
- CLI entry point (`cte validate`, `cte start`)

### Market Data
- Binance USDⓈ-M Futures WebSocket connector (combined stream)
- Bybit v5 public WebSocket connector (subscribe model)
- Base connector with exponential backoff + jitter reconnection
- Canonical event normalizer (raw → venue-agnostic)
- Prometheus metrics: message rates, latency, connection state

### Streaming Feature Engine
- O(1) per-event incremental computation via SecondBucket aggregation
- 4 rolling windows: 10s, 30s, 60s, 5m
- 10 feature families: returns_z, momentum_z, taker_flow_imbalance, spread_bps, spread_widening, ob_imbalance, liquidation_imbalance, venue_divergence, freshness, execution_feasibility
- ReturnHistory/MomentumHistory for z-scores with periodic drift correction
- MarkPriceEvent, LiquidationEvent support
- TimescaleDB persistence with batched writes

### Scoring Signal Engine
- 6 sub-scores: momentum (0.35), orderflow (0.25), microstructure (0.20), liquidation (0.10), cross-venue (0.10), context (modifier)
- 5 hard gates: stale feed, max spread, max divergence, execution feasibility, warmup
- Weighted composite: primary_score × context_multiplier
- A/B/C/REJECT tier mapping (0.72/0.55/0.40 thresholds)
- Cooldown + hourly rate limiting
- Full audit trail: sub_scores, weights, gates, features_used

### Risk Manager
- 5 independent checks: position size, total exposure, daily drawdown, correlation, emergency stop
- Absolute veto power — risk manager always wins
- PortfolioState tracking with daily high-water mark

### Paper Execution Engine
- 3 fill models: SpreadCrossing (bid/ask + slippage), VWAPDepth (walk orderbook), WorstCase (2x slippage)
- PaperPosition state machine: PENDING → OPEN → CLOSED
- MFE/MAE tracking on every price tick
- R-multiple calculation (PnL / initial risk)
- Signal provenance carried to position (tier, composite_score, entry_reason)
- Event-clock timestamps (no datetime.now, no asyncio.sleep)

### 5-Layer Smart Exit Engine
- L1 Hard Risk: absolute stop, stale data, spread blowout
- L2 Thesis Failure: orderflow flip, momentum collapse, liquidation shift (with confirmation window)
- L3 No Progress: tier-based time budget (A=15m, B=8m, C=4m)
- L4 Winner Protection: trailing stop for proved winners
- L5 Runner Mode: wide trailing for exceptional winners, suspends no-progress
- Tier-specific patience profiles (A/B/C)
- saved_losers / killed_winners analytics hooks

### Demo/Testnet Execution
- Common ExecutionAdapter interface (ABC)
- BinanceTestnetAdapter: HMAC-SHA256 signed REST, /fapi/v1 endpoints
- BybitDemoAdapter: HMAC-SHA256 signed REST, /v5 unified API
- OrderStateMachine: 11 states, enforced transitions, audit trail
- TokenBucketRateLimiter with exponential backoff and 429 handling
- PositionReconciler: local vs venue state comparison (4 discrepancy types)

### Analytics & Monitoring
- Epoch system: crypto_v1_paper, crypto_v1_demo, crypto_v1_live, crypto_v1_shadow_short
- 15+ pure metric functions (win rate, expectancy, profit factor, drawdown, etc.)
- Breakdowns by symbol, venue, tier, exit reason
- Exit analysis: saved losers, killed winners, no-progress regret, runner outcomes
- Slippage drift comparison (paper vs demo/live)
- 9 Prometheus alert rules with 3-tier drawdown escalation
- 13 dashboard-friendly API endpoints

### Operations Platform
- OperationsController: emergency stop, pause/resume, per-symbol toggle
- 25 readiness gates: infrastructure (6) + execution parity (10) + edge proof (9)
- GO/NO-GO report framework: 7-section investment decision document
- ValidationCampaign: multi-day orchestrator with daily snapshots
- Operations runbook: emergency procedures, secret management, DB maintenance

### Dashboard UI
- 7-page professional web UI (FastAPI + Tailwind CSS + Chart.js + Alpine.js)
- Pages: Overview, Positions, Operations, Readiness, Research, Config, Alerts
- 18+ REST API endpoints, all server-tested
- 10-second auto-refresh, epoch selector
- No build step (CDN for all frontend dependencies)

### Database
- 15 tables including TimescaleDB hypertables
- Continuous aggregates for OHLCV (1m, 5m)
- Epoch-aware daily summary (35 columns)
- Trade log with full analytics fields

### Dashboard deploy
- `analytics` service: `CTE_DASHBOARD_MODE` (default `paper`) for live Binance public WS; `seed` / `demo` documented in `docs/DASHBOARD_MODES.md`
- Optional `CTE_MARKET_WS_URL` for `MarketDataFeed` WebSocket override

### Infrastructure
- Dockerfile (Python 3.12-slim, health checks)
- docker-compose.yml (10 services + Postgres + Redis + Prometheus + Grafana)
- docker-compose.dev.yml (infrastructure only for local dev)
- Prometheus scrape config for all services
- GitHub Actions CI (pytest + coverage + ruff lint)
