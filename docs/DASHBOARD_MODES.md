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

## Verify

```bash
curl -s http://localhost:8080/api/market/health | python -m json.tool
curl -s http://localhost:8080/api/market/tickers | python -m json.tool
```

`source` should be `binance_testnet` when the feed is up.

## Production mainnet

Not supported in v1. `live` is blocked by `enforce_safety`.
