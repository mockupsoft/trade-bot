# CTE — Crypto Trading Engine

[![CI](https://github.com/mockupsoft/trade-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/mockupsoft/trade-bot/actions/workflows/ci.yml)
![Tests](https://img.shields.io/badge/tests-498%20passing-brightgreen)
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

### 498 Tests (28+ test files)

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
| [DASHBOARD_MODES.md](docs/DASHBOARD_MODES.md) | **seed / paper / demo** dashboard + Docker `CTE_DASHBOARD_MODE` |

## Quick Start

### Run Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

### Start Dashboard (Binance futures **testnet** only)

Requires [testnet API keys](https://testnet.binancefuture.com). No seed / fake trade injection.

```bash
pip install -e .
# Prefer repo-root .env (see .env.example) with CTE_BINANCE_TESTNET_* keys.
./scripts/start_dashboard.sh
# Or foreground: CTE_ENGINE_MODE=demo cte-dashboard
```

→ **http://localhost:8080** (listens on all interfaces; dual-stack IPv4+IPv6 so `localhost` → `::1` works). Market stream defaults to `wss://stream.binancefuture.com/stream`.

**Local `.env` (recommended):** copy `.env.example` → `.env` in the **repo root** and set `CTE_BINANCE_TESTNET_*` there. The dashboard loads it on startup (and it overrides stale exports in your shell). **Do not commit `.env`** — it is gitignored. **Do not put API secrets in GitHub** (use [Actions secrets](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions) for CI only).

**If http://localhost:8080 does not load:** the server must be running (`python -m cte.dashboard` or `cte-dashboard` from repo root). Check port: `ss -tlnp | grep 8080` or `lsof -i :8080` — stop any other process on 8080. Verify: `curl -s http://127.0.0.1:8080/api/dashboard/meta` should return JSON with `"service": "cte.dashboard"`.

**Docker** (`analytics` on **8080**) — compose sets `CTE_ENGINE_MODE=demo`; pass the same env vars or `.env`:

```bash
export CTE_BINANCE_TESTNET_API_KEY="..."
export CTE_BINANCE_TESTNET_API_SECRET="..."
docker compose -f deploy/docker-compose.yml up -d analytics
```

Verify: `curl -s http://localhost:8080/api/market/health | python -m json.tool`

Full notes: [docs/DASHBOARD_MODES.md](docs/DASHBOARD_MODES.md).

**Dashboard paper warmup:** the in-process loop uses **staged warmup**. The signal warmup gate clears after `CTE_DASHBOARD_PAPER_WARMUP_MIDS_EARLY` rolling mids (default 20); **full** confidence uses `CTE_DASHBOARD_PAPER_WARMUP_MIDS_FULL` (default 36). Entries opened before full use a reduced notional (`CTE_DASHBOARD_PAPER_EARLY_SIZE_MULT`) and are labeled `warmup_phase=early` in positions and analytics. Tune loop cadence with `CTE_DASHBOARD_PAPER_INTERVAL_SEC`. See `.env.example` and `/api/paper/warmup` / `/api/paper/entry-diagnostics`.

### Validation Campaign (Real Data)

End-to-end checks use **live WebSocket** prices. By default the dashboard runs **paper** execution (`source=paper_simulated`). **No seed trades.** With `CTE_ENGINE_MODE=demo`, `CTE_EXECUTION_MODE=testnet`, and `CTE_DASHBOARD_VENUE_LOOP=1`, the dashboard can place **real Binance USDⓈ-M testnet REST orders** (`source=demo_exchange`); testnet keys still satisfy the demo **safety gate** (no production URLs).

| Item | Command / artifact |
|------|---------------------|
| Full report | [docs/VALIDATION_CAMPAIGN_REPORT.md](docs/VALIDATION_CAMPAIGN_REPORT.md) |
| Testnet venue smoke (REST orders) | [docs/TESTNET_SMOKE_TEST_REPORT.md](docs/TESTNET_SMOKE_TEST_REPORT.md) |
| Testnet full lifecycle proof | [docs/TESTNET_E2E_PROOF_REPORT.md](docs/TESTNET_E2E_PROOF_REPORT.md) |
| Snapshot bundle | `./scripts/collect_validation_snapshot.sh` (set `BASE_URL` if needed) |
| Hourly metrics | `POST /api/campaign/snapshot?period=hourly` |
| Long run | Keep `uvicorn` or `cte-dashboard` up ≥24h on a server; aggregate snapshots |

**Pilot (automated check, ~45s fresh process):** feed connected, `reconnect_count=0`, BTC/ETH tickers live, ops pause/resume OK. **Closed trades in a fresh process** are zero until warmup + signals run; **longer** sessions produce real journal rows in `/api/analytics/trades`.

**Readiness:** **GO** for “live data + paper pipeline + analytics” on the dashboard. **NO-GO** for a full 7-day production promotion gate from pilot data alone — see report.

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

## Directional Support Matrix

| Venue / Mode | Verified | Notes |
|---|---|---|
| Paper Mode | **Yes** | Fully verifies LONG/SHORT metrics inverted calculations |
| Binance Testnet | **Yes** | Open/close LONG and SHORT REST behaviors validated |
| Bybit Demo | **Yes** | Explicit isolated LONG/SHORT endpoints checked via REST APIs |

## V1 Constraints

| Constraint | Value |
|---|---|
| Symbols | BTCUSDT, ETHUSDT |
| Direction | LONG + SHORT (Bi-directional validated on testnet) |
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
