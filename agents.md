# Crypto Trading Engine – Agent Rules

## Agent Identity
You are a senior systems engineer building a production-grade crypto trading engine.
You are NOT building a toy, a tutorial, or a proof-of-concept.
Every decision must be justified by production constraints.

## Core Principles

### 1. Safety First
- This system handles financial decisions. Bugs cost real money.
- Default to rejecting trades, not accepting them.
- Risk manager always wins over signal engine.
- When in doubt, do nothing (flat is a position too).

### 2. Simplicity Over Cleverness
- No premature optimization.
- No over-engineering for "future" features.
- V1 does two symbols, one direction, two venues. That's it.
- If a module isn't needed for the current phase, don't build it.

### 3. Observability Over Debugging
- If you can't measure it, don't ship it.
- Every decision path must emit structured logs and metrics.
- Every trade must carry a full reason chain from signal to execution.

### 4. Determinism Over Magic
- Given the same inputs, the engine must produce the same outputs.
- No hidden randomness in signal generation.
- Feature calculations must be reproducible from stored market data.

## Module Responsibilities

### Market Data Connectors (`src/cte/connectors/`)
- Connect to Binance USDⓈ-M Futures WebSocket and Bybit v5 public WebSocket.
- Maintain persistent connections with automatic reconnection.
- Emit raw events to Redis Streams.
- Do NOT process, filter, or transform data. That's the normalizer's job.
- Track connection state, message rates, and latency.

### Canonical Event Normalizer (`src/cte/normalizer/`)
- Consume raw events from connectors.
- Transform into canonical CTE event format (venue-agnostic).
- Validate schema, reject malformed data.
- Emit normalized events to Redis Streams.

### Feature Engine (`src/cte/features/`)
- Consume normalized market data events.
- Calculate technical indicators (RSI, EMA, VWAP, volume profile).
- Maintain rolling windows in memory, persist snapshots to TimescaleDB.
- Emit feature vectors to Redis Streams.
- Must be stateless-restartable: rebuild state from DB on startup.

### Signal Engine (`src/cte/signals/`)
- Consume feature vectors.
- Apply signal logic (rule-based in v1, ML-ready interface).
- Every signal includes confidence score and reason payload.
- Emit signal events to Redis Streams.
- Never emit execution commands directly.

### Risk Manager (`src/cte/risk/`)
- Intercept all signals before execution.
- Apply position limits, drawdown checks, exposure limits, correlation checks.
- Has absolute veto power.
- Emit approved/rejected decisions with reasons.

### Tiering & Sizing (`src/cte/sizing/`)
- Determine position size based on signal confidence, risk budget, and portfolio state.
- Kelly criterion or fixed-fraction in v1.
- Never exceed configured max position size.

### Execution Engine (`src/cte/execution/`)
- v1: Paper execution only (simulated fills).
- v2: Binance testnet execution.
- v3: Live execution with circuit breakers.
- Track order lifecycle: created → submitted → partial → filled → cancelled.
- Emit execution events to Redis Streams.

### Smart Exit Engine (`src/cte/exits/`)
- Monitor open positions for exit conditions.
- Trailing stops, time-based exits, target hits, invalidation exits.
- Every exit carries a reason (stop_loss, take_profit, trailing, timeout, invalidation).
- Coordinate with risk manager for emergency exits.

### Analytics (`src/cte/analytics/`)
- Consume all events for post-trade analysis.
- Calculate PnL, Sharpe, win rate, drawdown curves.
- Store aggregated metrics in PostgreSQL.
- Serve dashboards via FastAPI endpoints.

### Monitoring (`src/cte/monitoring/`)
- Prometheus metrics exporter.
- Health check aggregator.
- Alert rule definitions.
- Grafana dashboard definitions as code.

## Event Flow (Happy Path)
```
Binance WS → Raw Event → Redis → Normalizer → Canonical Event → Redis
                                                      ↓
Bybit WS → Raw Event → Redis → Normalizer → ─────────┘
                                                      ↓
                                              Feature Engine → Feature Vector → Redis
                                                      ↓
                                              Signal Engine → Signal Event → Redis
                                                      ↓
                                              Risk Manager → Approved/Rejected → Redis
                                                      ↓
                                              Sizing → Sized Order → Redis
                                                      ↓
                                              Execution → Fill Event → Redis
                                                      ↓
                                              Exit Engine (monitors position)
                                                      ↓
                                              Analytics (records everything)
```

## What NOT To Do
- Do NOT build a generic "trading framework". Build THIS specific engine.
- Do NOT add symbols beyond BTCUSDT/ETHUSDT in v1.
- Do NOT implement short selling in v1.
- Do NOT connect real wallets in v1.
- Do NOT use LLM/AI for trade decisions in v1.
- Do NOT build a web UI in v1 (API + Grafana dashboards only).
- Do NOT over-abstract. If there are only 2 venues, you don't need a plugin system.
- Do NOT use ORM. Use raw SQL with asyncpg for performance.
- Do NOT store secrets in code or config files.

## Testing Strategy
- Unit tests: All pure logic (features, signals, risk rules, sizing).
- Integration tests: Redis Stream producers/consumers, DB read/write.
- Replay tests: Feed historical data, verify deterministic outputs.
- Paper trading validation: Run paper engine against live data for 7 days before phase advancement.

## Configuration Hierarchy
1. Environment variables (highest priority)
2. `.env.{environment}` files
3. `config/defaults.toml` (lowest priority)

Never use YAML for configuration. TOML for static config, env vars for runtime.

## Cursor Cloud specific instructions

### Services overview
- **CTE (Crypto Trading Engine)**: Python 3.12 FastAPI backend with 10 microservices communicating via Redis Streams, persisting to PostgreSQL+TimescaleDB. Currently in Phase 0 (skeleton).

### Infrastructure
- **PostgreSQL+TimescaleDB** and **Redis** run via `docker compose -f deploy/docker-compose.dev.yml up -d postgres redis`. Docker must be running first (`sudo dockerd` if not started).
- The TimescaleDB auto-tune script may panic on first start in constrained environments — just restart the container and it works on the second attempt.
- Database migrations: `python3 scripts/migrate.py --dsn "postgresql://cte:cte_dev@localhost:5432/cte" --seed`
- The `.env` file must set `CTE_DB_PASSWORD=cte_dev` to match the docker-compose dev password.

### Key commands
- **Lint**: `python3 -m ruff check src/ tests/` (94 pre-existing warnings in Phase 0 skeleton)
- **Tests**: `python3 -m pytest tests/ -v` (60 tests, all use fakeredis/AsyncMock — no real infra needed)
- **Coverage**: `python3 -m pytest tests/ --cov=src/cte --cov-report=term-missing` (coverage threshold is 80% but Phase 0 skeleton is ~33%)
- **Config validate**: `python3 -m cte.core.cli validate`
- **Run a service**: `python3 -c "from cte.api.app import create_app; import uvicorn; uvicorn.run(create_app('health-check'), host='0.0.0.0', port=8000)"`
- See `README.md` "Quick Start" and `pyproject.toml` `[tool.*]` sections for full details.

### Gotchas
- `ruff` and `pytest` must be invoked via `python3 -m ruff` / `python3 -m pytest` (not bare commands) because pip installs to `~/.local/bin` which may not be on `PATH`.
- Unit tests do NOT require running infrastructure (Redis/Postgres). They use `fakeredis` and `AsyncMock`.
- The `pyproject.toml` coverage threshold (`fail_under = 80`) will cause `pytest --cov` to return exit code 1 even though all tests pass — this is expected at Phase 0.
