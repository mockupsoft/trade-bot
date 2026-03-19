# CTE — Testnet-only dashboard

V1 targets **Binance USDⓈ-M futures testnet** end-to-end for operator UI and public market data.

## What runs

| Layer | Behaviour |
|-------|-------------|
| **Dashboard process** | Always `SystemMode.DEMO`: `enforce_safety` requires `CTE_BINANCE_TESTNET_API_KEY` / `CTE_BINANCE_TESTNET_API_SECRET`. No `inject_seed_data`. |
| **Epoch** | Single active epoch: `crypto_v1_demo`. |
| **Market WebSocket** | Defaults to `wss://stream.binancefuture.com/stream` (`BinanceSettings.ws_combined_url` / `CTE_BINANCE_WS_COMBINED_URL`). |
| **Other Python services** | Default `CTE_ENGINE_MODE=paper`, `CTE_EXECUTION_MODE=paper` — simulated fills while consuming **testnet-priced** streams from settings. |

## Local

```bash
export CTE_BINANCE_TESTNET_API_KEY="..."
export CTE_BINANCE_TESTNET_API_SECRET="..."
CTE_ENGINE_MODE=demo cte-dashboard
```

(`CTE_ENGINE_MODE` is forced to `demo` inside the dashboard process for settings consistency.)

## Docker

`deploy/docker-compose.yml` sets `CTE_ENGINE_MODE=demo` **only** on the `analytics` service; the rest of the stack stays `paper`. You **must** provide testnet keys in the shell environment or `.env` before `docker compose up`, or the dashboard container exits on the safety gate.

```bash
export CTE_BINANCE_TESTNET_API_KEY="..."
export CTE_BINANCE_TESTNET_API_SECRET="..."
docker compose -f deploy/docker-compose.yml up -d analytics
```

## Config page

- `GET /api/config` returns grouped **sections** (runtime, universe, Binance URLs, execution, exits, risk, signals, infrastructure). **No secrets**: Redis URL passwords are redacted; testnet keys are only `configured` / `missing`.
- UI: **Refresh** re-fetches; **Copy JSON** copies the snapshot for tickets/CI. Changing values requires `.env` / `defaults.toml` and a process restart.

## Readiness page

- **Paper / validation → Testnet (v1)** (`GET /api/readiness/paper_to_demo`): scores **8 gates**. Keys + WebSocket + “not LIVE” are measured from this process; paper days, crash-free streak, pytest attestation, and FSM violation count use env vars (`CTE_READINESS_*` in `.env.example`).
- **Phase 5 → Live** (`GET /api/readiness/demo_to_live`): all gates **SKIP** — live mainnet is out of v1 scope (`enforce_safety`). The UI lists them as a future checklist only.

## Verify

```bash
curl -s http://localhost:8080/api/dashboard/meta | python -m json.tool
curl -s http://localhost:8080/api/market/health | python -m json.tool
curl -s http://localhost:8080/api/market/tickers | python -m json.tool
```

- `meta` must include `"service": "cte.dashboard"` and `"market_profile": "binance_usdm_testnet"`. If you get **404**, another app owns port **8080** (stop it).
- `health` must show `"mode": "testnet"` (not `seed`). If you see `seed` or empty `tickers`, the process is **not** this dashboard build — kill whatever is bound to 8080 and start `python -m cte.dashboard` from the repo root (or recreate the `analytics` container).
- `tickers` `source` should be `binance_testnet` when the WebSocket feed is up.

## Troubleshooting (Market feeds empty / OFFLINE)

1. **Port conflict:** `lsof -i :8080` or `ss -tlnp | grep 8080` — only one listener. Stop stray `python`/`uvicorn` jobs or Docker services using 8080.
2. **Local run:** use repo root so `.env` loads (`python -m cte.dashboard`); keys live in `.env` (`CTE_ENGINE_MODE=demo`, testnet key/secret).
3. **Docker:** `analytics` uses `env_file: ../.env` — place keys in the repository root `.env` next to `deploy/`, then `docker compose -f deploy/docker-compose.yml up -d --force-recreate analytics`.

## Production mainnet

Not supported in v1. `live` is blocked by `enforce_safety`.
