# Binance Futures testnet — full trade lifecycle proof

## Status (this environment)

| Step | Result |
|------|--------|
| Signed REST auth (`GET /fapi/v2/balance`) | **Failed** — `Binance API error: -2014 API-key format invalid.` |
| Entry → exit cycle | **Not executed** — blocked on credentials |

The shell environment exposes **placeholder** values (`pilot_validate_key_*`), not keys issued by [Binance USDⓈ-M Futures testnet](https://testnet.binancefuture.com/). **No trades were faked**; the automated proof script exits before starting Uvicorn when auth fails.

## What to run (operator with real testnet keys)

1. Create API key + secret on the **Futures testnet** site (not mainnet, not spot-only).

2. Export credentials and verify:

```bash
export CTE_BINANCE_TESTNET_API_KEY='…'
export CTE_BINANCE_TESTNET_API_SECRET='…'
export CTE_BINANCE_TESTNET_REST_URL='https://testnet.binancefuture.com'

python3 scripts/verify_binance_testnet_auth.py
# Expect: AUTH_OK wallet= … available= …
```

3. Run the end-to-end proof (single symbol **BTCUSDT**, cooldown **0**, short **max hold** for exit, **max order USD** sized for 0.001 BTC lot):

```bash
export CTE_ENGINE_MODE=demo
export CTE_EXECUTION_MODE=testnet
export CTE_DASHBOARD_VENUE_LOOP=1
export CTE_ENGINE_SYMBOLS='["BTCUSDT"]'
export CTE_SIGNALS_COOLDOWN_SECONDS=0
export CTE_EXITS_MAX_HOLD_MINUTES=3
export CTE_SIZING_MAX_ORDER_USD=200
export CTE_DASHBOARD_PAPER_TIER_C_THRESHOLD=0.26
export CTE_DASHBOARD_PAPER_INTERVAL_SEC=1.0

python3 scripts/run_testnet_e2e_proof.py
```

Exit codes: `0` = one entry + one exit observed; `2` = auth failed; `3` = timeout.

Set `E2E_SKIP_START=1` if the dashboard is already running.

## Verification checklist (when keys work)

| Check | Where |
|-------|--------|
| Real market order | `venue_order_metrics.entry_orders_filled` ≥ 1; logs `testnet_place_order_result` |
| `venue_order_id` | `/api/paper/status` → `venue_order_metrics.first_venue_order_id`; open leg → `venue_order_id` |
| Local mirror open | `/api/paper/positions` → `execution_mode=testnet`, `execution_channel=binance_usdm_testnet`, `trade_source=demo_exchange` |
| Close order | Logs `testnet_close_order_result`; `venue_order_metrics.exit_orders_filled` ≥ 1 |
| Journal | `GET /api/analytics/trades` → `source=demo_exchange`, `venue=binance_testnet`, `pnl` |
| Balance | `/api/paper/status` → `venue_balance_usdt` (wallet / available) |
| Reconciliation | `reconciliation.last.status` → `clean` when local qty matches `get_positions` |
| Runner | `runner_class=DashboardTestnetRunner`, `execution_mode=testnet` |

**PnL / prices:** journal rows expose `pnl` (realized); entry/exit **venue** prices are in structured logs (`avg_price` on `testnet_place_order_result` / `testnet_close_order_result`). Extending `CompletedTrade` with entry/exit prices was out of scope for this pass.

## Balance before / after

Capture `venue_balance_usdt` from the first `/api/paper/status` poll after startup (before) and the final status after exit (after), or compare printed JSON from `run_testnet_e2e_proof.py` when it exits `0`.

## Code changes in this iteration

- `execution_mode` / clarified `execution_channel` on testnet status and open-position payloads (`trade_source=demo_exchange` for journal semantics).
- `scripts/verify_binance_testnet_auth.py` — hard gate for real E2E.
- `scripts/run_testnet_e2e_proof.py` — automated poll until `entries_total ≥ 1` and `exits_recorded ≥ 1`.
