# CTE – Crypto Trading Engine

Production-grade, event-driven crypto trading engine for Binance USDⓈ-M Futures and Bybit v5 linear perpetuals.

## Overview

CTE is built from scratch as a modular, deterministic trading system with a strict phased rollout:
**market data validation → paper trading → demo/testnet → minimal live**.

### V1 Scope

| Constraint | Value |
|---|---|
| Symbols | BTCUSDT, ETHUSDT |
| Direction | LONG only |
| Primary Venue | Binance USDⓈ-M Futures |
| Secondary Venue | Bybit v5 public market data |
| Max Leverage | 3x |
| Wallet Connection | None in v1 |
| Short Execution | None in v1 |

### Key Properties

- **Event-driven**: All modules communicate via Redis Streams
- **Explainable**: Every trade decision carries a reason chain from signal to exit
- **Safe by default**: Independent risk manager with absolute veto power
- **Deterministic**: Same inputs produce same outputs — no hidden randomness
- **Observable**: Structured JSON logs, Prometheus metrics, Grafana dashboards

## Architecture

```
Binance WS ──┐                                    ┌── Risk Manager
Bybit WS   ──┤── Normalizer ── Features ── Signals──┤── Sizing
              │                                    └── Execution ── Exits ── Analytics
Context    ───┘
```

All inter-service communication flows through Redis Streams.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the complete design document including:

1. Service architecture with data flow diagrams
2. Repository structure
3. Database schema (PostgreSQL + TimescaleDB)
4. Event models (Pydantic v2)
5. Internal API definitions
6. Deployment topology
7. Failure modes catalog
8. Reconnection strategy
9. Observability plan
10. Phased rollout plan with acceptance criteria

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Web Framework | FastAPI |
| Async Runtime | asyncio |
| Message Bus | Redis Streams |
| Database | PostgreSQL + TimescaleDB |
| Data Models | Pydantic v2 |
| Logging | structlog (JSON) |
| Metrics | Prometheus / Grafana |

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (for infrastructure)

### Control UI (Vite + React)

The operator dashboard lives in `frontend/`. Development server listens on **port 8080** (see `frontend/vite.config.ts`).

```bash
cd frontend && npm install && npm run dev
# http://localhost:8080
```

Production assets: `npm run build` → `frontend/dist/`.

**Port note:** the Docker Compose `analytics` service also publishes **8080** by default. Run either the Vite dev server or that container on the same host port, or remap one of them.

### Development Setup

```bash
# Start infrastructure (PostgreSQL + TimescaleDB, Redis, Prometheus, Grafana)
docker compose -f deploy/docker-compose.dev.yml up -d

# Install Python dependencies
pip install -e ".[dev]"

# Run database migrations
python scripts/migrate.py --seed

# Validate configuration
cte validate

# Run tests
pytest tests/ -v

# Validate live market data (30 second test)
python scripts/validate_data.py --duration 30
```

### Run Full Stack (Docker)

```bash
docker compose -f deploy/docker-compose.yml up -d
```

## Project Structure

```
src/cte/
├── core/          # Settings, events, exceptions, logging, Redis streams
├── connectors/    # Binance & Bybit WebSocket connectors
├── normalizer/    # Raw → canonical event transformer
├── features/      # Technical indicators (RSI, EMA, VWAP, OB imbalance)
├── signals/       # Rule-based signal strategies
├── risk/          # Risk manager with veto power
├── sizing/        # Position sizing (fixed-fraction, Kelly)
├── execution/     # Paper / testnet / live execution
├── exits/         # Smart exits (trailing stop, TP/SL, timeout)
├── analytics/     # PnL, Sharpe, win rate, drawdown
├── monitoring/    # Prometheus metrics
├── api/           # FastAPI app factory, health checks
└── db/            # Schema, connection pool
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src/cte --cov-report=term-missing

# Run specific module tests
pytest tests/risk/ -v
pytest tests/features/ -v
```

## Phased Rollout

| Phase | Goal | Status |
|---|---|---|
| Phase 0 | Architecture & skeleton | **Current** |
| Phase 1 | Market data pipeline & validation | Planned |
| Phase 2 | Feature engine & signal generation | Planned |
| Phase 3 | Paper trading (full loop) | Planned |
| Phase 4 | Demo/testnet execution | Planned |
| Phase 5 | Minimal live trading | Planned |

Each phase has explicit acceptance criteria. A phase cannot begin until all criteria of the previous phase are met. See [ARCHITECTURE.md § Phased Rollout Plan](ARCHITECTURE.md#11-phased-rollout-plan) for details.

## Configuration

Configuration is loaded from:
1. Environment variables (highest priority)
2. `.env` files
3. `config/defaults.toml` (lowest priority)

Copy `.env.example` to `.env` and customize as needed.

## License

MIT
