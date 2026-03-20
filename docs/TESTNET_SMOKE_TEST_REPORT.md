# Binance USDⓈ-M testnet — dashboard smoke test report

## Run metadata

| Field | Value |
|-------|--------|
| Report date | 2026-03-20 (UTC) |
| Branch / code | `cursor/proje-al-t-rma-67f9` (venue runner + REST error handling) |
| Environment | `CTE_ENGINE_MODE=demo`, `CTE_EXECUTION_MODE=testnet`, `CTE_DASHBOARD_VENUE_LOOP=1` |
| Symbol under test | `SOLUSDT` only (`CTE_ENGINE_SYMBOLS='["SOLUSDT"]'`) |
| Sizing cap (smoke) | `CTE_SIZING_MAX_ORDER_USD=75` (keeps testnet notional small vs defaults) |
| Exit cap (smoke) | `CTE_EXITS_MAX_HOLD_MINUTES=2` (optional; short max-hold for faster closes when keys work) |
| Signal cooldown (smoke) | `CTE_SIGNALS_COOLDOWN_SECONDS=0` recommended so a failed venue attempt does not dominate with `rejected_cooldown` while debugging |

## Preconditions

1. **Valid Binance USDⓈ-M Futures testnet API key + secret** (from [Binance testnet](https://testnet.binancefuture.com/)). Keys must be accepted by `https://testnet.binancefuture.com` signed REST (not mainnet keys, not malformed strings).
2. **Demo safety path**: `CTE_BINANCE_TESTNET_REST_URL` must **not** be a production Binance futures URL (e.g. `https://fapi.binance.com` is **blocked** in demo mode).
3. **Engine / execution pairing**: `CTE_ENGINE_MODE=demo` with `CTE_EXECUTION_MODE=testnet` (validator rejects `paper` engine + `testnet` execution).

## Verification checklist (observed)

### 1. Startup

| Check | Result |
|-------|--------|
| `GET /api/paper/status` → `runner_class` = `DashboardTestnetRunner` | **Pass** |
| Banner / mode = demo testnet (no live capital) | **Pass** (see `print_startup_banner("demo")` in dashboard lifespan) |
| Production REST URL guard | **Pass** (`validate_environment("demo", binance_rest_url="https://fapi.binance.com", …)` → `binance_production_guard` fails) |

### 2. Live feed + warmup

| Check | Result |
|-------|--------|
| WebSocket tickers updating for configured symbol(s) | **Pass** (`ticks_ok` increases; `GET /api/market/tickers` live) |
| Warmup mids accumulate | **Pass** (`GET /api/paper/warmup` → `mid_count` increases toward `full_mids`) |

### 3. First entry (REST)

**Blocked in this workspace run**: REST calls returned **`-2014 API-key format invalid`** (keys present in shell env were not valid Binance testnet credentials).

Observed API fields when the pipeline reached `place_order`:

| Field | Value |
|-------|--------|
| `venue_order_metrics.entry_orders_sent` | 10 (attempts reached exchange) |
| `venue_order_metrics.entry_orders_filled` | 0 |
| `venue_last_error` | `Binance API error: -2014 API-key format invalid.` |
| `entry_diagnostics.global_counts.rejected_venue_rest` | > 0 |

**Code fix applied**: `ExecutionError` / `OrderRejectedError` from `place_order` / `close_position` are caught inside `DashboardTestnetRunner.tick()` so the loop **does not** abort the whole tick via `run_forever` exception path; errors surface as `rejected_venue_rest` and `venue_last_error` (no silent fallback to paper).

With **valid** keys, expect:

- `testnet_place_order_result` log (JSON) with `venue_order_id`, `status`, `avg_price`
- `GET /api/paper/positions` → `execution_mode=testnet`, `execution_channel=binance_usdm_testnet`, `trade_source=demo_exchange`, `venue_order_id` populated

### 4. Position management (pending valid keys)

Not completed here (no fills). With valid keys + short `max_hold`, expect:

- `testnet_close_order_result` log on exit
- `GET /api/analytics/trades` → `source=demo_exchange`

### 5. Reconciliation + balance

| Check | Result (this run) |
|-------|-------------------|
| `venue_balance_usdt` | Error string mirroring `-2014` (same root cause as orders) |
| `reconciliation.last` | `status: error` with same message (adapter cannot query positions without valid auth) |

With valid keys, expect `status: clean` when local mirror matches `get_positions`, and numeric `wallet` / `available` under `venue_balance_usdt`.

### 6. Edge cases (partial)

| Case | Result |
|------|--------|
| Venue REST failure | **Pass** — recorded as `rejected_venue_rest`, `venue_last_error`, ticks continue |
| Pause / resume | **Pass** — `POST /api/ops/pause`, `POST /api/ops/resume` |
| Symbol disable | **Pass** — `POST /api/ops/symbol/SOLUSDT/disable` |
| Quantity rounding / zero qty | Covered by unit tests (`tests/dashboard/test_testnet_runner.py`); re-verify with live keys |

## How to re-run (operator)

```bash
export CTE_ENGINE_MODE=demo
export CTE_EXECUTION_MODE=testnet
export CTE_DASHBOARD_VENUE_LOOP=1
export CTE_ENGINE_SYMBOLS='["SOLUSDT"]'   # or ["BTCUSDT"] — one symbol for safety
export CTE_BINANCE_TESTNET_API_KEY="…"
export CTE_BINANCE_TESTNET_API_SECRET="…"
export CTE_BINANCE_TESTNET_REST_URL="https://testnet.binancefuture.com"
# Optional smoke tuning:
export CTE_SIGNALS_COOLDOWN_SECONDS=0
export CTE_SIZING_MAX_ORDER_USD=75
export CTE_EXITS_MAX_HOLD_MINUTES=2

python3 -m uvicorn cte.dashboard.app:app --host 127.0.0.1 --port 8080
```

Poll:

```bash
curl -sS http://127.0.0.1:8080/api/paper/status | python3 -m json.tool
curl -sS http://127.0.0.1:8080/api/paper/positions
curl -sS 'http://127.0.0.1:8080/api/analytics/trades?epoch=crypto_v1_demo&limit=20'
```

## Conclusion

- **Runner selection, demo safety banner, and production URL guard** behave as designed.
- **End-to-end fill + close + clean reconciliation** could not be asserted in this environment because **testnet API credentials were rejected by Binance (`-2014`)**. Replace keys with valid futures testnet keys and re-run the same checklist; the dashboard surfaces venue failures explicitly (`venue_last_error`, `rejected_venue_rest`) without falling back to paper execution.
