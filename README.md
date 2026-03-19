# CTE — Crypto Trading Engine

[![CI](https://github.com/mockupsoft/trade-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/mockupsoft/trade-bot/actions/workflows/ci.yml)
![Tests](https://img.shields.io/badge/tests-419%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-72%25-yellow)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Production-grade, event-driven crypto trading engine for Binance USDⓈ-M Futures and Bybit v5 perpetuals. Built from scratch — no copy-paste from other bots.

## Current Status

| Phase | Status | Evidence |
|---|---|---|
| Phase 0: Architecture & skeleton | **Complete** | 53 modules, 8 design docs |
| Phase 1: Streaming Feature Engine | **Complete** | 10 features, 4 timeframes, 86 tests |
| Phase 2: Scoring Signal Engine | **Complete** | 6 sub-scores, 5 gates, 61 tests |
| Phase 3a: Paper Execution | **Complete** | Bid/ask fills, MFE/MAE, 50 tests |
| Phase 3b: Smart Exit Engine | **Complete** | 5 layers, tier patience, 41 tests |
| Phase 4: Demo/Testnet Execution | **Complete** | Binance+Bybit adapters, 37 tests |
| Phase 5: Analytics & Monitoring | **Complete** | Epoch-aware, 15+ metrics, 52 tests |
| Operations Platform | **Complete** | Kill switch, readiness gates, GO/NO-GO |
| Dashboard UI | **Complete** | 7-page ops + research platform |
| **Validation Campaign** | **Next** | 7-day paper/demo parallel run |

## Architecture

```
Binance WS ──┐                                              ┌── Risk Manager (veto)
Bybit WS   ──┤── Normalizer ── Feature Engine (O(1)) ──    │
              │                  10 features × 4 TFs   ── Signal Engine ──┤── Sizing (Kelly/FF)
Context    ───┘                                              │
                                                             └── Execution ──┐
                                                                             ├── Paper (bid/ask)
                                                                             ├── Testnet (Binance)
                                                                             └── Demo (Bybit)
                                                                                    │
                                                              5-Layer Exit ◄────────┘
                                                                   │
                                                              Analytics (epoch-aware)
                                                                   │
                                                              Dashboard (7 pages)
```

## What's Built (Not Planned — Built)

### 53 Python Modules

```
src/cte/
├── core/           7 modules   Settings, events (25+ models), logging, streams, exceptions
├── connectors/     3 modules   Binance USDⓈ-M WS, Bybit v5 WS, base with reconnection
├── normalizer/     1 module    Raw → canonical event transformer
├── features/       7 modules   O(1) incremental, SecondBucket aggregation, 10 features
├── signals/        5 modules   6 sub-scores, 5 hard gates, A/B/C tiers, weighted composite
├── risk/           2 modules   5 checks with absolute veto power
├── sizing/         1 module    Fixed-fraction + Kelly criterion
├── execution/      7 modules   Paper (bid/ask), Binance testnet, Bybit demo, FSM, rate limiter
├── exits/          3 modules   5-layer model: hard risk → thesis fail → no progress → winner → runner
├── analytics/      3 modules   Epoch system, 15+ metric functions, slippage drift
├── monitoring/     2 modules   9 alert rules, Prometheus metrics
├── ops/            4 modules   Kill switch, readiness gates (25), GO/NO-GO framework
├── dashboard/      2 modules   7-page professional UI
├── api/            3 modules   18+ REST endpoints
└── db/             2 modules   TimescaleDB schema (15 tables)
```

### 419 Tests (27 test files)

```
tests/
├── analytics/       25 tests   Metrics functions, epoch engine, trade recording
├── core/            22 tests   Event serialization, settings validation
├── execution/       50 tests   Fill models, position lifecycle, paper engine, state machine
│                    37 tests   Adapter interface, rate limiter, reconciliation
├── exits/           41 tests   5 layers × tier behavior, engine priority, replay
├── features/        86 tests   Accumulators, formulas, indicators, streaming engine
├── integration/      3 tests   Full pipeline: signal → risk → sizing → execution → analytics
├── monitoring/      10 tests   Alert rules, escalation
├── normalizer/       7 tests   Trade/orderbook normalization
├── ops/             22 tests   Kill switch, readiness gates, validation campaigns, GO/NO-GO
├── risk/            20 tests   Risk checks, manager integration, emergency stop
├── signals/         61 tests   Scorers, gates, composite, tier mapping, determinism
└── sizing/           8 tests   Sizing bounds, Kelly mode
```

### 18+ Server-Tested API Endpoints

All endpoints verified via HTTP (not just unit tests):

| Endpoint | Method | Response |
|---|---|---|
| `/` | GET | 22KB HTML dashboard (7 pages) |
| `/api/analytics/summary` | GET | 15+ metrics with breakdowns |
| `/api/analytics/epochs` | GET | 4 epochs (paper/demo/live/shadow) |
| `/api/analytics/trades` | GET | Trade journal with filtering |
| `/api/ops/status` | GET | Mode, symbols, events, history |
| `/api/ops/emergency_stop` | POST | Halts all trading |
| `/api/readiness/edge_proof` | GET | 9 edge proof gates |
| `/api/report/go_no_go` | GET | 7-section decision report |
| `/api/config` | GET | Read-only active configuration |

### 8 Design Documents

| Document | Scope |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System overview, deployment, failure modes |
| [STREAMING_FEATURE_ENGINE.md](docs/STREAMING_FEATURE_ENGINE.md) | O(1) bucketed windows, formulas |
| [SIGNAL_ENGINE.md](docs/SIGNAL_ENGINE.md) | Weighted composite, anti-overfitting |
| [PAPER_EXECUTION_ENGINE.md](docs/PAPER_EXECUTION_ENGINE.md) | Bid/ask fills, position FSM |
| [SMART_EXIT_ENGINE.md](docs/SMART_EXIT_ENGINE.md) | 5-layer model, tier patience |
| [DEMO_EXECUTION_ENGINE.md](docs/DEMO_EXECUTION_ENGINE.md) | Adapter interface, order FSM |
| [ANALYTICS_MONITORING.md](docs/ANALYTICS_MONITORING.md) | Epoch system, alert rules |
| [OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md) | Emergency procedures, secrets |

## Quick Start

### Run Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

### Start Dashboard

```bash
uvicorn cte.dashboard.app:app --host 0.0.0.0 --port 8080
# Open http://localhost:8080
```

### Start Infrastructure (Docker)

```bash
docker compose -f deploy/docker-compose.dev.yml up -d   # PostgreSQL + Redis + Prometheus + Grafana
docker compose -f deploy/docker-compose.yml up -d        # Full stack
```

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Bid/ask fills, never mid-price | Mid-price hides spread cost — compounds to real P&L gap |
| O(1) feature computation | BTC = 100+ trades/sec, array recomputation doesn't scale |
| Weighted composite signals, no ML | Auditable, deterministic, no training data dependency |
| Context dampens only, never amplifies | Whale/news flags gate trades, never trigger them |
| 5-layer exit with tier patience | Tier A gets 15min, Tier C gets 4min — conviction earns patience |
| Event clock, no wall clock | `datetime.now()` breaks replay; event timestamps are deterministic |
| 25 readiness gates before live | Infrastructure (6) + execution parity (10) + edge proof (9) |

## V1 Constraints

| Constraint | Value |
|---|---|
| Symbols | BTCUSDT, ETHUSDT |
| Direction | LONG only |
| Max leverage | 3x |
| Primary venue | Binance USDⓈ-M Futures |
| Secondary venue | Bybit v5 linear |
| Wallet connection | None in v1 |

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Web Framework | FastAPI |
| Async Runtime | asyncio |
| Message Bus | Redis Streams |
| Database | PostgreSQL + TimescaleDB |
| Data Models | Pydantic v2 (frozen, schema-enforced) |
| Logging | structlog (JSON) |
| Metrics | Prometheus / Grafana |
| Dashboard | Tailwind CSS + Chart.js + Alpine.js |

## License

MIT
