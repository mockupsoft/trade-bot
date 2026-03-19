# CTE Dashboard — Engine modes (seed / paper / demo)

The FastAPI dashboard (`cte.dashboard.app`) is controlled by **`CTE_ENGINE_MODE`** (or **`CTE_DASHBOARD_MODE`** in Docker Compose for the `analytics` service only).

| Mode | Market WebSocket | Analytics trades | Exchange orders | Typical use |
|------|------------------|------------------|-----------------|-------------|
| **`seed`** | No | Injected fake trades (~50) | None | UI demos, CI, offline |
| **`paper`** | Yes — Binance USDⓈ-M **public** combined stream | Empty until your pipeline writes real closes | **None** (simulated fills elsewhere) | Live **prices** + paper PnL story |
| **`demo`** | Same as paper | Same | **Testnet** REST keys required | Binance **Futures testnet** orders only |

**V1 rule:** No live wallet / production trading in this repo phase. `live` mode is blocked at startup.

---

## 1. `seed` — UI preview

- **No** `MarketDataFeed`; dashboard shows **NO FEED**.
- `inject_seed_data()` fills analytics so KPIs and tables look populated.
- **Data is not real market or exchange activity.**

```bash
CTE_ENGINE_MODE=seed cte-dashboard
# http://localhost:8080
```

**Docker:**

```bash
export CTE_DASHBOARD_MODE=seed
docker compose -f deploy/docker-compose.yml up -d analytics
```

---

## 2. `paper` — live ticker, no orders from dashboard

- Starts **`MarketDataFeed`** → connects to **`wss://fstream.binance.com/stream`** (default) for BTCUSDT + ETHUSDT trades, depth, mark price.
- **Read-only public data** — no API keys required for the feed.
- Analytics starts **empty** (no seed trades) unless other services record exits into the same process (single-process `cte-dashboard` only sees its own in-memory engine).

**Requirements:**

- Outbound **HTTPS + WSS** to Binance (corporate firewalls may block `fstream.binance.com`).
- After a few seconds, **Market Feeds** should show **FEED LIVE** and non-stale ages.

```bash
CTE_ENGINE_MODE=paper cte-dashboard
```

**Optional — custom WebSocket URL** (proxy, testing):

```bash
export CTE_MARKET_WS_URL="wss://fstream.binance.com/stream"
CTE_ENGINE_MODE=paper cte-dashboard
```

Empty or unset `CTE_MARKET_WS_URL` → default combined stream URL in code.

**Docker (default for `analytics` since `CTE_DASHBOARD_MODE` defaults to `paper` in `deploy/docker-compose.yml`):**

```bash
docker compose -f deploy/docker-compose.yml up -d analytics
```

Verify:

```bash
curl -s http://localhost:8080/api/market/health | python -m json.tool
curl -s http://localhost:8080/api/market/tickers | python -m json.tool
```

---

## 3. `demo` — testnet + safety gate

- Same **live market** feed as `paper` (public Binance futures WS).
- Activates epoch **`crypto_v1_demo`**.
- **`enforce_safety("demo", ...)`** runs at startup:
  - Binance REST URL must **not** be production.
  - **`CTE_BINANCE_TESTNET_API_KEY`** and **`CTE_BINANCE_TESTNET_API_SECRET`** must be **non-empty** or the process **exits with code 1**.

Get keys: [Binance Futures Testnet](https://testnet.binancefuture.com).

```bash
export CTE_BINANCE_TESTNET_API_KEY="..."
export CTE_BINANCE_TESTNET_API_SECRET="..."
export CTE_BINANCE_TESTNET_REST_URL="https://testnet.binancefuture.com"   # default if omitted
CTE_ENGINE_MODE=demo cte-dashboard
```

**Docker:**

```bash
export CTE_DASHBOARD_MODE=demo
export CTE_BINANCE_TESTNET_API_KEY="..."
export CTE_BINANCE_TESTNET_API_SECRET="..."
docker compose -f deploy/docker-compose.yml up -d analytics
```

If keys are missing, check container logs for `SAFETY BLOCK` / `api_keys_required`.

---

## 4. Docker Compose reference

In **`deploy/docker-compose.yml`**, the **`analytics`** service sets:

```yaml
CTE_ENGINE_MODE: "${CTE_DASHBOARD_MODE:-paper}"
```

So:

| Goal | Set before `docker compose up` |
|------|--------------------------------|
| Offline UI with charts | `CTE_DASHBOARD_MODE=seed` |
| Live ticker (default) | unset, or `CTE_DASHBOARD_MODE=paper` |
| Testnet-ready dashboard | `CTE_DASHBOARD_MODE=demo` + testnet keys |

Other services still use the shared anchor `CTE_ENGINE_MODE: paper` from `x-cte-env`; only the dashboard container reads `CTE_DASHBOARD_MODE` for its **process** mode.

Healthcheck **`start_period: 45s`** allows time for the WebSocket handshake on first boot.

---

## 5. What is still “not real money”

- **`paper`**: Public market data is **real**; any PnL in this dashboard process is only from data you inject or from integrated services — not automatic live fills in this container alone.
- **`demo`**: **Testnet balances only** — never production futures keys or production REST URLs.
- **`seed`**: Entirely synthetic analytics.

For end-to-end paper **execution** (signals → risk → paper engine), run the full stack and ensure events flow into analytics; the dashboard mode only defines **this** service’s feed and seed behaviour.
