# CTE – Crypto Trading Engine Architecture

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Service Architecture](#2-service-architecture)
3. [Repository Structure](#3-repository-structure)
4. [Database Schema](#4-database-schema)
5. [Event Models](#5-event-models)
6. [Internal APIs](#6-internal-apis)
7. [Deployment Topology](#7-deployment-topology)
8. [Failure Modes](#8-failure-modes)
9. [Reconnection Strategy](#9-reconnection-strategy)
10. [Observability Plan](#10-observability-plan)
11. [Phased Rollout Plan](#11-phased-rollout-plan)

---

## 1. System Overview

CTE is a production-grade, event-driven crypto trading engine designed for Binance USDⓈ-M Futures
(primary) and Bybit v5 linear perpetuals (secondary). V1 is scoped to:

- **Symbols**: BTCUSDT, ETHUSDT
- **Direction**: LONG only
- **Leverage**: Max 3x
- **Execution**: Paper → Demo/Testnet → Live (phased)
- **No** real wallet connection in v1

### Design Principles

| Principle | Implementation |
|---|---|
| Event-driven | All module communication via Redis Streams |
| Modular | Each module is an independent service with own API |
| Deterministic | Same inputs → same outputs; no hidden randomness |
| Explainable | Every trade carries a reason chain from signal to exit |
| Safe by default | Risk manager has absolute veto; default is "do nothing" |

### Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Language | Python 3.12 | Core runtime |
| Web Framework | FastAPI | Service APIs, health checks |
| Async Runtime | asyncio | Concurrent I/O |
| Message Bus | Redis Streams | Inter-service event bus |
| Primary DB | PostgreSQL + TimescaleDB | Time-series + relational storage |
| Serialization | Pydantic v2 + orjson | Type-safe data contracts |
| Logging | structlog (JSON) | Structured machine-readable logs |
| Metrics | Prometheus + Grafana | Monitoring and alerting |
| WebSocket | websockets library | Venue data connections |

---

## 2. Service Architecture

### Data Flow Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL DATA SOURCES                              │
│                                                                              │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────────┐   │
│  │ Binance USDⓈ-M   │    │ Bybit v5 Public  │    │ Whale Alert / Chain  │   │
│  │ fstream.binance   │    │ stream.bybit.com │    │ (context only)       │   │
│  │ .com/stream       │    │ /v5/public/linear│    │                      │   │
│  └────────┬─────────┘    └────────┬─────────┘    └──────────┬───────────┘   │
└───────────┼──────────────────────┼──────────────────────────┼───────────────┘
            │                      │                          │
            ▼                      ▼                          ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                        INGESTION LAYER                                        │
│                                                                               │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────────┐    │
│  │ Binance Connector │    │ Bybit Connector  │    │ Context Connector    │    │
│  │ (WS client)       │    │ (WS client)      │    │ (REST poller)        │    │
│  └────────┬─────────┘    └────────┬─────────┘    └──────────┬───────────┘    │
│           │                       │                          │                │
│           ▼                       ▼                          ▼                │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                    Redis Streams (raw events)                       │     │
│  │  cte:raw:trade    cte:raw:orderbook    cte:context:whale           │     │
│  └─────────────────────────────┬───────────────────────────────────────┘     │
└────────────────────────────────┼─────────────────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                      NORMALIZATION LAYER                                      │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                    Event Normalizer                                  │     │
│  │  • Validate schemas  • Map symbols  • Convert to canonical format   │     │
│  └─────────────────────────────┬───────────────────────────────────────┘     │
│                                │                                              │
│           ┌────────────────────┼────────────────────┐                        │
│           ▼                    ▼                     ▼                        │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                    Redis Streams (canonical events)                  │     │
│  │  cte:market:trade    cte:market:orderbook                           │     │
│  └─────────────────────────────┬───────────────────────────────────────┘     │
└────────────────────────────────┼─────────────────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                       COMPUTATION LAYER                                       │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                    Feature Engine                                    │     │
│  │  • RSI, EMA, VWAP  • Volume Profile  • Orderbook Imbalance         │     │
│  │  • Rolling windows in memory  • Snapshots to TimescaleDB            │     │
│  └─────────────────────────────┬───────────────────────────────────────┘     │
│                                │                                              │
│                                ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │  Redis Stream: cte:feature:vector                                   │     │
│  └─────────────────────────────┬───────────────────────────────────────┘     │
│                                │                                              │
│                                ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                    Signal Engine                                     │     │
│  │  • Rule-based strategies  • Confidence scoring  • Reason payloads   │     │
│  │  • Cooldown enforcement   • Context gating (whale/news)             │     │
│  └─────────────────────────────┬───────────────────────────────────────┘     │
│                                │                                              │
│                                ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │  Redis Stream: cte:signal:event                                     │     │
│  └─────────────────────────────┬───────────────────────────────────────┘     │
└────────────────────────────────┼─────────────────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                     RISK & EXECUTION LAYER                                    │
│                                                                               │
│  ┌─────────────────────────┐   ┌──────────────────────────────────────┐     │
│  │     Risk Manager        │──▶│  Redis: cte:risk:assessment          │     │
│  │ • Position limits       │   └──────────────────┬───────────────────┘     │
│  │ • Drawdown checks       │                      │ (if approved)           │
│  │ • Exposure limits       │                      ▼                         │
│  │ • Absolute veto power   │   ┌──────────────────────────────────────┐     │
│  └─────────────────────────┘   │     Sizing Engine                    │     │
│                                │ • Fixed fraction / Kelly              │     │
│                                │ • Max position limits                 │     │
│                                └──────────────────┬───────────────────┘     │
│                                                   │                         │
│                                                   ▼                         │
│                                ┌──────────────────────────────────────┐     │
│                                │     Execution Engine                  │     │
│                                │ • Paper mode (simulated fills)        │     │
│                                │ • Testnet mode (Binance testnet)      │     │
│                                │ • Live mode (real orders)             │     │
│                                └──────────────────┬───────────────────┘     │
│                                                   │                         │
│                                                   ▼                         │
│                                ┌──────────────────────────────────────┐     │
│                                │     Smart Exit Engine                 │     │
│                                │ • Trailing stops • Time exits         │     │
│                                │ • TP/SL • Invalidation               │     │
│                                └──────────────────────────────────────┘     │
└───────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                      ANALYTICS & MONITORING                                   │
│                                                                               │
│  ┌───────────────────────┐    ┌───────────────────────────────────────┐      │
│  │   Analytics Engine    │    │   Monitoring Stack                     │      │
│  │ • PnL calculation     │    │ • Prometheus metrics                   │      │
│  │ • Win rate / Sharpe   │    │ • Grafana dashboards                   │      │
│  │ • Drawdown curves     │    │ • Alert rules                          │      │
│  │ • Trade journal       │    │ • Health aggregation                   │      │
│  └───────────────────────┘    └───────────────────────────────────────┘      │
│                                                                               │
│  Storage: PostgreSQL + TimescaleDB                                            │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Service Boundaries

Each service runs as an independent process with its own:
- FastAPI application (health + metrics endpoints)
- Redis Stream consumer group
- Database connection pool (where needed)
- Prometheus metrics exporter

Services communicate ONLY through Redis Streams. No direct service-to-service calls.

### Service Registry

| Service | Redis Streams (Consumes) | Redis Streams (Produces) | DB Access | API Port |
|---|---|---|---|---|
| binance-connector | – | cte:raw:trade, cte:raw:orderbook | No | 8001 |
| bybit-connector | – | cte:raw:trade, cte:raw:orderbook | No | 8002 |
| normalizer | cte:raw:* | cte:market:trade, cte:market:orderbook | Write (trades, orderbook) | 8010 |
| feature-engine | cte:market:* | cte:feature:vector | Write (features) | 8020 |
| signal-engine | cte:feature:vector, cte:context:* | cte:signal:event | Write (signals) | 8030 |
| risk-manager | cte:signal:event | cte:risk:assessment | Read (positions, daily_pnl) | 8040 |
| sizing-engine | cte:risk:assessment (approved) | cte:sizing:order | Read (positions) | 8050 |
| execution-engine | cte:sizing:order | cte:execution:order | Write (orders, positions) | 8060 |
| exit-engine | cte:execution:order, cte:market:trade | cte:exit:event | Write (exits, positions) | 8070 |
| analytics | cte:exit:event, cte:execution:order | – | Write (daily_pnl) | 8080 |

---

## 3. Repository Structure

```
cte/
├── .cursorrules                 # AI development rules
├── agents.md                    # Agent behavior specification
├── ARCHITECTURE.md              # This document
├── README.md                    # Project overview
├── pyproject.toml               # Python project config & dependencies
├── config/
│   └── defaults.toml            # Default configuration values
├── src/
│   └── cte/
│       ├── __init__.py
│       ├── core/                # Shared infrastructure
│       │   ├── __init__.py
│       │   ├── settings.py      # Pydantic Settings configuration
│       │   ├── events.py        # Canonical event models (Pydantic v2)
│       │   ├── exceptions.py    # Exception hierarchy
│       │   ├── logging.py       # structlog JSON logging setup
│       │   ├── streams.py       # Redis Streams producer/consumer
│       │   └── cli.py           # Click CLI entry point
│       ├── connectors/          # Venue WebSocket connectors
│       │   ├── __init__.py
│       │   ├── base.py          # Abstract connector with reconnection
│       │   ├── binance.py       # Binance USDⓈ-M Futures WS
│       │   └── bybit.py         # Bybit v5 public linear WS
│       ├── normalizer/          # Raw → canonical event transformer
│       │   ├── __init__.py
│       │   └── engine.py        # Normalization logic
│       ├── features/            # Technical indicator computation
│       │   ├── __init__.py
│       │   ├── engine.py        # Feature engine coordinator
│       │   ├── indicators.py    # RSI, EMA, VWAP calculations
│       │   └── window.py        # Rolling window management
│       ├── signals/             # Trade signal generation
│       │   ├── __init__.py
│       │   ├── engine.py        # Signal engine coordinator
│       │   └── strategies.py    # Rule-based signal strategies
│       ├── risk/                # Risk management with veto power
│       │   ├── __init__.py
│       │   ├── manager.py       # Risk manager coordinator
│       │   └── checks.py        # Individual risk check implementations
│       ├── sizing/              # Position sizing
│       │   ├── __init__.py
│       │   └── engine.py        # Fixed-fraction / Kelly sizing
│       ├── execution/           # Order execution
│       │   ├── __init__.py
│       │   ├── engine.py        # Execution coordinator
│       │   └── paper.py         # Paper trading simulator
│       ├── exits/               # Smart exit management
│       │   ├── __init__.py
│       │   └── engine.py        # Exit condition monitoring
│       ├── analytics/           # Post-trade analytics
│       │   ├── __init__.py
│       │   └── engine.py        # PnL, Sharpe, drawdown calculations
│       ├── monitoring/          # Observability
│       │   ├── __init__.py
│       │   └── metrics.py       # Prometheus metric definitions
│       ├── api/                 # Shared API components
│       │   ├── __init__.py
│       │   ├── app.py           # FastAPI app factory
│       │   └── health.py        # Health check + metrics routes
│       └── db/                  # Database access
│           ├── __init__.py
│           ├── pool.py          # asyncpg connection pool
│           └── schema.py        # SQL schema definitions
├── tests/                       # Mirrors src/ layout
│   ├── conftest.py
│   ├── core/
│   │   └── test_events.py
│   ├── connectors/
│   ├── normalizer/
│   │   └── test_normalizer.py
│   ├── features/
│   ├── signals/
│   ├── risk/
│   ├── sizing/
│   ├── execution/
│   ├── exits/
│   ├── analytics/
│   ├── monitoring/
│   └── integration/
├── migrations/                  # Database migration scripts
├── deploy/                      # Docker and deployment configs
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── docker-compose.dev.yml
├── dashboards/
│   └── grafana/                 # Grafana dashboard JSON definitions
├── scripts/                     # Utility scripts
│   ├── migrate.py               # Run database migrations
│   └── validate_data.py         # Market data validation tool
└── .env.example                 # Environment variable template
```

---

## 4. Database Schema

### Entity-Relationship Overview

```
cte.symbols (reference)
cte.venues (reference)

cte.trades (hypertable, time-series) ─────────────┐
cte.orderbook_snapshots (hypertable, time-series)  │
                                                    ├──▶ cte.ohlcv_1m (continuous agg)
                                                    │    cte.ohlcv_5m (continuous agg)
cte.feature_snapshots (hypertable, time-series)     │
                                                    │
cte.signals (hypertable) ◀─ reason payloads         │
        │                                           │
        ▼                                           │
cte.risk_decisions (hypertable)                     │
        │                                           │
        ▼                                           │
cte.orders (hypertable) ──────────────────────────┐│
        │                                          ││
        ▼                                          ▼▼
cte.positions ◀────────────────── cte.exits (hypertable)
        │
        ▼
cte.daily_pnl (aggregated analytics)
```

### Key Design Decisions

1. **TimescaleDB hypertables** for all time-series data (trades, orderbook, features, signals,
   orders, exits). Automatic chunk management and compression.
2. **Continuous aggregates** for OHLCV candles (1m, 5m). Computed incrementally by TimescaleDB.
3. **Positions table** is standard PostgreSQL (not hypertable) because it tracks entity lifecycle,
   not time-series.
4. **No ORM**. Raw SQL with asyncpg for performance and explicit query control.
5. **NUMERIC type** for all prices and quantities. Never use FLOAT for money.
6. **JSONB columns** for extensible metadata (features.extra, positions.metadata).

### Full Schema

See `src/cte/db/schema.py` for complete SQL definitions including:
- All CREATE TABLE statements
- TimescaleDB hypertable creation
- Indexes for common query patterns
- Continuous aggregate materialized views
- Seed data for symbols and venues

---

## 5. Event Models

All events are defined as frozen Pydantic v2 models in `src/cte/core/events.py`.

### Event Hierarchy

```
BaseEvent (event_id, timestamp, source)
├── RawTradeEvent           – Raw trade from venue WS
├── RawOrderbookEvent       – Raw orderbook from venue WS
├── TradeEvent              – Canonical normalized trade
├── OrderbookSnapshotEvent  – Canonical normalized orderbook
├── FeatureVector           – Computed technical indicators
├── SignalEvent             – Trade signal with reason payload
├── RiskAssessmentEvent     – Risk manager decision
├── SizedOrderEvent         – Order with calculated position size
├── OrderEvent              – Order lifecycle event
├── ExitEvent               – Position exit with reason
├── PositionSnapshot        – Current position state
├── WhaleAlertEvent         – On-chain whale transfer (context)
└── OnChainContextEvent     – Aggregated chain data (context)
```

### Event Contracts

Every event guarantees:
- **Unique ID**: UUID v4 `event_id`
- **Timestamp**: UTC timezone-aware `datetime`
- **Source**: Which module created the event
- **Immutability**: All models are `frozen=True`
- **Schema enforcement**: `extra="forbid"` rejects unknown fields
- **JSON serialization**: via `model_dump(mode="json")` + orjson

### Redis Stream Keys

| Stream Key | Event Type | Producer | Consumer(s) |
|---|---|---|---|
| `cte:raw:trade` | RawTradeEvent | Connectors | Normalizer |
| `cte:raw:orderbook` | RawOrderbookEvent | Connectors | Normalizer |
| `cte:market:trade` | TradeEvent | Normalizer | Feature Engine, Exit Engine |
| `cte:market:orderbook` | OrderbookSnapshotEvent | Normalizer | Feature Engine |
| `cte:feature:vector` | FeatureVector | Feature Engine | Signal Engine |
| `cte:signal:event` | SignalEvent | Signal Engine | Risk Manager |
| `cte:risk:assessment` | RiskAssessmentEvent | Risk Manager | Sizing Engine |
| `cte:sizing:order` | SizedOrderEvent | Sizing Engine | Execution Engine |
| `cte:execution:order` | OrderEvent | Execution Engine | Exit Engine, Analytics |
| `cte:exit:event` | ExitEvent | Exit Engine | Analytics |
| `cte:position:snapshot` | PositionSnapshot | Execution Engine | Risk Manager, Exit Engine |
| `cte:context:whale` | WhaleAlertEvent | Context Connector | Signal Engine |
| `cte:context:onchain` | OnChainContextEvent | Context Connector | Signal Engine |

### Signal Reason Payload

Every signal carries an explainable `SignalReason`:

```json
{
  "primary_trigger": "ema_crossover_bullish",
  "supporting_factors": [
    "rsi_oversold_recovery",
    "volume_above_average",
    "orderbook_bid_imbalance"
  ],
  "context_flags": {
    "whale_accumulation": true,
    "funding_rate_neutral": true
  },
  "human_readable": "EMA 12/26 bullish crossover with RSI recovering from oversold (32→45), volume 1.8x average, 65% bid-side orderbook imbalance. Whale accumulation detected in last 4h."
}
```

---

## 6. Internal APIs

Each service exposes a FastAPI application on its designated port.

### Common Endpoints (All Services)

| Method | Path | Description |
|---|---|---|
| GET | `/api/{service}/health` | Full health check with component statuses |
| GET | `/api/{service}/health/live` | Kubernetes liveness probe |
| GET | `/api/{service}/health/ready` | Kubernetes readiness probe |
| GET | `/api/{service}/metrics` | Prometheus metrics (text format) |

### Connector Service API (Port 8001/8002)

| Method | Path | Description |
|---|---|---|
| GET | `/api/connector/status` | Connection state, message rates, latency |
| POST | `/api/connector/reconnect` | Force reconnection |
| GET | `/api/connector/streams` | Active stream subscriptions |

### Normalizer Service API (Port 8010)

| Method | Path | Description |
|---|---|---|
| GET | `/api/normalizer/stats` | Normalization counts, error rates |
| GET | `/api/normalizer/rejected` | Recent rejected events |

### Feature Engine API (Port 8020)

| Method | Path | Description |
|---|---|---|
| GET | `/api/features/current/{symbol}` | Latest feature vector for symbol |
| GET | `/api/features/history/{symbol}` | Feature history (paginated) |
| GET | `/api/features/windows` | Active rolling window states |

### Signal Engine API (Port 8030)

| Method | Path | Description |
|---|---|---|
| GET | `/api/signals/recent` | Recent signals with reasons |
| GET | `/api/signals/stats` | Signal generation statistics |
| GET | `/api/signals/cooldowns` | Active cooldown timers |

### Risk Manager API (Port 8040)

| Method | Path | Description |
|---|---|---|
| GET | `/api/risk/state` | Current risk state (exposure, drawdown) |
| GET | `/api/risk/decisions` | Recent risk decisions |
| GET | `/api/risk/limits` | Active risk limits and utilization |
| POST | `/api/risk/emergency-stop` | Trigger emergency stop (kills all positions) |

### Execution Engine API (Port 8060)

| Method | Path | Description |
|---|---|---|
| GET | `/api/execution/orders` | Active and recent orders |
| GET | `/api/execution/positions` | Open positions |
| GET | `/api/execution/positions/{id}` | Position detail with full trade chain |

### Analytics API (Port 8080)

| Method | Path | Description |
|---|---|---|
| GET | `/api/analytics/pnl/daily` | Daily PnL breakdown |
| GET | `/api/analytics/pnl/cumulative` | Cumulative PnL curve |
| GET | `/api/analytics/metrics` | Sharpe, win rate, drawdown, etc. |
| GET | `/api/analytics/trades` | Trade journal with reason payloads |

---

## 7. Deployment Topology

### Development (Local)

```
┌─────────────────────────────────────────────────────┐
│                  docker-compose.dev.yml              │
│                                                      │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐       │
│  │ PostgreSQL │  │   Redis   │  │  Grafana  │       │
│  │ +Timescale │  │  7.x      │  │  :3000    │       │
│  │  :5432     │  │  :6379    │  └───────────┘       │
│  └───────────┘  └───────────┘  ┌───────────┐       │
│                                │ Prometheus │       │
│                                │  :9090     │       │
│                                └───────────┘       │
└─────────────────────────────────────────────────────┘

CTE services run natively (not containerized) for fast iteration:
  $ python -m cte.connectors.binance   # port 8001
  $ python -m cte.normalizer           # port 8010
  $ python -m cte.features             # port 8020
  ... etc
```

### Staging (Docker Compose)

All services containerized with a single `docker-compose.yml`:
- Each CTE service in its own container
- Shared network for inter-service health checks
- Volume mounts for logs
- Resource limits per container

### Production (Single VPS)

Target: A single VPS (4 CPU, 8GB RAM) running all services via Docker Compose.
Not Kubernetes — unnecessary complexity for this scope.

```
VPS (4 CPU, 8GB RAM)
├── docker-compose.yml
├── PostgreSQL + TimescaleDB (dedicated 2GB RAM)
├── Redis (dedicated 512MB RAM)
├── 10 CTE services (shared 4GB RAM)
├── Prometheus (256MB)
├── Grafana (256MB)
└── Nginx reverse proxy (TLS termination)
```

### Resource Estimates (Per Service)

| Service | CPU | Memory | Disk I/O |
|---|---|---|---|
| binance-connector | 0.1 core | 64MB | Low |
| bybit-connector | 0.1 core | 64MB | Low |
| normalizer | 0.2 core | 128MB | Medium |
| feature-engine | 0.3 core | 256MB | Medium |
| signal-engine | 0.2 core | 128MB | Low |
| risk-manager | 0.1 core | 64MB | Low |
| sizing-engine | 0.05 core | 32MB | Low |
| execution-engine | 0.2 core | 128MB | Medium |
| exit-engine | 0.1 core | 64MB | Low |
| analytics | 0.1 core | 128MB | Medium |

---

## 8. Failure Modes

### Failure Mode Catalog

| ID | Failure | Impact | Detection | Recovery |
|---|---|---|---|---|
| F1 | Binance WS disconnect | No primary market data | Connection state metric → 0 | Auto-reconnect with backoff |
| F2 | Bybit WS disconnect | No secondary data | Connection state metric → 0 | Auto-reconnect with backoff |
| F3 | Redis unavailable | All inter-service communication halted | Health check fails | Retry with backoff; services buffer in memory (limited) |
| F4 | PostgreSQL unavailable | No writes; reads from cache | DB pool health check | Retry with backoff; services continue with cached state |
| F5 | Normalizer crashes | Raw events accumulate in Redis | Consumer lag alert | Auto-restart via Docker; replay from Redis stream |
| F6 | Feature engine lag | Stale feature vectors | Feature age metric | Auto-restart; rebuild state from DB snapshots |
| F7 | Signal engine crash | No new signals | Consumer lag alert | Auto-restart; missed signals are acceptable (safety) |
| F8 | Risk manager crash | No signals approved | CRITICAL: all signals blocked | Auto-restart with highest priority |
| F9 | Execution engine crash | Open orders not managed | Order state alerts | Auto-restart; reconcile open positions from DB |
| F10 | Exit engine crash | Positions not monitored for exits | Position age alert | Auto-restart; emergency stop if too many unmonitored |
| F11 | Malformed market data | Bad feature calculations | Validation error counter | Drop event; alert if rate exceeds threshold |
| F12 | Clock skew between services | Event ordering issues | NTP monitoring | NTP sync; use event_id ordering, not timestamps |
| F13 | Redis Stream memory full | Stream writes rejected | Redis memory alerts | Increase maxlen trim; archive old events |
| F14 | Cascading failure | Multiple services down | Aggregate health check | Emergency stop all positions; restart services in order |

### Circuit Breaker Policy

External API calls (venue REST, Whale Alert, Etherscan) use circuit breaker pattern:

| State | Behavior |
|---|---|
| CLOSED | Normal operation; track error rate |
| OPEN | Reject all calls; return cached/default data. Opens after 5 consecutive failures. |
| HALF-OPEN | Allow 1 probe request. If success → CLOSED. If fail → OPEN. Probe interval: 30s. |

### Graceful Degradation

1. **Bybit down, Binance up**: Continue with single venue. Signal confidence reduced.
2. **Feature engine lagging**: Signal engine uses last known features. Confidence penalty applied.
3. **Whale/context data unavailable**: Signal engine ignores context flags. No impact on primary signals.
4. **Analytics down**: No impact on trading. Data accumulates in Redis for replay.

---

## 9. Reconnection Strategy

### WebSocket Reconnection (Binance)

```
State: DISCONNECTED
  │
  ▼
  connect(wss://fstream.binance.com/stream?streams=...)
  │
  ├── Success → CONNECTED (reset retry counter)
  │               │
  │               ├── WS close / error → RECONNECTING
  │               │                         │
  │               │                         ▼
  │               │                    delay = min(base * 2^attempt, max) + jitter
  │               │                         │
  │               │                         ▼
  │               │                    connect() → loop back
  │               │
  │               └── ping timeout (>360s no message) → RECONNECTING
  │
  └── Failure → RECONNECTING (same backoff)
```

**Binance-specific constraints:**
- Max 5 WS connections per IP
- Ping frame every 180s (server sends)
- Connection auto-closes after 24h → reconnect
- Combined stream URL: subscribe via URL params, no explicit subscribe message
- Rate limit: 10 connections per 5 seconds

### WebSocket Reconnection (Bybit)

```
Same state machine as Binance, with these differences:
- Ping interval: 20s (must send {"op":"ping"} heartbeat)
- Max 10 subscriptions per connection
- Max 500 connections per 5 minutes
- Explicit subscribe message required after connect
- Orderbook: snapshot on connect, then deltas → must re-snapshot on reconnect
```

### Backoff Parameters

| Parameter | Value |
|---|---|
| Base delay | 1.0 second |
| Max delay | 60.0 seconds |
| Jitter | 0–10% of computed delay |
| Formula | `delay = min(1.0 * 2^attempt, 60.0) + random(0, delay * 0.1)` |
| Max attempts | Unlimited (reconnect forever) |

### Redis Reconnection

| Scenario | Strategy |
|---|---|
| Connection lost | Built-in redis-py retry with backoff |
| Consumer group lost | Re-create group; start from last acknowledged |
| Stream deleted | Re-create stream on next publish |

### Database Reconnection

| Scenario | Strategy |
|---|---|
| Connection pool exhausted | Queue requests; alert after 10s wait |
| Connection dropped | asyncpg auto-reconnects from pool |
| Database restart | Pool detects stale connections; refreshes |

---

## 10. Observability Plan

### Logging

| Aspect | Implementation |
|---|---|
| Library | structlog with JSON renderer |
| Output | stdout (captured by Docker/journald) |
| Fields | timestamp (UTC ISO), level, service, event, context |
| Correlation | event_id flows through all downstream events |
| Sensitive data | Never log API keys, passwords, or full orderbook data |
| Log levels | DEBUG (dev), INFO (default), WARNING (degraded), ERROR (failures), CRITICAL (data loss) |

### Metrics (Prometheus)

#### Connector Metrics
| Metric | Type | Labels |
|---|---|---|
| `cte_ws_messages_total` | Counter | venue, stream |
| `cte_ws_connection_state` | Gauge | venue |
| `cte_ws_reconnects_total` | Counter | venue |
| `cte_ws_message_latency_seconds` | Histogram | venue |

#### Normalizer Metrics
| Metric | Type | Labels |
|---|---|---|
| `cte_normalize_total` | Counter | venue, event_type |
| `cte_normalize_errors_total` | Counter | venue, error_type |
| `cte_normalize_latency_seconds` | Histogram | event_type |

#### Feature Engine Metrics
| Metric | Type | Labels |
|---|---|---|
| `cte_feature_compute_total` | Counter | symbol |
| `cte_feature_compute_latency_seconds` | Histogram | symbol |
| `cte_feature_window_size` | Gauge | symbol |
| `cte_feature_staleness_seconds` | Gauge | symbol |

#### Signal Engine Metrics
| Metric | Type | Labels |
|---|---|---|
| `cte_signal_generated_total` | Counter | symbol, action |
| `cte_signal_confidence` | Histogram | symbol |
| `cte_signal_cooldown_active` | Gauge | symbol |

#### Risk Manager Metrics
| Metric | Type | Labels |
|---|---|---|
| `cte_risk_decisions_total` | Counter | symbol, decision |
| `cte_risk_exposure_pct` | Gauge | – |
| `cte_risk_daily_drawdown_pct` | Gauge | – |
| `cte_risk_veto_rate` | Gauge | – |

#### Execution Metrics
| Metric | Type | Labels |
|---|---|---|
| `cte_orders_total` | Counter | symbol, status |
| `cte_order_fill_latency_seconds` | Histogram | venue |
| `cte_positions_open` | Gauge | symbol |

#### Analytics Metrics
| Metric | Type | Labels |
|---|---|---|
| `cte_pnl_total_usd` | Gauge | – |
| `cte_pnl_daily_usd` | Gauge | – |
| `cte_win_rate` | Gauge | – |
| `cte_sharpe_ratio` | Gauge | – |
| `cte_max_drawdown_pct` | Gauge | – |

### Grafana Dashboards

| Dashboard | Panels |
|---|---|
| **System Overview** | Service health grid, Redis Stream lag, DB connection pool, memory/CPU |
| **Market Data** | Message rates by venue, normalization error rates, data latency |
| **Trading** | Open positions, signal rate, risk veto rate, PnL curve |
| **Performance** | Daily PnL, cumulative returns, Sharpe, win rate, drawdown |
| **Alerts** | Active alerts, recent incidents, SLA compliance |

### Alerting Rules

| Alert | Condition | Severity | Action |
|---|---|---|---|
| WS Disconnected > 60s | `cte_ws_connection_state == 0` for 60s | WARNING | Notify |
| WS Disconnected > 5min | `cte_ws_connection_state == 0` for 5min | CRITICAL | Page |
| Normalization Error Spike | Error rate > 5% in 5min window | WARNING | Investigate |
| Feature Staleness > 5min | `cte_feature_staleness_seconds > 300` | WARNING | Restart feature engine |
| Risk Veto Rate > 90% | Over 1h window | WARNING | Review signal logic |
| Daily Drawdown > 2% | `cte_risk_daily_drawdown_pct > 0.02` | WARNING | Reduce position sizing |
| Daily Drawdown > 3% | `cte_risk_daily_drawdown_pct > 0.03` | CRITICAL | Halt new positions |
| Emergency Drawdown > 5% | `cte_risk_daily_drawdown_pct > 0.05` | CRITICAL | Close all positions |
| Redis Stream Lag > 1000 | Consumer group pending > 1000 | WARNING | Scale consumers |
| DB Connection Pool Exhausted | Available connections == 0 | CRITICAL | Increase pool / investigate |

---

## 11. Phased Rollout Plan

### Phase 0: Architecture & Skeleton (Current)

**Goal**: Establish project structure, conventions, and data contracts.

**Deliverables**:
- [x] Repository structure
- [x] .cursorrules and agents.md
- [x] Configuration system (Pydantic Settings + TOML)
- [x] Event model definitions (Pydantic v2)
- [x] Database schema (SQL + TimescaleDB)
- [x] Exception hierarchy
- [x] Logging setup (structlog JSON)
- [x] Redis Streams abstraction
- [x] API factory with health/metrics
- [x] Architecture documentation

**Acceptance Criteria**:
- `pyproject.toml` installs cleanly
- All event models serialize/deserialize correctly
- Settings load from env vars and defaults
- Health endpoint returns 200

---

### Phase 1: Market Data Pipeline

**Goal**: Connect to venues, normalize data, validate quality.

**Deliverables**:
- Binance USDⓈ-M Futures WS connector (combined stream)
- Bybit v5 public WS connector (publicTrade + orderbook)
- Event normalizer (raw → canonical)
- Data validation scripts
- Trade and orderbook data persisted to TimescaleDB
- OHLCV continuous aggregates working

**Acceptance Criteria**:
- [ ] Binance WS connects and stays connected for 24h without manual intervention
- [ ] Bybit WS connects and stays connected for 24h without manual intervention
- [ ] Automatic reconnection works within 60s for both venues
- [ ] Normalized trades match expected format (100% schema compliance)
- [ ] Orderbook snapshots update at expected frequency
- [ ] OHLCV 1m/5m aggregates match reference data (±0.01%)
- [ ] Data latency < 500ms (venue timestamp to DB write)
- [ ] No data gaps > 5 seconds during normal operation

---

### Phase 2: Feature & Signal Engine

**Goal**: Compute technical indicators and generate explainable signals.

**Deliverables**:
- Feature engine with RSI, EMA(12/26), VWAP, volume profile, OB imbalance
- Rolling window management with DB-backed recovery
- Signal engine with rule-based strategies
- Signal reason payload system
- Context event integration (whale/news as gating only)

**Acceptance Criteria**:
- [ ] Feature calculations match reference implementation (numpy/ta-lib) within ε
- [ ] Features update within 1s of new market data
- [ ] Feature engine recovers state from DB on restart
- [ ] Signals include valid reason payloads (human-readable)
- [ ] Signal cooldown prevents spam (< 10 signals/hour)
- [ ] Context flags are read-only and never trigger trades alone
- [ ] Replay test: same historical data → same signals (deterministic)

---

### Phase 3: Paper Trading

**Goal**: Complete trading loop with simulated execution.

**Deliverables**:
- Risk manager with all checks (position, drawdown, exposure, correlation)
- Position sizing (fixed-fraction, half-Kelly ready)
- Paper execution engine (simulated fills with slippage)
- Smart exit engine (trailing stop, TP/SL, timeout, invalidation)
- Analytics engine (PnL, Sharpe, win rate, drawdown)
- Full event chain: signal → risk → sizing → execution → exit → analytics

**Acceptance Criteria**:
- [ ] Risk manager vetoes signals exceeding limits (100% enforcement)
- [ ] Emergency stop triggers at 5% daily drawdown
- [ ] Position sizes never exceed 5% of portfolio
- [ ] Paper fills include realistic slippage (5 bps default)
- [ ] Every trade has a complete reason chain (signal → exit)
- [ ] Exit engine triggers correct exit types
- [ ] Analytics PnL matches sum of individual trade PnLs
- [ ] Paper trading runs for 7 consecutive days without crashes
- [ ] All data is explainable and auditable from DB

---

### Phase 4: Demo/Testnet

**Goal**: Execute real orders on Binance testnet.

**Deliverables**:
- Binance testnet REST API integration
- Real order lifecycle management (submit, track, cancel)
- Order reconciliation (local state vs venue state)
- Testnet-specific error handling

**Acceptance Criteria**:
- [ ] Testnet orders submit and fill successfully
- [ ] Order status updates within 2s of venue confirmation
- [ ] Local position state matches testnet account state
- [ ] Handles testnet-specific failures gracefully
- [ ] Runs 7 consecutive days on testnet without manual intervention
- [ ] PnL tracking matches testnet account balance changes

---

### Phase 5: Minimal Live

**Goal**: Live trading with minimal capital, BTCUSDT only, 1x leverage.

**Deliverables**:
- Live API key management (secure, rotatable)
- Real Binance USDⓈ-M Futures execution
- Capital limits (hard-coded max $1000)
- Enhanced monitoring and alerting
- Manual kill switch (API + CLI)

**Acceptance Criteria**:
- [ ] Live execution with real funds (< $100 initial capital)
- [ ] Leverage locked to 1x
- [ ] Maximum single position: $50
- [ ] Emergency stop tested and functional
- [ ] Manual kill switch tested and functional
- [ ] 24/7 monitoring alerts configured
- [ ] First 7 days: manual review of every trade
- [ ] Drawdown limits enforced in live (2% daily warning, 3% halt, 5% close all)

---

### Phase Gate Rules

A phase can only begin when ALL acceptance criteria of the previous phase are met.

| Gate | Requirement |
|---|---|
| Phase 0 → 1 | Architecture review complete, all models validate |
| Phase 1 → 2 | 24h data collection stable, < 0.1% data gaps |
| Phase 2 → 3 | Deterministic replay test passes, features match reference |
| Phase 3 → 4 | 7 days paper trading, positive expectancy or explainable losses |
| Phase 4 → 5 | 7 days testnet, order reconciliation 100%, no unhandled errors |
